# -*- coding: utf-8 -*-
"""
DSH Origin 画图插件 —— 核心引擎
================================

基于 originpro（OriginLab 官方 Python 包，内部经 comtypes/COM 连接 Origin
自动化服务器）。提供连接、写数、画图、导出、并发加锁与错误处理。

并发/加锁策略（多 DSH 会话并发调用时的稳定性）：
1. Origin 是"单实例 COM 自动化服务器"：本机只有一个 Origin64.exe 进程，
   所有 Python 脚本通过 COM 连到同一个实例；Origin 内部状态（活动页/活动
   工作表）是全局共享的。
2. 引擎采用"专用 COM 线程"模型：所有 Origin 操作投递到唯一的工作线程
   串行执行（任务队列）。这同时解决两个问题：
   a) comtypes/OriginExt 的 COM 接口指针有线程亲和性——跨线程调用会报
      "对象没有连接到服务器"；单线程模型彻底规避；
   b) 多会话并发天然串行化：任意时刻只有一个操作触碰 Origin。
3. 每次操作使用唯一命名空间（DSH_<8位hex>），不同调用互不踩踏数据。
4. 可选跨进程锁（Windows 命名互斥体，环境变量 ORIGIN_IPC_LOCK=1 开启）：
   覆盖"多个 DSH 实例/多个 profile 同时操作同一 Origin"的极端场景。
   互斥体在 COM 线程内、执行操作前获取。
5. 出错永不崩溃：所有公开函数返回结构化 dict，异常被转换为
   {"ok": false, "error": ...}，并保证队列/锁状态一致。
"""
import os
import queue
import re
import sys
import threading
import time
import traceback
import uuid

import origin_errors as oerr
import origin_analysis as oana
import plot_style as pst

# ---------------------------------------------------------------------------
# 常量与全局
# ---------------------------------------------------------------------------
DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "dsch_origin_plugin", "output")

_com_queue = queue.Queue()        # 任务队列：所有 Origin 操作在此串行执行
_com_thread = None                # 专用 COM 线程
_ipc_lock = None                  # 可选跨进程锁（Windows 命名互斥体）
_origin_app = None                # 惰性连接的 originpro 句柄（仅 COM 线程访问）
_connected = False

# 画图类型 -> originpro add_plot type 参数（2D 基础）
PLOT_TYPES = {
    "line": "l",          # 折线
    "scatter": "s",       # 散点
    "line_symbol": "y",   # 线+符号
    "column": "c",        # 柱状
}

# 说明：box/bar 均改走 Origin 官方模板（"box"/"bar"），不再依赖 plotxy 代码
# （plotxy 的 204/215 在部分 Origin 2026b 会渲染成面积图或不出图，真机验证）。
PLOT_XY_CODES = {}

# 等高线/3D matrix 图型 -> add_mplot type
MATRIX_PLOT_TYPES = {
    "contour": 104,       # 等高线
    "contour_fill": 105,  # 填充等高线
    "3d_wire": 106,       # 3D 线框
    "3d_surface": 103,    # 3D 表面（GLparafunc）
}

PLOT_TYPES_CN = {
    "line": "折线", "scatter": "散点", "line_symbol": "线+符号", "column": "柱状",
    "histogram": "直方图", "box": "箱线图", "bar": "条形图",
    "contour": "等高线", "contour_fill": "填充等高线", "3d_wire": "3D线框",
    "3d_surface": "3D表面",
}


# ---------------------------------------------------------------------------
# 专用 COM 线程调度
# ---------------------------------------------------------------------------
class _TaskResult:
    """单次任务的结果槽：主线程等待、COM 线程填充。"""

    def __init__(self):
        self._evt = threading.Event()
        self.value = None
        self.exc = None

    def set_result(self, v):
        self.value = v
        self._evt.set()

    def set_exception(self, e):
        self.exc = e
        self._evt.set()

    def wait(self):
        self._evt.wait()
        if self.exc is not None:
            raise self.exc
        return self.value


def _com_thread_loop():
    """专用 COM 线程主循环：串行执行所有 Origin 任务。"""
    while True:
        task = _com_queue.get()
        if task is None:
            return
        fn, args, kwargs, done = task
        try:
            if _ipc_lock is not None:
                with _ipc_lock:
                    done.set_result(fn(*args, **kwargs))
            else:
                done.set_result(fn(*args, **kwargs))
        except Exception as e:
            done.set_exception(e)


def _run_on_com_thread(fn, *args, **kwargs):
    """把 fn 投递到专用 COM 线程执行并等待结果（线程安全，可被并发调用）。"""
    global _com_thread
    if _com_thread is None or not _com_thread.is_alive():
        t = threading.Thread(target=_com_thread_loop, name="origin-com", daemon=True)
        t.start()
        _com_thread = t
    done = _TaskResult()
    _com_queue.put((fn, args, kwargs, done))
    return done.wait()


def _configure_ipc_lock_if_requested():
    """按环境变量 ORIGIN_IPC_LOCK 启用跨进程命名互斥体锁（Windows）。"""
    global _ipc_lock
    if _ipc_lock is not None:
        return
    if os.environ.get("ORIGIN_IPC_LOCK", "").lower() in ("1", "true", "yes"):
        try:
            _ipc_lock = _WindowsNamedMutex("dsh_origin_plugin_mutex")
        except Exception as e:  # 失败不影响主流程，仅降级为单 COM 线程模型
            _ipc_lock = None
            sys.stderr.write(f"[origin_engine] 跨进程锁初始化失败，降级: {e}\n")


class _WindowsNamedMutex:
    """Windows 命名互斥体（跨进程互斥）。"""

    def __init__(self, name):
        import ctypes
        self._kt = ctypes.windll.kernel32
        self._handle = self._kt.CreateMutexW(None, False, name)
        if not self._handle:
            raise ctypes.WinError()
        self._INFINITE = 0xFFFFFFFF

    def __enter__(self):
        res = self._kt.WaitForSingleObject(self._handle, self._INFINITE)
        if res != 0:  # WAIT_OBJECT_0 = 0
            raise RuntimeError(f"跨进程锁等待失败 (WaitForSingleObject={res})")

    def __exit__(self, *exc):
        self._kt.ReleaseMutex(self._handle)
        return False


def _synchronized(fn):
    """装饰器：公开函数 -> 投递到专用 COM 线程串行执行。

    边界兜底：若 impl 返回旧式 {"ok": false, ...}，升级为统一错误码结构
    （error_code / recoverable / next_actions），保证 MCP 面格式始终一致。
    """
    def wrapper(*args, **kwargs):
        _configure_ipc_lock_if_requested()
        result = _run_on_com_thread(fn, *args, **kwargs)
        if isinstance(result, dict) and not result.get("ok") and "error_code" not in result:
            return oerr.upgrade_legacy_failure(result)
        return result
    return wrapper


# ---------------------------------------------------------------------------
# 连接（以下 *_impl 函数只在 COM 线程内执行）
# ---------------------------------------------------------------------------
def _origin_running():
    """探测 Origin 主进程是否在运行（仅提示用，不阻塞）。"""
    try:
        out = os.popen('tasklist /FI "IMAGENAME eq Origin64.exe" /NH 2>nul').read()
        return "Origin64.exe" in out
    except Exception:
        return None


def _origin_proc_count():
    """统计 Origin64 进程数（>1 说明存在多实例，需清理以免 COM 连错实例）。"""
    try:
        out = os.popen('tasklist /FI "IMAGENAME eq Origin64.exe" /NH 2>nul').read()
        return sum(1 for line in out.splitlines() if "Origin64.exe" in line)
    except Exception:
        return None


