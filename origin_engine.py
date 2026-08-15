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

# ---------------------------------------------------------------------------
# 常量与全局
# ---------------------------------------------------------------------------
DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "dsch_origin_plugin", "output")

_com_queue = queue.Queue()        # 任务队列：所有 Origin 操作在此串行执行
_com_thread = None                # 专用 COM 线程
_ipc_lock = None                  # 可选跨进程锁（Windows 命名互斥体）
_origin_app = None                # 惰性连接的 originpro 句柄（仅 COM 线程访问）
_connected = False

# 画图类型 -> originpro add_plot type 参数
PLOT_TYPES = {
    "line": "l",          # 折线
    "scatter": "s",       # 散点
    "line_symbol": "y",   # 线+符号
    "column": "c",        # 柱状
}

PLOT_TYPES_CN = {
    "line": "折线", "scatter": "散点", "line_symbol": "线+符号", "column": "柱状",
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
    """装饰器：公开函数 -> 投递到专用 COM 线程串行执行。"""
    def wrapper(*args, **kwargs):
        _configure_ipc_lock_if_requested()
        return _run_on_com_thread(fn, *args, **kwargs)
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
               graph_name=None, title=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app

        if plot_type not in PLOT_TYPES:
            return {"ok": False, "error": f"plot_type 必须是 {list(PLOT_TYPES)} 之一，收到 {plot_type!r}"}
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}

        if x_column is None:
            x_column = 0  # 默认第一列（列索引），与 write_data 的"第一列自动为 X"一致
        if y_columns is None:
            y_columns = _y_columns_impl(wks, x_column)

        gp = op.new_graph(lname=title or "")   # 短名自动分配（Graph1, Graph2...）
        gl = gp[0]
        plotted = []
        for yc in y_columns:
            p = gl.add_plot(wks, yc, x_column, type=PLOT_TYPES[plot_type])
            if p is None:
                return {"ok": False, "error": f"画图失败: y={yc}, x={x_column}（列不存在？）"}
            plotted.append(str(yc))
        gl.rescale()
        short_name = gp.obj.GetName()
        return {
            "ok": True,
            "graph": short_name,
            "graph_short": short_name,
            "plot_type": plot_type,
            "y_columns": plotted,
            "x_column": str(x_column),
            "detail": f"已创建图 {short_name}（{PLOT_TYPES_CN.get(plot_type, plot_type)}，{len(plotted)} 条曲线）",
        }
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


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
                    output_dir=None, x_column=None, y_columns=None, title=None):
    try:
        r1 = _write_data_impl(columns)
        if not r1.get("ok"):
            return r1
        r2 = _plot_impl(r1["worksheet"], y_columns=y_columns, x_column=x_column,
                        plot_type=plot_type, title=title)
        if not r2.get("ok"):
            return r2
        return _export_impl(r2["graph"], file_path=file_path, fmt=fmt, width=width,
                            output_dir=output_dir)
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


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
            wks = op.new_sheet("w", "Scat3D")
            wks.from_list(0, list(data["x"]), lname="X")
            wks.from_list(1, list(data["y"]), lname="Y")
            wks.from_list(2, list(data["z"]), lname="Z")
            op.po.LT_execute("plotxy iy:=(1,2,3) plot:=310;")   # 310 = 3D scatter
            gp = op.find_graph()
            if not gp:
                return {"ok": False, "error": "3D 散点图创建失败"}
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
# 查询
# ---------------------------------------------------------------------------
def _status_impl():
    ok, conn = _connect_impl()
    info = dict(conn)
    info["plot_types"] = PLOT_TYPES
    info["default_output_dir"] = DEFAULT_OUTPUT_DIR
    info["python"] = sys.executable
    return info


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
# 公开 API（线程安全：自动投递到专用 COM 线程）
# ---------------------------------------------------------------------------
@_synchronized
def connect():
    return _connect_impl()


@_synchronized
def status():
    return _status_impl()


@_synchronized
def write_data(columns, worksheet=None, book_name=None, sheet_name=None):
    return _write_data_impl(columns, worksheet=worksheet, book_name=book_name,
                            sheet_name=sheet_name)


@_synchronized
def plot(worksheet, y_columns=None, x_column=None, plot_type="line",
         graph_name=None, title=None):
    return _plot_impl(worksheet, y_columns=y_columns, x_column=x_column,
                      plot_type=plot_type, graph_name=graph_name, title=title)


@_synchronized
def export(graph, file_path=None, fmt="png", width=1200, output_dir=None):
    return _export_impl(graph, file_path=file_path, fmt=fmt, width=width,
                        output_dir=output_dir)


@_synchronized
def plot_file(columns, plot_type="line", fmt="png", file_path=None, width=1200,
              output_dir=None, x_column=None, y_columns=None, title=None):
    return _plot_file_impl(columns, plot_type=plot_type, fmt=fmt, file_path=file_path,
                           width=width, output_dir=output_dir, x_column=x_column,
                           y_columns=y_columns, title=title)


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
def list_sheets():
    return _list_sheets_impl()