def _connect_impl():
    global _connected, _origin_app
    if _connected and _origin_app is not None:
        return True, _describe_impl()
    try:
        import originpro as op
        # 单实例语义加固：originpro 首次访问默认走 Origin.Application（可能启动
        # 新实例；多实例时 COM 会连错导致 LT_execute 异常）。这里在首次访问前
        # 强制 Attach（OriginExt.ApplicationSI）复用已运行的 Origin 主实例。
        try:
            from originpro import config as _opconfig
            _opconfig.po.Attach()
        except Exception:
            pass
        started = time.time()
        user_files = op.path()          # 触发 COM 连接（OriginExt.ApplicationSI）
        elapsed = time.time() - started
        _origin_app = op
        _connected = True
        nproc = _origin_proc_count()
        info = {
            "ok": True,
            "connected": True,
            "user_files": user_files,
            "connect_ms": int(elapsed * 1000),
            "origin_running_before": _origin_running(),
            "detail": "已连接 Origin COM 自动化服务器",
        }
        if nproc and nproc > 1:
            info["warning"] = (
                f"检测到 {nproc} 个 Origin64 进程（正常应为 1）。多实例会导致 COM "
                "连接异常（如 LT_execute 报错）。请关闭多余 Origin 窗口，仅保留主实例。"
            )
        return True, info
    except Exception as e:
        nproc = _origin_proc_count()
        extra = ""
        if nproc and nproc > 1:
            extra = (f" 当前有 {nproc} 个 Origin64 进程（多实例冲突常见原因），"
                     "请关闭多余的 Origin 窗口只保留一个主实例后重试。")
        return False, {
            "ok": False,
            "connected": False,
            "error": str(e) + extra,
            "origin_running": _origin_running(),
            "hint": (
                "无法连接 Origin。请检查："
                "1) 是否已安装 Origin（C:\\Program Files\\OriginLab\\Origin2026b\\Origin64.exe）；"
                "2) 是否已打开 Origin（或允许脚本自动启动它）；"
                "3) Origin 是否以管理员权限运行而脚本不是（COM 权限不匹配）；"
                "4) 首次使用需等待 Origin 完成启动（最多约45秒）。"
            ),
        }


def _describe_impl():
    op = _origin_app
    info = {
        "ok": True,
        "connected": _connected,
        "user_files": op.path() if op else None,
        "origin_exe": "C:\\Program Files\\OriginLab\\Origin2026b\\Origin64.exe",
        "lock": "专用COM线程(串行)" + (" + 跨进程命名互斥体" if _ipc_lock else ""),
    }
    return info


# ---------------------------------------------------------------------------
# 数据写入
# ---------------------------------------------------------------------------
def _new_unique_name(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _page_names():
    """当前项目所有页面短名集合（用于 plotxy 页面差集检测）。"""
    op = _origin_app
    try:
        return {str(op.po.Pages(i).GetName()) for i in range(op.po.Pages.Count)}
    except Exception:
        return set()


def _plotxy_new_page(script, before):
    """执行 LabTalk plotxy，用页面名差集返回新建的图页短名。

    不依赖 find_graph()（其返回最近图页有歧义），因此更健壮：
    优先选非 Book 的图页，避免把数据工作簿当成图。
    """
    op = _origin_app
    op.po.LT_execute(script)
    after = _page_names()
    newp = after - before
    if not newp:
        return None
    cands = [n for n in newp if not n.lower().startswith("book")]
    return (cands or sorted(newp))[0]


def _delete_graph_page(graph_name):
    """按短名删除图页（幂等命名用）。返回是否已删除。"""
    op = _origin_app
    try:
        gp = op.find_graph(graph_name)
        if gp is not None:
            gp.destroy()
            return True
    except Exception:
        pass
    # 兜底：LabTalk 直接删页
    try:
        op.po.LT_execute(f"page -d {graph_name};")
        return True
    except Exception:
        return False


def _ensure_graph_name(graph_name, title):
    """幂等命名：若已有同名图页，先删旧再新建，图名保持稳定，不产生 Graph2/3。

    返回用于 new_graph 的 lname。
    """
    if graph_name:
        _delete_graph_page(graph_name)
        return str(graph_name)
    return title or ""


def _write_data_impl(columns, worksheet=None, book_name=None, sheet_name=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app

        data = _normalize_columns(columns)
        if data is None:
            return {"ok": False, "error": "columns 参数必须是 dict{列名: 列表} 或 二维列表"}
        if not data:
            return {"ok": False, "error": "columns 为空"}

        if worksheet:
            wks = op.find_sheet("w", worksheet)
            if not wks:
                return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        else:
            wks = op.new_sheet("w", sheet_name or _new_unique_name("DSHData"))
            if not wks:
                return {"ok": False, "error": "新建工作表失败"}

        rows = max((len(v) for v in data.values()), default=0)
        col_names = list(data.keys())
        for i, cname in enumerate(col_names):
            vals = data[cname]
            axis = "X" if i == 0 else "Y"
            wks.from_list(i, vals, lname=cname, axis=axis)
        return {
            "ok": True,
            "worksheet": str(wks),
            "book": book_name,
            "columns": col_names,
            "rows": rows,
            "detail": f"已写入 {len(col_names)} 列 x {rows} 行到 {wks}",
        }
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _normalize_columns(columns):
    """把 dict{列名: 列表} / 二维列表 / 一维列表 统一为 dict{列名: 列表}。"""
    if isinstance(columns, dict):
        out = {}
        for k, v in columns.items():
            if isinstance(v, (list, tuple)):
                out[str(k)] = [float(x) if isinstance(x, (int, float)) else x for x in v]
            else:
                return None
        return out
    if isinstance(columns, (list, tuple)):
        rows = list(columns)
        if not rows:
            return {}
        if all(isinstance(r, (list, tuple)) for r in rows):
            ncol = max(len(r) for r in rows)
            return {f"C{i+1}": [r[i] if i < len(r) else None for r in rows] for i in range(ncol)}
        if all(isinstance(r, (int, float)) for r in rows):
            return {"Y": [float(x) for x in rows]}
    return None


# ---------------------------------------------------------------------------
# 画图
# ---------------------------------------------------------------------------
def _plot_impl(worksheet, y_columns=None, x_column=None, plot_type="line",
               graph_name=None, title=None, yerr_column=None,
               style_mode=None, family=None):
    """画图（支持幂等命名 + 可选样式应用）。返回包含 style 建议。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app

        plot_type = (plot_type or "line").lower()
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return oerr.fail("worksheet_not_found", f"工作表不存在: {worksheet}", worksheet=worksheet)

        valid_kinds = list(PLOT_TYPES) + ["histogram", "box", "bar"]
        if plot_type not in valid_kinds:
            return oerr.fail("invalid_request",
                             f"plot_type 必须是 {valid_kinds} 之一，收到 {plot_type!r}",
                             valid_kinds=valid_kinds, received=plot_type)

        # 幂等命名：同名图页先删旧再新建
        lname = _ensure_graph_name(graph_name, title or "")

        # 特殊图型：直方图（numpy 分箱 + 柱状图，不依赖 plotxy，稳定可控）
        if plot_type == "histogram":
            import numpy as np
            col = 0
            if y_columns:
                c = y_columns[0]
                ci2 = _col_index_impl(wks, c)
                col = ci2 if ci2 is not None else 0
            v = np.asarray(wks.to_list(col), dtype=float)
            v = v[np.isfinite(v)]
            if v.size == 0:
                return oerr.fail("empty_data", "直方图数据为空", column=str(col))
            bins = 10
            counts, edges = np.histogram(v, bins=bins)
            centers = (edges[:-1] + edges[1:]) / 2
            hs = op.new_sheet("w", "HistData")
            hs.from_list(0, list(centers), lname="bin_center")
            hs.from_list(1, list(counts), lname="count")
            gp = op.new_graph(lname=lname or "Histogram")
            gl = gp[0]
            gl.add_plot(hs, 1, 0, type="c")
            gl.rescale()
            short_name = gp.obj.GetName()
            style = _apply_style_impl(short_name, plot_type="histogram",
                                      columns=["count"], style_mode=style_mode,
                                      family=family, x_title="Bin center")
            return {
                "ok": True,
                "graph": short_name,
                "graph_short": short_name,
                "plot_type": "histogram",
                "y_columns": [str(col)],
                "x_column": "auto",
                "bins": bins,
                "style": style,
                "detail": f"已创建直方图 {short_name}（{bins} 个 bin，{v.size} 点）",
            }

        # 特殊图型：箱线图 / 条形图（Origin 官方模板，比 plotxy 代码稳定可靠；
        # plotxy 的 204/215 在部分 Origin 2026b 上会渲染成面积图或不出图）
        if plot_type in ("box", "bar"):
            is_bar = plot_type == "bar"
            ncols = wks.obj.Cols
            if y_columns:
                c = y_columns[0]
                ci2 = _col_index_impl(wks, c)
                col = ci2 if ci2 is not None else (1 if ncols > 1 else 0)
            else:
                col = 1 if ncols > 1 else 0   # 默认取第二列（首列为 X 的惯例）
            templ = "bar" if is_bar else "box"
            gp = op.new_graph(lname=lname or ("Bar" if is_bar else "Box"),
                              template=templ)
            gl = gp[0]
            p = gl.add_plot(wks, col, "#", type="?")   # '#' = 行号/类别作 X
            if p is None:
                return oerr.fail("origin_operation_error", f"{plot_type} 图创建失败")
            gl.rescale()
            short_name = gp.obj.GetName()
            return {
                "ok": True,
                "graph": short_name,
                "graph_short": short_name,
                "plot_type": plot_type,
                "y_columns": [str(col)],
                "x_column": "auto",
                "detail": f"已创建图 {short_name}（{PLOT_TYPES_CN.get(plot_type, plot_type)}）",
            }

        # 主路径：XY 基础图型（line/scatter/line_symbol/column）
        if x_column is None:
            x_column = 0  # 默认第一列（列索引），与 write_data 的"第一列自动为 X"一致
        if y_columns is None:
            y_columns = _y_columns_impl(wks, x_column)

        gp = op.new_graph(lname=lname)   # 短名=给定名或自动分配
        gl = gp[0]
        plotted = []
        for yc in y_columns:
            p = gl.add_plot(wks, yc, x_column, type=PLOT_TYPES[plot_type],
                            colyerr=yerr_column or -1)
            if p is None:
                return oerr.fail("column_not_found", f"画图失败: y={yc}, x={x_column}",
                                 y_column=str(yc), x_column=str(x_column))
            plotted.append(str(yc))
        gl.rescale()
        short_name = gp.obj.GetName()
        x_title = None
        try:
            # 解析真实 X 列名（x_column 可能是 0 起始索引），再语义推断标题
            x_name = x_column if not isinstance(x_column, int) else None
            if x_name is None and x_column is not None:
                x_name = _col_name_impl(wks, int(x_column))
            if x_name:
                t = pst.infer_axis_title([str(x_name)])["title"]
                if t and str(t).strip() and not str(t).strip().isdigit():
                    x_title = t
        except Exception:
            pass
        style = _apply_style_impl(short_name, plot_type=plot_type,
                                  columns=plotted, style_mode=style_mode,
                                  family=family, x_title=x_title)
        return {
            "ok": True,
            "graph": short_name,
            "graph_short": short_name,
            "plot_type": plot_type,
            "y_columns": plotted,
            "x_column": str(x_column),
            "yerr_column": str(yerr_column) if yerr_column is not None else None,
            "style": style,
            "detail": f"已创建图 {short_name}（{PLOT_TYPES_CN.get(plot_type, plot_type)}，{len(plotted)} 条曲线）",
        }
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _y_columns_impl(wks, x_column):
    """除 X 列（按索引）外的全部列，返回列名列表。"""
    x_idx = None
    if isinstance(x_column, int):
        x_idx = x_column
    else:
        try:
            x_idx = wks._col_index(x_column)
        except Exception:
            x_idx = None
    cols = []
    for i in range(wks.obj.Cols):
        if i == x_idx:
            continue
        try:
            cname = wks.obj[i].GetLongName() or wks.obj[i].GetName()
        except Exception:
            cname = str(i)
        cols.append(cname)
    return cols or [0]


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def _export_impl(graph, file_path=None, fmt="png", width=1200, output_dir=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app

        fmt = (fmt or "png").lower().lstrip(".")
        if fmt not in ("png", "svg"):
            return {"ok": False, "error": f"fmt 只支持 png/svg，收到 {fmt!r}"}

        gp = op.find_graph(graph)
        if not gp:
            return {"ok": False, "error": f"图不存在: {graph}"}

        if file_path:
            file_path = os.path.abspath(file_path)
            ext = os.path.splitext(file_path)[1].lstrip(".").lower()
            if ext and ext != fmt:
                fmt = ext
        else:
            out_dir = os.path.abspath(output_dir or DEFAULT_OUTPUT_DIR)
            os.makedirs(out_dir, exist_ok=True)
            base = re.sub(r"[^\w\-.]", "_", str(graph).replace(" ", "_"))
            file_path = os.path.join(out_dir, f"{base}.{fmt}")

        kwargs = {"replace": True}
        if fmt == "png" and width and width > 0:
            kwargs["width"] = int(width)
        result = gp.save_fig(file_path, **kwargs)
        if not result or not os.path.exists(result):
            return {"ok": False, "error": f"导出失败，save_fig 返回 {result!r}"}
        return {
            "ok": True,
            "file": result,
            "size": os.path.getsize(result),
            "format": fmt,
            "detail": f"已导出 {fmt.upper()} -> {result} ({os.path.getsize(result)} bytes)",
        }
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


# ---------------------------------------------------------------------------
# 一站式：写数 + 画图 + 导出（在 COM 线程内直接调用各 impl，避免嵌套投递）
# ---------------------------------------------------------------------------
def _plot_file_impl(columns, plot_type="line", fmt="png", file_path=None, width=1200,
                    output_dir=None, x_column=None, y_columns=None, title=None,
                    graph_name=None, style_mode=None, family=None):
    try:
        r1 = _write_data_impl(columns)
        if not r1.get("ok"):
            return r1
        r2 = _plot_impl(r1["worksheet"], y_columns=y_columns, x_column=x_column,
                        plot_type=plot_type, title=title, graph_name=graph_name,
                        style_mode=style_mode, family=family)
        if not r2.get("ok"):
            return r2
        result = _export_impl(r2["graph"], file_path=file_path, fmt=fmt, width=width,
                              output_dir=output_dir)
        if result.get("ok"):
            result["graph"] = r2.get("graph")
            result["plot_type"] = r2.get("plot_type")
            result["style"] = r2.get("style")
        return result
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


# ---------------------------------------------------------------------------
# 进阶能力：删点 / 拟合 / 3D（在 COM 线程内直接调用各 impl，避免嵌套投递）
# ---------------------------------------------------------------------------
def _filter_data_impl(worksheet, drop_rows=None, x_column=0, x_min=None, x_max=None):
    """删除数据点：按行索引删除 + 按 X 列范围裁剪（写回原工作表）。

    返回: {"ok": True, "worksheet": ..., "kept": N, "dropped": M, ...}
    """
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}

        ncol = wks.obj.Cols
        cols = [wks.to_list(i) for i in range(ncol)]
        if not cols or not cols[0]:
            return {"ok": False, "error": "工作表无数据"}
        n = len(cols[0])
        if isinstance(x_column, str):
            try:
                x_column = wks._col_index(x_column)
            except Exception:
                return {"ok": False, "error": f"X 列不存在: {x_column}"}

        drop_set = set(int(r) for r in (drop_rows or []) if isinstance(r, (int, float)))
        keep_idx = []
        for r in range(n):
            if r in drop_set:
                continue
            if x_min is not None and cols[x_column][r] < x_min:
                continue
            if x_max is not None and cols[x_column][r] > x_max:
                continue
            keep_idx.append(r)

        dropped = n - len(keep_idx)
        if dropped == 0:
            return {"ok": True, "worksheet": str(wks), "kept": n, "dropped": 0,
                    "detail": "没有需要删除的数据点"}
        # 重写各列：保留行 + NaN 填充尾部（Origin 将 NaN 视为缺失，图上不显示）
        for i, col in enumerate(cols):
            newvals = [col[r] for r in keep_idx]
            newvals += [float("nan")] * dropped
            wks.from_list(i, newvals)
        return {
            "ok": True,
            "worksheet": str(wks),
            "kept": len(keep_idx),
            "dropped": dropped,
            "detail": f"已删除 {dropped} 个数据点（保留 {len(keep_idx)}），写回 {wks}",
        }
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _fit_impl(worksheet, x_column, y_column, kind="linear", plot_curve=True,
              graph=None, title=None):
    """拟合：linear（线性）或 Origin 内置拟合函数名（如 ExpDec1/Gauss/Polynomial...）。

    返回: {"ok": True, "kind": ..., "parameters": {...}, "report": ..., "fit_curves": ...,
           "graph": 可选（拟合曲线已上图时）}
    """
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}

        kind = (kind or "linear").strip()
        if kind == "linear":
            lr = op.LinearFit()
            lr.set_data(wks, x_column, y_column)
            res = lr.result()
            try:
                parameters = {
                    "slope": res["Parameters"]["Slope"]["Value"],
                    "slope_error": res["Parameters"]["Slope"].get("Error"),
                    "intercept": res["Parameters"]["Intercept"]["Value"],
                    "intercept_error": res["Parameters"]["Intercept"].get("Error"),
                    "note": "R² 等统计量见报告表（report 字段）",
                }
            except Exception:
                parameters = {"raw": res}
            rep, curves = lr.report(0)
            fit_kind = "linear"
        else:
            model = op.NLFit(kind)          # kind = Origin 内置函数名
            model.set_data(wks, x_column, y_column)
            model.fit()
            rep, curves = model.report()    # 必须先 report
            res = model.result()
            # result() 返回扁平键：参数名直接作 key（如 y0/A/t1），
            # e_/s_/f_/u_/l_/ub/lb 前缀是误差/固定/边界元数据，跳过
            parameters = {}
            for k, v in res.items():
                if isinstance(v, (int, float)) and not k.startswith(
                        ("f_", "s_", "u_", "l_", "ub", "lb", "e_", "Data")):
                    parameters[k] = v
            fit_kind = kind

        result = {
            "ok": True,
            "kind": fit_kind,
            "parameters": parameters,
            "report": rep,
            "fit_curves": curves,
            "worksheet": str(wks),
            "detail": f"{fit_kind} 拟合完成，参数见 parameters",
        }

        # 拟合曲线加图：原始数据（散点）+ 拟合曲线（线）
        if plot_curve and curves:
            wc = op.find_sheet("w", curves)
            if graph:
                gp = op.find_graph(graph)
                if not gp:
                    return {**result, "warning": f"图不存在: {graph}，未添加拟合曲线"}
            else:
                gp = op.new_graph(lname=title or f"{fit_kind}_fit")
            gl = gp[0]
            gl.add_plot(wks, y_column, x_column, type="s")
            gl.add_plot(wc, 1, 0, type="l")
            gl.rescale()
            gname = gp.obj.GetName()
            result["graph"] = gname
            result["detail"] += f"，拟合曲线已上图（{gname}）"
        return result
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _plot3d_impl(data, plot_type="surface", fmt="png", file_path=None, width=1200,
                 output_dir=None, title=None):
    """3D 图：surface（矩阵表面）或 scatter（XYZ 散点）。

    surface 的 data: {"z": [[...],...]}（2D 网格，自动生成 X/Y 索引网格）
                     或 {"x": [...], "y": [...], "z": [[...],...]}（显式网格向量）
    scatter 的 data: {"x": [...], "y": [...], "z": [...]}
    返回: {"ok": True, "graph": ..., "file": ..., ...}
    """
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        import numpy as np

        plot_type = (plot_type or "surface").lower()
        if plot_type not in ("surface", "scatter"):
            return {"ok": False, "error": f"plot_type 必须是 surface/scatter，收到 {plot_type!r}"}

        if plot_type == "surface":
            if not (isinstance(data, dict) and "z" in data):
                return {"ok": False, "error": "surface 需要 data={'z': 二维网格, 可选 x/y 向量}"}
            z2d = np.asarray(data["z"], dtype=float)
            if z2d.ndim != 2:
                return {"ok": False, "error": "z 必须是二维网格（列表的列表）"}
            ny, nx = z2d.shape
            if "x" in data and "y" in data:
                gx, gy = np.meshgrid(np.asarray(data["x"], dtype=float),
                                     np.asarray(data["y"], dtype=float))
            else:
                gx, gy = np.meshgrid(np.arange(nx, dtype=float),
                                     np.arange(ny, dtype=float))
            ms = op.new_sheet("m", "SurfData")
            ms.from_np(np.array([z2d, gx, gy]))       # Z, X, Y 三个矩阵对象
            gp = op.new_graph(lname=title or "SurfPlot", template="GLparafunc")
            gl = gp[0]
            gl.add_mplot(ms, 0, 1, 2)
            gl.rescale()
            gname = gp.obj.GetName()
        else:  # scatter
            if not (isinstance(data, dict) and all(k in data for k in ("x", "y", "z"))):
                return {"ok": False, "error": "scatter 需要 data={'x': [...], 'y': [...], 'z': [...]}"}
            lens = {len(data[k]) for k in ("x", "y", "z")}
            if len(lens) != 1:
                return {"ok": False, "error": "x/y/z 长度必须一致"}
            wks = op.new_sheet("w", _new_unique_name("Scat3D"))
            wks.from_list(0, list(data["x"]), lname="X")
            wks.from_list(1, list(data["y"]), lname="Y")
            wks.from_list(2, list(data["z"]), lname="Z")
            wks.activate()
            before = _page_names()
            gname = _plotxy_new_page("plotxy iy:=(1,2,3) plot:=310;", before)
            if not gname:
                return oerr.fail(
                    "unsupported_origin_feature",
                    "3D 散点图创建失败（plotxy 310 在当前 Origin 上无输出，可改用 origin_plot3d 的 surface）")
            gp = op.find_graph(gname)
            if not gp:
                return oerr.fail("origin_operation_error", f"3D 散点图创建失败: {gname}",
                                 gname=gname)
            gname = gp.obj.GetName()

        r = _export_impl(gname, file_path=file_path, fmt=fmt, width=width,
                         output_dir=output_dir)
        if not r.get("ok"):
            return r
        return {
            "ok": True,
            "graph": gname,
            "plot_type": plot_type,
            "file": r["file"],
            "size": r["size"],
            "format": r["format"],
            "detail": f"3D {plot_type} 图 {gname} 已导出 -> {r['file']}",
        }
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


# ---------------------------------------------------------------------------
# 科学分析：统计 / 变换 / 积分 / FFT / 相关 / 峰值 / 直方图 / 等高线
# ---------------------------------------------------------------------------
def _col_index_impl(wks, col):
    """列名或索引 -> 0 起始索引；不存在返回 None。"""
    if isinstance(col, int):
        return col if 0 <= col < wks.obj.Cols else None
    try:
        idx = wks._col_index(col)
        return idx if idx >= 0 else None
    except Exception:
        return None


def _write_col_impl(wks, data, lname=None):
    """把数据写为新列，返回列名。"""
    ncol = wks.obj.Cols
    wks.from_list(ncol, list(data), lname=lname or f"C{ncol + 1}")
    try:
        return wks.obj[ncol].GetLongName() or wks.obj[ncol].GetName()
    except Exception:
        return str(ncol)


def _stats_impl(worksheet, columns=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        import numpy as np
        if columns is None:
            columns = [wks.obj[i].GetLongName() or wks.obj[i].GetName()
                       for i in range(wks.obj.Cols)]
        out = {}
        for c in columns:
            ci = _col_index_impl(wks, c)
            if ci is None or ci >= wks.obj.Cols:
                return {"ok": False, "error": f"列不存在: {c}"}
            v = np.asarray(wks.to_list(ci), dtype=float)
            v = v[np.isfinite(v)]
            if v.size == 0:
                out[str(c)] = {"error": "无有效数值"}
                continue
            out[str(c)] = {
                "count": int(v.size),
                "mean": float(v.mean()),
                "std": float(v.std(ddof=1)) if v.size > 1 else 0.0,
                "min": float(v.min()),
                "p25": float(np.percentile(v, 25)),
                "median": float(np.median(v)),
                "p75": float(np.percentile(v, 75)),
                "max": float(v.max()),
                "skew": float(__skew_impl(v)) if v.size > 2 else 0.0,
            }
        return {"ok": True, "worksheet": str(wks), "stats": out,
                "detail": "描述统计完成（count/mean/std/min/p25/median/p75/max/skew）"}
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def __skew_impl(v):
    import numpy as np
    m = v.mean()
    s = v.std(ddof=1)
    if s == 0:
        return 0.0
    return float((((v - m) / s) ** 3).mean())


def _transform_impl(worksheet, column, op="smooth", window=5, method="moving",
                    new_x=None, write_back=True):
    """数据变换：smooth(移动平均/中值) | normalize(minmax/zscore/sum) | derivative | interpolate。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op_ = _origin_app
        wks = op_.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        import numpy as np

        ci = _col_index_impl(wks, column)
        if ci is None:
            return {"ok": False, "error": f"列不存在: {column}"}
        v = np.asarray(wks.to_list(ci), dtype=float)
        n = v.size
        op_name = (op or "smooth").lower()

        if op_name == "smooth":
            w = max(1, int(window))
            if w % 2 == 0:
                w += 1
            if method == "median":
                out = np.array([np.median(v[max(0, i - w // 2): i + w // 2 + 1])
                                for i in range(n)])
            else:  # moving average
                kernel = np.ones(w) / w
                out = np.convolve(v, kernel, mode="same")
                # 边界修正（convolve same 两端偏差，用可用窗口重算）
                for i in range(w // 2):
                    lo, hi = 0, i + w // 2 + 1
                    out[i] = v[lo:hi].mean()
                    out[n - 1 - i] = v[n - 1 - hi + 1:].mean()
        elif op_name == "normalize":
            method = (method or "minmax").lower()
            if method == "zscore":
                s = v.std(ddof=1)
                out = (v - v.mean()) / s if s else v - v.mean()
            elif method == "sum":
                out = v / v.sum() if v.sum() else v
            else:  # minmax
                r = v.max() - v.min()
                out = (v - v.min()) / r if r else v - v.min()
        elif op_name == "derivative":
            if ci + 1 < wks.obj.Cols and ci - 1 >= 0:
                # 有 X 列（通常第 0 列）时用 x 差分
                xv = np.asarray(wks.to_list(0), dtype=float)
                out = np.gradient(v, xv)
            else:
                out = np.gradient(v)
        elif op_name == "interpolate":
            if new_x is None:
                return {"ok": False, "error": "interpolate 需要 new_x（新 x 网格列表）"}
            xv = np.asarray(wks.to_list(0), dtype=float)
            nx = np.asarray(new_x, dtype=float)
            out = np.interp(nx, xv, v)
            # 写回时同时写新 x
            xname = _write_col_impl(wks, nx, lname=f"x_interp")
            new_col = _write_col_impl(wks, out, lname=f"{_col_name_impl(wks, ci)}_interp")
            return {"ok": True, "worksheet": str(wks), "new_column": new_col,
                    "new_x_column": xname, "points": int(nx.size),
                    "detail": f"插值完成 -> 新列 {new_col}（{nx.size} 点）"}
        else:
            return {"ok": False, "error": f"op 必须是 smooth/normalize/derivative/interpolate，收到 {op_name!r}"}

        if write_back:
            new_col = _write_col_impl(wks, out,
                                      lname=f"{_col_name_impl(wks, ci)}_{op_name}")
        else:
            new_col = None
        return {"ok": True, "worksheet": str(wks), "new_column": new_col,
                "points": int(n), "op": op_name,
                "detail": f"{op_name} 完成" + (f"，结果写入新列 {new_col}" if new_col else "")}
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _col_name_impl(wks, ci):
    try:
        return wks.obj[ci].GetLongName() or wks.obj[ci].GetName()
    except Exception:
        return str(ci)


def _integrate_impl(worksheet, x_column=0, y_column=1):
    """数值积分（梯形法），返回曲线下面积 AUC。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        import numpy as np
        xi = _col_index_impl(wks, x_column)
        yi = _col_index_impl(wks, y_column)
        if xi is None or yi is None:
            return {"ok": False, "error": "x/y 列不存在"}
        xv = np.asarray(wks.to_list(xi), dtype=float)
        yv = np.asarray(wks.to_list(yi), dtype=float)
        mask = np.isfinite(xv) & np.isfinite(yv)
        auc = float(np.trapezoid(yv[mask], xv[mask]))
        return {"ok": True, "worksheet": str(wks), "auc": auc,
                "x_column": str(x_column), "y_column": str(y_column),
                "points": int(mask.sum()),
                "detail": f"曲线下面积 AUC = {auc:.6g}（梯形法，{int(mask.sum())} 点）"}
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _fft_impl(worksheet, x_column=0, y_column=1, plot_spectrum=False,
              file_path=None, fmt="png", width=1200, top=5):
    """FFT 频谱分析：返回幅度谱与前 top 个主频；可选画频谱图并导出。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        import numpy as np
        xi = _col_index_impl(wks, x_column)
        yi = _col_index_impl(wks, y_column)
        xv = np.asarray(wks.to_list(xi), dtype=float)
        yv = np.asarray(wks.to_list(yi), dtype=float)
        n = xv.size
        if n < 4:
            return {"ok": False, "error": "数据点太少（至少 4 点）"}
        dx = float(np.median(np.diff(xv))) if n > 1 else 1.0
        if dx <= 0:
            return {"ok": False, "error": "x 必须单调递增（均匀采样）"}
        yv = yv - yv.mean()
        spec = np.abs(np.fft.rfft(yv))
        freqs = np.fft.rfftfreq(n, d=dx)
        amps = spec / n * 2
        amps[0] /= 2
        # 主频（跳过 DC）
        idx = np.argsort(amps[1:])[::-1][: max(1, int(top))] + 1
        peaks = [{"frequency": float(freqs[i]), "amplitude": float(amps[i])}
                 for i in idx]
        result = {
            "ok": True,
            "worksheet": str(wks),
            "n_points": n,
            "sampling_interval": dx,
            "nyquist": float(freqs[-1]),
            "top_frequencies": peaks,
            "detail": f"FFT 完成：{n} 点，采样间隔 {dx:.6g}，主频 {peaks[0]['frequency']:.6g}",
        }
        if plot_spectrum:
            ws = op.new_sheet("w", "FFTSpectrum")
            ws.from_list(0, list(freqs), lname="Frequency")
            ws.from_list(1, list(amps), lname="Amplitude")
            gp = op.new_graph(lname="FFT Spectrum")
            gl = gp[0]
            gl.add_plot(ws, 1, 0, type="l")
            gl.rescale()
            gname = gp.obj.GetName()
            result["graph"] = gname
            r = _export_impl(gname, file_path=file_path, fmt=fmt, width=width)
            if r.get("ok"):
                result["file"] = r["file"]
                result["size"] = r["size"]
                result["format"] = r["format"]
            else:
                result["warning"] = f"频谱图导出失败: {r.get('error')}"
        return result
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _correlate_impl(worksheet, columns=None):
    """Pearson 相关矩阵。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        import numpy as np
        if columns is None:
            columns = [wks.obj[i].GetLongName() or wks.obj[i].GetName()
                       for i in range(wks.obj.Cols)]
        names = [str(c) for c in columns]
        mat = []
        used = []
        for c in columns:
            ci = _col_index_impl(wks, c)
            if ci is None:
                return {"ok": False, "error": f"列不存在: {c}"}
            v = np.asarray(wks.to_list(ci), dtype=float)
            mat.append(v)
            used.append(str(c))
        # 长度不一致时按最短列截断（如插值/变换产生短列）
        minlen = min(len(v) for v in mat)
        if minlen == 0:
            return {"ok": False, "error": "存在空列，无法计算相关"}
        arr = np.vstack([v[:minlen] for v in mat])
        corr = np.corrcoef(arr)
        note = ""
        if any(len(v) != minlen for v in mat):
            note = f"（列长度不一致，已按最短 {minlen} 行截断计算）"
        return {
            "ok": True,
            "worksheet": str(wks),
            "columns": used,
            "correlation": [[float(x) for x in row] for row in corr],
            "rows_used": int(minlen),
            "detail": f"Pearson 相关矩阵（{len(used)} 列）{note}",
        }
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _peak_find_impl(worksheet, x_column=0, y_column=1, min_height=None,
                    min_distance=1):
    """峰值检测：局部极大值 + 最小峰高过滤 + 最小间距去重。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        import numpy as np
        xi = _col_index_impl(wks, x_column)
        yi = _col_index_impl(wks, y_column)
        xv = np.asarray(wks.to_list(xi), dtype=float)
        yv = np.asarray(wks.to_list(yi), dtype=float)
        n = yv.size
        cands = [i for i in range(1, n - 1)
                 if yv[i] >= yv[i - 1] and yv[i] >= yv[i + 1]]
        if min_height is not None:
            cands = [i for i in cands if yv[i] >= min_height]
        # min_distance 去重：间距内保留最高峰
        cands.sort(key=lambda i: yv[i], reverse=True)
        picked = []
        for i in cands:
            if all(abs(i - j) >= max(1, int(min_distance)) for j in picked):
                picked.append(i)
        picked.sort()
        peaks = [{"index": int(i), "x": float(xv[i]), "y": float(yv[i])}
                 for i in picked]
        return {
            "ok": True,
            "worksheet": str(wks),
            "peaks": peaks,
            "count": len(peaks),
            "detail": f"检测到 {len(peaks)} 个峰值",
        }
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _histogram_impl(worksheet, column=0, bins=10, plot=False, file_path=None,
                    fmt="png", width=1200):
    """直方图：返回 bin 区间与频数；plot=True 时画柱状图并导出。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        import numpy as np
        ci = _col_index_impl(wks, column)
        v = np.asarray(wks.to_list(ci), dtype=float)
        v = v[np.isfinite(v)]
        if v.size == 0:
            return {"ok": False, "error": "无有效数值"}
        counts, edges = np.histogram(v, bins=int(bins))
        centers = (edges[:-1] + edges[1:]) / 2
        result = {
            "ok": True,
            "worksheet": str(wks),
            "column": str(column),
            "bins": int(bins),
            "counts": [int(c) for c in counts],
            "bin_edges": [float(e) for e in edges],
            "bin_centers": [float(c) for c in centers],
            "detail": f"直方图统计完成（{int(bins)} 个 bin，{v.size} 点）",
        }
        if plot:
            ws = op.new_sheet("w", "HistData")
            ws.from_list(0, list(centers), lname="bin_center")
            ws.from_list(1, list(counts), lname="count")
            gp = op.new_graph(lname="Histogram")
            gl = gp[0]
            gl.add_plot(ws, 1, 0, type="c")
            gl.rescale()
            gname = gp.obj.GetName()
            result["graph"] = gname
            r = _export_impl(gname, file_path=file_path, fmt=fmt, width=width)
            if r.get("ok"):
                result["file"] = r["file"]
                result["size"] = r["size"]
                result["format"] = r["format"]
            else:
                result["warning"] = f"直方图导出失败: {r.get('error')}"
        return result
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _plot_contour_impl(data, plot_type="contour", fmt="png", file_path=None,
                       width=1200, output_dir=None, title=None):
    """等高线图：data={"z": 2D 网格, 可选 x/y}；plot_type: contour|contour_fill|3d_wire。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        import numpy as np
        plot_type = (plot_type or "contour").lower()
        if plot_type not in MATRIX_PLOT_TYPES:
            return {"ok": False, "error": f"plot_type 必须是 {list(MATRIX_PLOT_TYPES)} 之一"}
        if not (isinstance(data, dict) and "z" in data):
            return {"ok": False, "error": "需要 data={'z': 二维网格, 可选 x/y 向量}"}
        z2d = np.asarray(data["z"], dtype=float)
        if z2d.ndim != 2:
            return {"ok": False, "error": "z 必须是二维网格"}
        ny, nx = z2d.shape
        if "x" in data and "y" in data:
            gx, gy = np.meshgrid(np.asarray(data["x"], dtype=float),
                                 np.asarray(data["y"], dtype=float))
        else:
            gx, gy = np.meshgrid(np.arange(nx, dtype=float), np.arange(ny, dtype=float))
        ms = op.new_sheet("m", "ContourData")
        ms.from_np(np.array([z2d, gx, gy]))
        gp = op.new_graph(lname=title or "Contour", template="GLparafunc")
        gl = gp[0]
        gl.add_mplot(ms, 0, 1, 2, type=MATRIX_PLOT_TYPES[plot_type])
        gl.rescale()
        gname = gp.obj.GetName()
        r = _export_impl(gname, file_path=file_path, fmt=fmt, width=width,
                         output_dir=output_dir)
        if not r.get("ok"):
            return r
        return {
            "ok": True,
            "graph": gname,
            "plot_type": plot_type,
            "file": r["file"],
            "size": r["size"],
            "format": r["format"],
            "detail": f"{plot_type} 图 {gname} 已导出 -> {r['file']}",
        }
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def _status_impl():
    ok, conn = _connect_impl()
    info = dict(conn)
    info["plot_types"] = PLOT_TYPES
    info["default_output_dir"] = DEFAULT_OUTPUT_DIR
    info["python"] = sys.executable
    return info


def _help_impl():
    """快速使用速查（不连接 Origin，秒回）。模型画图前调用一次即可上手。"""
    return {
        "ok": True,
        "usage": (
            "最快路径：origin_plot_file(columns={'x':[...], 'y':[...]}, "
            "plot_type='line'|'scatter'|'line_symbol'|'column', fmt='png'|'svg', "
            "file_path='可选绝对路径', width=1200, title='可选') -> "
            "返回 {'ok': true, 'file': '绝对路径'}。"
        ),
        "data_format": {
            "columns": "dict {列名: [数值列表]}，第一列自动设为 X，其余为 Y；也可传二维列表 [[..],[..]] 或一维列表",
            "worksheet": "write_data 返回的 '[Book]Sheet' 引用，供 plot/fit/filter 等使用",
            "graph": "plot 返回的图短名，供 export 使用",
        },
        "tools": {
            "origin_status": "检查连接（Origin 未启动会自动启动，首连 5~45 秒）",
            "origin_write_data": "写多列数据 -> 返回 worksheet",
            "origin_plot": "画图（含 histogram/box/bar + yerr_column 误差棒）-> 返回 graph",
            "origin_export": "导出 PNG/SVG -> 返回 file 绝对路径",
            "origin_plot_file": "一键 写数+画图+导出 -> 返回 file（最常用）",
            "origin_filter_data": "删点/裁剪（drop_rows 索引 或 x_min/x_max）",
            "origin_fit": "拟合 kind=linear 或 Origin 函数名(ExpDec1/Gauss/Polynomial/...)，拟合曲线上图",
            "origin_plot3d": "3D surface(需 {'z': 2D网格}) / scatter(需 {'x','y','z'})",
            "origin_stats": "描述统计 count/mean/std/min/p25/median/p75/max/skew",
            "origin_transform": "smooth/normalize/derivative/interpolate（写回新列）",
            "origin_integrate": "梯形法 AUC",
            "origin_fft": "FFT 频谱（top 主频 + plot_spectrum 频谱图）",
            "origin_correlate": "Pearson 相关矩阵",
            "origin_peak_find": "峰值检测（min_height/min_distance）",
            "origin_histogram": "直方图统计（plot=True 画图导出）",
            "origin_plot_contour": "等高线 contour/contour_fill/3d_wire（需 {'z': 2D网格}）",
            "origin_catalog": "动态工具目录（按分类列出全部工具）",
            "origin_read_worksheet": "读取工作表列数据（含列角色/点数）",
            "origin_view_graph": "把图渲染为内联图片，模型可直接看（不落盘）",
            "origin_apply_style": "对已有图应用排版/调色板/多序列区分（style_mode/family）",
            "origin_ttest": "t 检验：one/两样本(Welch)/paired",
            "origin_anova": "单因素方差分析（每组一列）",
            "origin_pca": "主成分分析（载荷/解释方差/得分）",
            "origin_survival": "Kaplan-Meier 生存分析（时间列+事件列）",
            "origin_list_graphs": "列出项目图页短名",
            "origin_error_codes": "列出全部稳定错误码与恢复建议",
        },
        "templates": [
            "折线图: origin_plot_file(columns, plot_type='line')",
            "散点图: origin_plot_file(columns, plot_type='scatter')",
            "直方图: origin_histogram(worksheet, column, bins=10, plot=True)",
            "箱线图: origin_plot(worksheet, y_columns=[col], plot_type='box')",
            "误差棒: origin_plot(worksheet, y_columns=[y], x_column=x, yerr_column=err)",
            "拟合: origin_write_data -> origin_fit(worksheet, kind='ExpDec1')",
            "FFT: origin_fft(worksheet, x_column, y_column, plot_spectrum=True)",
            "3D 表面: origin_plot3d({'z': [[..],..]}, plot_type='surface')",
            "等高线: origin_plot_contour({'z': [[..],..]})",
            "删异常点: origin_filter_data(worksheet, x_min=.., x_max=..) 再 plot",
        ],
        "tips": [
            "所有工具返回 JSON；ok=false 时读 error_code / recoverable / next_actions 安全分支重试",
            "画图可用 style_mode=default|journal|presentation 与 family=调色板家族 提升排版",
            "需视觉校验时调用 origin_view_graph（返回内联图片，模型可直接看）",
            "幂等命名：origin_plot 传 graph_name 重复调用会清旧重画，图名稳定",
            "file_path 省略时输出到 ~/dsch_origin_plugin/output（自动命名）",
            "数据 1000 点内秒级完成；不要读 README.md，本速查即完整用法",
        ],
    }


def _list_sheets_impl():
    ok, conn = _connect_impl()
    if not ok:
        return conn
    try:
        op = _origin_app
        pages = []
        try:
            n = op.po.Pages.Count
            for i in range(n):
                try:
                    pages.append(str(op.po.Pages(i).GetName()))
                except Exception:
                    pass
        except Exception:
            pass
        return {"ok": True, "pages": pages}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# 新增能力：读工作表 / 样式应用 / 统计批 / 视觉预览 / 查询
# ---------------------------------------------------------------------------
def _read_worksheet_impl(worksheet, columns=None, max_rows=None):
    """读取工作表列数据（列名 -> 数值列表）+ 列角色信息。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return oerr.fail("worksheet_not_found", f"工作表不存在: {worksheet}",
                             worksheet=worksheet)
        ncol = wks.obj.Cols
        if columns is None:
            columns = [_col_name_impl(wks, i) for i in range(ncol)]
        out = {}
        col_meta = []
        for c in columns:
            ci = _col_index_impl(wks, c)
            if ci is None:
                return oerr.fail("column_not_found", f"列不存在: {c}", column=str(c))
            vals = wks.to_list(ci)
            if max_rows is not None:
                vals = vals[: int(max_rows)]
            out[str(c)] = vals
            col_meta.append({"name": str(c), "index": int(ci), "points": len(vals)})
        n_rows = max((len(v) for v in out.values()), default=0)
        return oerr.ok(worksheet=str(wks), columns=out, column_meta=col_meta,
                       n_rows=int(n_rows), n_columns=len(out),
                       detail=f"已读取 {len(out)} 列 x {n_rows} 行")
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _apply_style_impl(graph, plot_type=None, columns=None, style_mode="default",
                      family=None, x_title=None):
    """应用默认排版规则：调色板 + 多序列区分 + 语义轴标题（真机验证可靠）。

    返回 {ok, applied, applied_ops, style_plan}：每一步都给 reason，方便排查。
    仅当多序列或显式指定 style_mode/family 时改色；单序列保持 Origin 默认。
    """
    try:
        op = _origin_app
        gp = op.find_graph(graph)
        if gp is None:
            return oerr.fail("graph_not_found", f"图不存在: {graph}", graph=graph)
        gl = gp[0]
        plots = gl.plot_list() or []
        n = len(plots)
        if n == 0:
            return oerr.ok(applied=False, reason="图中没有 plot（空模板？）")
        rows_est = 0
        for p in plots:
            try:
                rows_est = max(rows_est, int(getattr(p, "size", 0) or 0))
            except Exception:
                pass

        col_names = columns or [f"S{i+1}" for i in range(n)]
        style_plan = pst.full_style_plan(plot_type or "line", col_names, rows_est,
                                         style_mode=style_mode, family=family)

        applied_ops = []
        do_color = (n > 1) or bool(style_mode) or bool(family)
        if do_color:
            pal = style_plan["palette"]["colors"]
            for i, p in enumerate(plots):
                try:
                    rgb = pal[i % len(pal)]
                    p.color = _hex_to_rgb_tuple(rgb)
                    applied_ops.append(f"plot[{i}].color={rgb}")
                except Exception:
                    pass
        distinct = style_plan["series_distinction"]
        if distinct["kind"] == "symbol_shape_cycle":
            for i, p in enumerate(plots):
                try:
                    p.symbol_kind = int(distinct["assignments"][i])
                    applied_ops.append(f"plot[{i}].symbol_kind={distinct['assignments'][i]}")
                except Exception:
                    pass
        if style_plan["readability"]["tweaks"].get("marker_downscale"):
            for p in plots:
                try:
                    p.symbol_size = 6
                    applied_ops.append("marker_downscale symbol_size=6")
                except Exception:
                    pass

        # 轴标题：用 GLayer.axis('x'/'y').title（真机验证可靠）
        y_title = None
        if isinstance(style_plan["axis_titles"].get("y"), dict):
            y_title = style_plan["axis_titles"]["y"].get("title")
        if y_title:
            try:
                gl.axis("y").title = str(y_title)
                applied_ops.append(f"y.title={y_title!r}")
            except Exception:
                pass
        if x_title:
            try:
                gl.axis("x").title = str(x_title)
                applied_ops.append(f"x.title={x_title!r}")
            except Exception:
                pass

        style_plan["applied"] = applied_ops
        return oerr.ok(applied=True, applied_ops=applied_ops, style_plan=style_plan)
    except Exception as e:
        return oerr.fail("origin_operation_error", str(e),
                         trace=traceback.format_exc(limit=3))


def _find_wks_of_plot(gl):
    try:
        pl = gl.plot_list() or []
        if pl:
            return pl[0].ws
    except Exception:
        pass
    return None


def _hex_to_rgb_tuple(hexstr):
    h = hexstr.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _ttest_impl(worksheet, column_a, column_b=None, kind="two", paired=False, mu=0.0):
    """t 检验：one(单样本 vs mu) / two(双样本 Welch) / paired(配对)。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return oerr.fail("worksheet_not_found", f"工作表不存在: {worksheet}",
                             worksheet=worksheet)
        a = _col_index_impl(wks, column_a)
        if a is None:
            return oerr.fail("column_not_found", f"列不存在: {column_a}", column=str(column_a))
        va = wks.to_list(a)
        kind = (kind or "two").lower()
        if kind == "one":
            r = oana.ttest_one_sample(va, mu=float(mu))
        elif kind == "paired":
            b = _col_index_impl(wks, column_b)
            if b is None:
                return oerr.fail("column_not_found", f"列不存在: {column_b}", column=str(column_b))
            r = oana.ttest_paired(va, wks.to_list(b))
        else:
            b = _col_index_impl(wks, column_b)
            if b is None:
                return oerr.fail("column_not_found", f"列不存在: {column_b}", column=str(column_b))
            r = oana.ttest_two_sample(va, wks.to_list(b))
        if not r.get("ok"):
            return oerr.fail("empty_data", r.get("error"))
        return oerr.ok(worksheet=str(wks), column_a=str(column_a),
                       column_b=str(column_b) if column_b else None, **r)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _anova_impl(worksheet, columns):
    """单因素方差分析：每组一列。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return oerr.fail("worksheet_not_found", f"工作表不存在: {worksheet}",
                             worksheet=worksheet)
        groups = []
        used = []
        for c in columns or []:
            ci = _col_index_impl(wks, c)
            if ci is None:
                return oerr.fail("column_not_found", f"列不存在: {c}", column=str(c))
            groups.append(wks.to_list(ci))
            used.append(str(c))
        if len(groups) < 2:
            return oerr.fail("invalid_request", "ANOVA 需要至少 2 列作为组", columns=used)
        r = oana.anova_oneway(groups)
        if not r.get("ok"):
            return oerr.fail("empty_data", r.get("error"))
        return oerr.ok(worksheet=str(wks), columns=used, **r)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _pca_impl(worksheet, columns=None, scale=False, n_components=None):
    """主成分分析（把每列当作变量、每行为样本）。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return oerr.fail("worksheet_not_found", f"工作表不存在: {worksheet}",
                             worksheet=worksheet)
        ncol = wks.obj.Cols
        if columns is None:
            columns = [_col_name_impl(wks, i) for i in range(ncol)]
        cols_data = []
        used = []
        for c in columns:
            ci = _col_index_impl(wks, c)
            if ci is None:
                return oerr.fail("column_not_found", f"列不存在: {c}", column=str(c))
            cols_data.append(wks.to_list(ci))
            used.append(str(c))
        n_rows = max((len(v) for v in cols_data), default=0)
        # 样本=行，变量=列
        matrix = [[cols_data[j][r] if r < len(cols_data[j]) else float("nan")
                   for j in range(len(cols_data))] for r in range(n_rows)]
        r = oana.pca(matrix, scale=bool(scale), n_components=n_components)
        if not r.get("ok"):
            return oerr.fail("empty_data", r.get("error"))
        # r 已含 n_samples / n_features / n_components 等，勿重复传同名键
        return oerr.ok(worksheet=str(wks), columns=used,
                       n_columns=len(used), **r)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _survival_impl(worksheet, time_column, event_column):
    """Kaplan-Meier 生存分析：time 列 + 事件列(1=事件,0=删失)。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return oerr.fail("worksheet_not_found", f"工作表不存在: {worksheet}",
                             worksheet=worksheet)
        ti = _col_index_impl(wks, time_column)
        ei = _col_index_impl(wks, event_column)
        if ti is None or ei is None:
            return oerr.fail("column_not_found", "time/event 列不存在")
        r = oana.kaplan_meier(wks.to_list(ti), wks.to_list(ei))
        if not r.get("ok"):
            return oerr.fail("empty_data", r.get("error"))
        return oerr.ok(worksheet=str(wks), time_column=str(time_column),
                       event_column=str(event_column), **r)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _view_graph_impl(graph=None, max_width=1200, fmt="png"):
    """把图渲染为临时 PNG，返回 base64（供模型视觉校验），不落盘。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        target = graph or _active_graph_shortname()
        if not target:
            return oerr.fail("graph_not_found", "未指定图名且没有活动图页")
        tmp_dir = os.path.join(DEFAULT_OUTPUT_DIR, "_preview")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp = os.path.join(tmp_dir, f"preview_{uuid.uuid4().hex[:8]}.{fmt}")
        r = _export_impl(target, file_path=tmp, fmt=fmt, width=max_width)
        if not r.get("ok"):
            return r
        try:
            with open(tmp, "rb") as f:
                data = f.read()
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass
        import base64
        return oerr.ok(graph=target, format=fmt, size=len(data),
                       width_px=max_width, image_png_base64=base64.b64encode(data).decode(),
                       detail=f"图 {target} 渲染为临时 {fmt.upper()}（{len(data)}B，不落盘）")
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _active_graph_shortname():
    op = _origin_app
    try:
        gp = op.find_graph()
        if gp is not None:
            return gp.obj.GetName()
    except Exception:
        pass
    return None


def _list_graphs_impl():
    """列出当前项目里的图页短名。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    try:
        op = _origin_app
        names = []
        n = op.po.Pages.Count
        for i in range(n):
            try:
                name = str(op.po.Pages(i).GetName())
                if op.find_graph(name) is not None:
                    names.append(name)
            except Exception:
                pass
        return oerr.ok(pages=names, count=len(names),
                       detail=f"共 {len(names)} 个图页")
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _error_codes_impl():
    """列出全部稳定错误码与恢复建议。"""
    out = {}
    for code, (recoverable, actions) in oerr.CODE_META.items():
        out[code] = {"recoverable": recoverable, "next_actions": actions}
    return oerr.ok(error_codes=out, count=len(out))


# ---------------------------------------------------------------------------
# 公开 API（线程安全：自动投递到专用 COM 线程）
# ---------------------------------------------------------------------------
@_synchronized
def connect():
    return _connect_impl()


@_synchronized
def status():
    return _status_impl()


@_synchronized
def help():
    return _help_impl()


@_synchronized
def write_data(columns, worksheet=None, book_name=None, sheet_name=None):
    return _write_data_impl(columns, worksheet=worksheet, book_name=book_name,
                            sheet_name=sheet_name)


@_synchronized
def plot(worksheet, y_columns=None, x_column=None, plot_type="line",
         graph_name=None, title=None, yerr_column=None,
         style_mode="default", family=None):
    return _plot_impl(worksheet, y_columns=y_columns, x_column=x_column,
                      plot_type=plot_type, graph_name=graph_name, title=title,
                      yerr_column=yerr_column, style_mode=style_mode, family=family)


@_synchronized
def export(graph, file_path=None, fmt="png", width=1200, output_dir=None):
    return _export_impl(graph, file_path=file_path, fmt=fmt, width=width,
                        output_dir=output_dir)


@_synchronized
def plot_file(columns, plot_type="line", fmt="png", file_path=None, width=1200,
              output_dir=None, x_column=None, y_columns=None, title=None,
              graph_name=None, style_mode="default", family=None):
    return _plot_file_impl(columns, plot_type=plot_type, fmt=fmt, file_path=file_path,
                           width=width, output_dir=output_dir, x_column=x_column,
                           y_columns=y_columns, title=title, graph_name=graph_name,
                           style_mode=style_mode, family=family)


@_synchronized
def filter_data(worksheet, drop_rows=None, x_column=0, x_min=None, x_max=None):
    return _filter_data_impl(worksheet, drop_rows=drop_rows, x_column=x_column,
                             x_min=x_min, x_max=x_max)


@_synchronized
def fit(worksheet, x_column, y_column, kind="linear", plot_curve=True,
        graph=None, title=None):
    return _fit_impl(worksheet, x_column, y_column, kind=kind, plot_curve=plot_curve,
                     graph=graph, title=title)


@_synchronized
def plot3d(data, plot_type="surface", fmt="png", file_path=None, width=1200,
           output_dir=None, title=None):
    return _plot3d_impl(data, plot_type=plot_type, fmt=fmt, file_path=file_path,
                        width=width, output_dir=output_dir, title=title)


@_synchronized
def stats(worksheet, columns=None):
    return _stats_impl(worksheet, columns=columns)


@_synchronized
def transform(worksheet, column, op="smooth", window=5, method="moving",
              new_x=None, write_back=True):
    return _transform_impl(worksheet, column, op=op, window=window, method=method,
                           new_x=new_x, write_back=write_back)


@_synchronized
def integrate(worksheet, x_column=0, y_column=1):
    return _integrate_impl(worksheet, x_column=x_column, y_column=y_column)


@_synchronized
def fft(worksheet, x_column=0, y_column=1, plot_spectrum=False,
        file_path=None, fmt="png", width=1200, top=5):
    return _fft_impl(worksheet, x_column=x_column, y_column=y_column,
                     plot_spectrum=plot_spectrum, file_path=file_path, fmt=fmt,
                     width=width, top=top)


@_synchronized
def correlate(worksheet, columns=None):
    return _correlate_impl(worksheet, columns=columns)


@_synchronized
def peak_find(worksheet, x_column=0, y_column=1, min_height=None, min_distance=1):
    return _peak_find_impl(worksheet, x_column=x_column, y_column=y_column,
                           min_height=min_height, min_distance=min_distance)


@_synchronized
def histogram(worksheet, column=0, bins=10, plot=False, file_path=None,
              fmt="png", width=1200):
    return _histogram_impl(worksheet, column=column, bins=bins, plot=plot,
                           file_path=file_path, fmt=fmt, width=width)


@_synchronized
def plot_contour(data, plot_type="contour", fmt="png", file_path=None, width=1200,
                 output_dir=None, title=None):
    return _plot_contour_impl(data, plot_type=plot_type, fmt=fmt,
                              file_path=file_path, width=width,
                              output_dir=output_dir, title=title)


@_synchronized
def list_sheets():
    return _list_sheets_impl()


@_synchronized
def read_worksheet(worksheet, columns=None, max_rows=None):
    return _read_worksheet_impl(worksheet, columns=columns, max_rows=max_rows)


@_synchronized
def apply_style(graph, plot_type=None, columns=None, style_mode="default",
                family=None, x_title=None):
    return _apply_style_impl(graph, plot_type=plot_type, columns=columns,
                             style_mode=style_mode, family=family, x_title=x_title)


@_synchronized
def ttest(worksheet, column_a, column_b=None, kind="two", paired=False, mu=0.0):
    return _ttest_impl(worksheet, column_a=column_a, column_b=column_b,
                       kind=kind, paired=paired, mu=mu)


@_synchronized
def anova(worksheet, columns):
    return _anova_impl(worksheet, columns=columns)


@_synchronized
def pca(worksheet, columns=None, scale=False, n_components=None):
    return _pca_impl(worksheet, columns=columns, scale=scale,
                     n_components=n_components)


@_synchronized
def survival(worksheet, time_column, event_column):
    return _survival_impl(worksheet, time_column=time_column,
                          event_column=event_column)


@_synchronized
def view_graph(graph=None, max_width=1400, fmt="png"):
    return _view_graph_impl(graph=graph, max_width=max_width, fmt=fmt)


@_synchronized
def list_graphs():
    return _list_graphs_impl()


@_synchronized
def error_codes():
    return _error_codes_impl()
