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
import json
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
# 领域模板注册表（origin_plot_template；每条都只用"真机验证过的原语"搭建）
# ---------------------------------------------------------------------------
# fit 支持的拟合类型提示（2026-09-16 补齐：此前模型名是"暗知识"）
FIT_SUPPORTED_KINDS = ["linear", "ExpDec1", "ExpGrow1", "Gauss", "Lorentz",
                       "Boltzmann", "DoseResp", "MichaelisMenten", "Logistic",
                       "Poly2"]

PLOT_TEMPLATES = {
    "stacked_spectra": {
        "desc": "多谱线纵向堆叠偏移（XPS/UV-Vis/PL/FTIR 多样品对比）",
        "data": "{'x': [..], 'spectra': {'谱线名': [..], ...}}",
        "options": "offset='auto'|数值（相邻谱线间距）；reverse_x=True 反转 X（结合能惯例）",
    },
    "xrd_pattern": {
        "desc": "XRD 三件套：Observed 散点 + Calculated 线 + Difference 下移线（可加相刻线）",
        "data": "{'two_theta': [..], 'observed': [..], 'calculated': [..], "
                "'difference': 可选, 'phases': {'相名': [2θ位置...]} 可选}",
        "options": "difference_offset='auto'（Difference 相对主谱下移量，默认 12% 谱高）",
    },
    "dual_y": {
        "desc": "双 Y 轴图（左右轴各一条序列，DUALY 模板，缺失时结构化报错）",
        "data": "{'x': [..], 'left': [..], 'right': [..], "
                "'left_name': 可选, 'right_name': 可选}",
        "options": "left_name/right_name 轴标题",
    },
    "forest": {
        "desc": "森林图：效应量点 + 置信区间横线 + 零参考线（Meta 分析/回归系数）",
        "data": "{'labels': [研究名...], 'effect': [..], 'ci_low': [..], 'ci_high': [..]}",
        "options": "zero=0（参考线位置）",
    },
    "multi_panel": {
        "desc": "多面板纵向堆叠（每面板一条序列，共享 X）",
        "data": "{'x': [..] 可选（缺省用行号）, 'panels': {'面板名': [..], ...}}",
        "options": "无",
    },
}

# ---------------------------------------------------------------------------
# 已知版本风险矩阵（origin_status.capabilities 暴露；真机探针结论数据化）
# ---------------------------------------------------------------------------
CAPABILITY_KNOWN_RISKS = [
    {"code": "plotxy_type_204_215", "severity": "high", "versions": ["2026b"],
     "note": "LabTalk plotxy 图型代码 204/215 可能渲染成面积图或不出图",
     "workaround": "box/bar 已内置改走 Origin 官方模板；请勿直接传 plotxy 代码"},
    {"code": "multi_origin_instances", "severity": "high", "versions": ["all"],
     "note": "多个 Origin64 进程会让 COM 连接错实例（LT_execute 报异常）",
     "workaround": "只保留一个主实例；origin_status 返回进程数警告"},
    {"code": "com_thread_affinity", "severity": "medium", "versions": ["all"],
     "note": "comtypes/OriginExt 的 COM 接口指针有线程亲和性，跨线程调用报"
             "「对象没有连接到服务器」",
     "workaround": "引擎已用专用 COM 线程串行化，调用方无需处理"},
    {"code": "modal_dialog_blocks_com", "severity": "medium", "versions": ["all"],
     "note": "Origin 弹出模态对话框时 COM 调用会挂起",
     "workaround": "自动化期间不要手动操作 Origin；关闭对话框后重试"},
    {"code": "dualy_template_local", "severity": "low", "versions": ["all"],
     "note": "双 Y 模板名因版本而异（2026 实测 doubley/righty 可用，dualy 不存在），"
             "origin_plot_template=dual_y 会按 dualy/doubley/righty 顺序探测",
     "workaround": "全部不可用时改用 origin_plot 双序列或 stacked_spectra"},
]

_ORIGIN_VERSION_LABELS = {10.15: "Origin 2024b"}


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


def _isolated_session_blocked():
    """ORIGIN_SESSION=isolated 时的隔离守卫：已有 Origin 在运行则拒绝连接。

    设计动机：默认 attach 模式复用已运行的 Origin（快），但会"劫持"用户手动
    打开的 Origin 窗口；隔离模式宁可拒绝也不碰用户会话，由用户决定关闭 Origin
    还是切回 attach。返回 None 表示放行，返回 dict 表示结构化拒绝。
    """
    if os.environ.get("ORIGIN_SESSION", "attach").lower() != "isolated":
        return None
    nproc = _origin_proc_count()
    if nproc:
        return oerr.fail(
            "origin_busy_user_session",
            f"隔离会话模式（ORIGIN_SESSION=isolated）检测到 {nproc} 个 Origin64 进程"
            "正在运行，为避免劫持用户手动打开的 Origin，已停止连接。",
            origin_processes=nproc)
    return None


def _lt_read_float(expr):
    """读取 LabTalk 数值表达式（项目变量中转，originpro/OriginExt 双通道）。

    只允许在 COM 线程内调用。读不到返回 None（调用方决定 unreadable 语义）。
    """
    op = _origin_app
    if op is None:
        return None
    try:
        op.po.LT_execute(f"double __dshv = {expr};")
    except Exception:
        return None
    try:
        v = op.lt_float("__dshv")
        if v is not None:
            return float(v)
    except Exception:
        pass
    try:
        v = op.po.LT_get_var("__dshv")
        if v is not None:
            return float(v)
    except Exception:
        pass
    return None


def _lt_read_str(expr):
    """读取 LabTalk 字符串表达式（项目变量中转）。读不到返回 None。"""
    op = _origin_app
    if op is None:
        return None
    try:
        op.po.LT_execute('__dshvs$ = %s;' % expr)
    except Exception:
        return None
    try:
        v = op.lt_str("__dshvs$")
        if v is not None:
            return str(v)
    except Exception:
        pass
    try:
        v = op.po.LT_get_str("__dshvs$")
        if isinstance(v, tuple) and v:
            v = v[0]
        if v is not None:
            return str(v)
    except Exception:
        pass
    return None


def _ensure_active_graph(graph=None):
    """激活目标图页并复核（LabTalk 通道的前提）。

    真机探针（probe6）证明：LabTalk 裸表达式只在目标图页恰为活动窗口时解析，
    否则静默返回 NaN 或落到别的窗口。返回 (short_name, error_payload|None)。
    """
    op = _origin_app
    if op is None:
        return None, oerr.fail("connection_error", "未连接 Origin")
    import origin_edit as oedit
    return oedit.ensure_active_graph(op, op.po, graph)


def _lt_write_checked(script, read_expr=None, expect=None, read_fn=None):
    """LabTalk 写入 + 读回校验：返回 (ok, readback, note)。

    - 先复核激活（否则直接判失败，避免"静默落到别的窗口"）；
    - read_fn 优先（COM 读回通道，如 axis.title），否则用 read_expr 走 LabTalk；
    - 读不到即判失败（宁可报未生效，也不谎报成功）。
    """
    short, err = _ensure_active_graph()
    if err is not None:
        return False, None, f"激活复核失败: {err.get('error')}"
    try:
        _origin_app.po.LT_execute(script)
    except Exception as e:
        return False, None, f"LabTalk 执行异常: {e}"
    v = None
    if read_fn is not None:
        v, verr = safe_call(read_fn)
        if verr is not None:
            v = None
    if v is None and read_expr:
        v = _lt_read_float(read_expr)
        if v is None:
            v = _lt_read_str(read_expr)
    if v is None:
        return False, None, "写入后读回失败（通道不支持读回，或未落到目标图页）"
    # NaN 是"读到了空值"，绝不能当作校验通过（NaN 比较恒为 False，会假成功）
    try:
        import math as _math
        if isinstance(v, float) and _math.isnan(v):
            return False, None, "读回为 NaN（该 LabTalk 通道在当前图层/版本下无效），判未生效"
    except Exception:
        pass
    if expect is not None:
        try:
            if abs(float(v) - float(expect)) > 1e-6:
                return False, v, f"读回 {v} 与期望 {expect} 不符"
        except (TypeError, ValueError):
            if str(v).strip() != str(expect).strip():
                return False, v, f"读回 {v!r} 与期望 {expect!r} 不符"
    return True, v, None


def _origin_proc_count():
    """统计 Origin64 进程数（>1 说明存在多实例，需清理以免 COM 连错实例）。"""
    try:
        out = os.popen('tasklist /FI "IMAGENAME eq Origin64.exe" /NH 2>nul').read()
        return sum(1 for line in out.splitlines() if "Origin64.exe" in line)
    except Exception:
        return None


def _connect_impl():
    global _connected, _origin_app
    blocked = _isolated_session_blocked()
    if blocked is not None:
        return False, blocked
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


def safe_call(fn, *args, **kwargs):
    """调用并容错：返回 (result, error_str|None)。"""
    try:
        return fn(*args, **kwargs), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _set_layer_geometry(gl, left=None, top=None, width=None, height=None):
    """以 %页 单位设置图层几何（COM 作用域读写，窗口无关），返回逐项说明。"""
    notes = []
    try:
        gl.set_int("unit", 1)          # 1 = %page
    except Exception:
        pass
    for key, val in (("left", left), ("top", top),
                     ("width", width), ("height", height)):
        if val is None:
            continue
        ok = False
        for setter in (lambda k=key, v=val: gl.set_float(k, float(v)),
                       lambda k=key, v=val: gl.set_int(k, int(round(float(v))))):
            _, err = safe_call(setter)
            if err is None:
                ok = True
                break
        back, _ = safe_call(gl.get_float, key)
        if back is None:
            back, _ = safe_call(gl.get_int, key)
        hit = ok and isinstance(back, (int, float)) and abs(float(back) - float(val)) <= 0.05
        notes.append(f"{key}={val}%{'✔' if hit else '✗'}"
                     + (f"(读回 {back})" if not hit and back is not None else ""))
    return notes


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
               style_mode=None, family=None, style_overrides=None):
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
                                      family=family, x_title="Bin center",
                                      style_overrides=style_overrides)
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
                                  family=family, x_title=x_title,
                                  style_overrides=style_overrides)
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
        if fmt == "tiff":
            fmt = "tif"
        if fmt not in ("png", "svg", "pdf", "tif", "emf"):
            return {"ok": False,
                    "error": f"fmt 只支持 png/svg/pdf/tif/emf，收到 {fmt!r}"}

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
                    graph_name=None, style_mode=None, family=None,
                    style_overrides=None):
    try:
        r1 = _write_data_impl(columns)
        if not r1.get("ok"):
            return r1
        r2 = _plot_impl(r1["worksheet"], y_columns=y_columns, x_column=x_column,
                        plot_type=plot_type, title=title, graph_name=graph_name,
                        style_mode=style_mode, family=family,
                        style_overrides=style_overrides)
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
              graph=None, title=None, drop_report_pages=True):
    """拟合：linear（线性）或 Origin 内置拟合函数名（如 ExpDec1/Gauss/...）。

    supported_kinds（2026-09-16 补齐，此前模型名是"暗知识"只能蒙）：
    本机 Origin 2026 常见可用 NLFit 名：ExpDec1 / ExpGrow1 / Gauss / Lorentz /
    Boltzmann / DoseResp / MichaelisMenten / Logistic / Poly2（以 Origin 内置
    函数目录为准，未知名会返回可用列表）。

    drop_report_pages=True（默认）：自动关闭 NLFit 产生的 FitLine*/Residual*
    报告副产品页面 —— 参数与报告已在返回值里，页面留着会爆窗口
    （2026-09-16 实测 7 次 fit 多开 14 页）。

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
            "supported_kinds": FIT_SUPPORTED_KINDS,
            "detail": f"{fit_kind} 拟合完成，参数见 parameters",
        }

        # NLFit 报告副产品页面清理（FitLine*/Residual*：参数已在返回值里，
        # 页面留着会爆窗口 —— 2026-09-16 实测 7 次 fit 多开 14 页）
        removed_pages = []
        if drop_report_pages:
            try:
                for pn in _page_names():
                    if pn not in pages_before and (
                            pn.startswith("FitLine") or pn.startswith("Residual")
                            or "Report" in pn):
                        if _delete_graph_page(pn):
                            removed_pages.append(pn)
            except Exception:
                pass
        result["removed_report_pages"] = removed_pages

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

    surface 的 data（两种写法都会自动识别行列方向）:
      {"z": [[...],...]}                        隐式网格（自动生成 X/Y 索引）
      {"x": [...], "y": [...], "z": [[...],...]} 显式网格；z[行][列] 行对应 y、
                                                列对应 x，AI 直觉的行=x 写法
                                                也会自动转置（2026-09-16 修复）
    scatter 的 data: {"x": [...], "y": [...], "z": [...]}（三个等长一维列表）
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
                return oerr.fail(
                    "invalid_request",
                    "z 必须是二维网格（列表的列表）。"
                    "隐式网格: {'z': [[...],...]}；显式网格: {'x':[...], 'y':[...], "
                    "'z': [[...],...]}，其中 z[行][列] 的行对应 y、列对应 x")
            ny, nx = z2d.shape
            if "x" in data and "y" in data:
                xvec = np.asarray(data["x"], dtype=float).ravel()
                yvec = np.asarray(data["y"], dtype=float).ravel()
                # 形状自适应（2026-09-16 c26 教训）：AI/人直觉写法是 z[行]=x 方向，
                # 而 meshgrid(x, y) 产出 (len(y), len(x))。两种写法都接受，
                # 形状不匹配时友好报错而不是 numpy 底层 "inhomogeneous shape"。
                if z2d.shape == (len(yvec), len(xvec)):
                    pass                                  # 行=y 列=x（标准）
                elif z2d.shape == (len(xvec), len(yvec)):
                    z2d = z2d.T                           # 行=x 列=y → 转置
                    ny, nx = z2d.shape
                else:
                    return oerr.fail(
                        "invalid_request",
                        f"z 形状 {list(z2d.shape)} 与 x({len(xvec)})/y({len(yvec)}) "
                        f"不匹配：z 应为 [len(y)][len(x)] 或 [len(x)][len(y)]"
                        "（两种行列方向都会自动识别）",
                        got={"z_shape": list(z2d.shape),
                             "len_x": len(xvec), "len_y": len(yvec)})
                gx, gy = np.meshgrid(xvec, yvec)
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
                    new_x=None, write_back=True, return_values=True):
    """数据变换：smooth | normalize | derivative | interpolate |
    ln | log10 | reciprocal | exp | sqrt | abs（后五者为 2026-09-16 补齐：
    动力学 ln[A]-t、Arrhenius 1/T、二级 1/[A] 是化学高频路径，此前只能
    让 AI 自算回写，多两跳且易错）。

    return_values=True（默认）时结果 ≤2000 点直接随返回回传（values 字段），
    可直接喂给 plot_template 等内存数据接口，免二次 read_worksheet。
    """
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
        n_bad = 0
        hint = ""
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
                    "values": [round(float(b), 8) for b in out[:2000]]
                    if return_values and nx.size <= 2000 else None,
                    "detail": f"插值完成 -> 新列 {new_col}（{nx.size} 点）"}
        elif op_name in ("ln", "log10", "reciprocal", "exp", "sqrt", "abs"):
            with np.errstate(divide="ignore", invalid="ignore"):
                if op_name == "ln":
                    out = np.log(v)
                elif op_name == "log10":
                    out = np.log10(v)
                elif op_name == "reciprocal":
                    out = 1.0 / v
                elif op_name == "exp":
                    out = np.exp(v)
                elif op_name == "sqrt":
                    out = np.sqrt(np.where(v < 0, np.nan, v))
                else:
                    out = np.abs(v)
            n_bad = int(np.size(out) - np.isfinite(out).sum())
            hint = (f"（{n_bad} 个点无效，多为对 0/负数取对数或除零——"
                    "先用 filter_data 清洗）" if n_bad else "")
        else:
            return {"ok": False, "error": f"op 必须是 smooth/normalize/derivative/interpolate/ln/log10/reciprocal/exp/sqrt/abs，收到 {op_name!r}"}

        if write_back:
            new_col = _write_col_impl(wks, out,
                                      lname=f"{_col_name_impl(wks, ci)}_{op_name}")
        else:
            new_col = None
        n_out = int(np.size(out))
        return {"ok": True, "worksheet": str(wks), "new_column": new_col,
                "points": n_out, "op": op_name,
                "values": [None if not np.isfinite(b) else round(float(b), 8)
                           for b in out[:2000]]
                if return_values and n_out <= 2000 else None,
                "invalid_points": n_bad if op_name in ("ln", "log10", "reciprocal",
                                                       "exp", "sqrt", "abs") else 0,
                "detail": f"{op_name} 完成" + (f"，结果写入新列 {new_col}" if new_col else "") + hint}
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


def _col_name_impl(wks, ci):
    try:
        return wks.obj[ci].GetLongName() or wks.obj[ci].GetName()
    except Exception:
        return str(ci)


def _integrate_impl(worksheet, x_column=0, y_column=1, baseline=None):
    """数值积分（梯形法），返回曲线下面积 AUC。

    baseline（2026-09-16 补齐，DSC 焓变等需要扣基线）：
      None      不扣（原行为，纯 AUC）；
      "min"     以 y 最小值为基线扣除（快速近似）；
      数值      以给定常数扣除；
      "first"   以首点 y 值扣除（平稳基线起点近似）。
    """
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
        base_desc, base_val = "未扣除", 0.0
        if baseline is not None:
            if isinstance(baseline, str) and baseline.lower() == "min":
                base_val = float(np.nanmin(yv[mask]))
                base_desc = f"min(y)={base_val:.6g}"
            elif isinstance(baseline, str) and baseline.lower() == "first":
                base_val = float(yv[mask][0])
                base_desc = f"y(首点)={base_val:.6g}"
            else:
                try:
                    base_val = float(baseline)
                    base_desc = f"常数 {base_val:.6g}"
                except (TypeError, ValueError):
                    return {"ok": False,
                            "error": "baseline 取 None/'min'/'first' 或数值"}
        auc = float(np.trapezoid(yv[mask] - base_val, xv[mask]))
        return {"ok": True, "worksheet": str(wks), "auc": auc,
                "baseline": base_desc,
                "x_column": str(x_column), "y_column": str(y_column),
                "points": int(mask.sum()),
                "detail": f"曲线下面积 AUC = {auc:.6g}（梯形法，"
                          f"{int(mask.sum())} 点，基线：{base_desc}）"}
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
                    fmt="png", width=1200, color=None):
    """直方图：返回 bin 区间与频数；plot=True 时画柱状图并导出。

    color（2026-09-16 补齐）："#RRGGBB" 或 Origin 调色板色名，默认接入当前
    调色板体系首色（此前为纯黑默认，与插件配色纪律脱节）。
    """
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
            # 柱色：显式 color > 调色板首色（P3：不再纯黑默认）
            try:
                import plot_style as _pst
                if color:
                    rgb = _hex_to_rgb_tuple(str(color))
                else:
                    rgb = _hex_to_rgb_tuple(
                        _pst.choose_palette(1)["colors"][0])
                for p_ in (gl.plot_list() or []):
                    p_.color = rgb
            except Exception:
                pass
            try:
                for ax_, tx_ in (("x", str(column)), ("y", "Count")):
                    import origin_edit as _oedit
                    _oedit.set_axis_title_checked(gl, ax_, tx_, po=op.po)
            except Exception:
                pass
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
    info["templates"] = {k: v["desc"] for k, v in PLOT_TEMPLATES.items()}
    info["default_output_dir"] = DEFAULT_OUTPUT_DIR
    info["python"] = sys.executable
    try:
        info["capabilities"] = _capabilities_impl()
    except Exception as e:
        info["capabilities"] = {"error": f"能力探测失败: {e}"}
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
            "origin_status": "检查连接 + 版本能力握手（known_risks/features，Origin 未启动会自动启动）",
            "origin_write_data": "写多列数据 -> 返回 worksheet",
            "origin_load_file": "导入本地表格文件（CSV/TXT/XLSX/XLS，中文路径/编码安全）-> 返回 worksheet+列画像",
            "origin_plot": "画图（含 histogram/box/bar + yerr_column 误差棒 + style_overrides 显式样式）-> 返回 graph",
            "origin_export": "导出 PNG/SVG/PDF/TIF/EMF -> 返回 file 绝对路径",
            "origin_plot_file": "一键 写数+画图+导出 -> 返回 file（最常用）",
            "origin_plot_plan": "绘图计划（离线秒回）：逐列画像+角色建议+元素清单+待确认问题 -> plan_id",
            "origin_execute_plan": "按 plan_id 执行计划（写数+画图+导出），不确定列应先经用户确认",
            "origin_plot_template": "领域模板：stacked_spectra/xrd_pattern/dual_y/forest/multi_panel",
            "origin_verify_graph": "确定性反读核验（轴标题/字号/图层几何/图例/文件完整性），与 view_graph 互补",
            "origin_save_project": "保存当前项目为可编辑 OPJU",
            "origin_export_delivery": "一键交付：源文件同级建 <数据名>_Origin_<时间戳>/ 收纳图片+OPJU 并核验",
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
            "origin_apply_style": "对已有图应用排版/调色板/多序列区分（style_mode/family/style_overrides）",
            "origin_list_pages": "列出全部页面（图页/工作簿/矩阵）+ 当前活动窗口",
            "origin_inspect_graph": "巡检图页现状（几何/曲线样式/轴/图例/页面 cm）——改图前先看",
            "origin_edit_plot": "逐条微调曲线：颜色/线宽/线型/符号/透明度/显示隐藏（每项带读回）",
            "origin_edit_axis": "微调轴：标题/范围/刻度类型/网格/刻度长度/标签字号加粗",
            "origin_edit_legend": "微调图例：显示隐藏/字号/边框/背景/四角锚点/文本",
            "origin_edit_page": "纸张 cm 尺寸/页面背景/图层位置与大小（%页）",
            "origin_manage_pages": "窗口管理：关闭/激活/重命名/隐藏/显示/复制页面",
            "origin_add_text": "添加文本标注（峰位/条件说明）",
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
            "文件导入: origin_load_file(path='D:/data/样品1.csv') -> origin_plot(worksheet,...)",
            "确认流: origin_plot_plan(columns,...) -> （有 questions 先问用户）-> origin_execute_plan(plan_id)",
            "多谱线堆叠: origin_plot_template('stacked_spectra', {'x':[..], 'spectra':{...}})",
            "XRD 三件套: origin_plot_template('xrd_pattern', {'two_theta':[..], 'observed':[..], 'calculated':[..], 'difference':[..]})",
            "双Y轴: origin_plot_template('dual_y', {'x':[..], 'left':[..], 'right':[..]})",
            "森林图: origin_plot_template('forest', {'labels':[..], 'effect':[..], 'ci_low':[..], 'ci_high':[..]})",
            "一键交付: origin_export_delivery(graph, source_path='数据.csv', fmts='png,pdf') -> 图片+OPJU 目录",
            "微调改图: origin_inspect_graph(graph) 看现状 -> origin_edit_plot/edit_axis/edit_legend/edit_page 改 -> origin_view_graph 看效果",
            "关窗口: origin_list_pages() 取短名 -> origin_manage_pages('close', pages=['Book3','Book4'])",
        ],
        "tips": [
            "所有工具返回 JSON；ok=false 时读 error_code / recoverable / next_actions 安全分支重试",
            "画图可用 style_mode=default|journal|presentation 与 family=调色板家族 提升排版",
            "需视觉校验时调用 origin_view_graph（模型看图）；需程序核验时 origin_verify_graph（对象反读）",
            "细粒度改动逐项回报 applied/applied_unverified/rejected；applied_unverified（如线宽）需目视确认",
            "LabTalk 类操作只在活动窗口内解析：返回 window_activation_failed 时先用 origin_manage_pages 激活图页",
            "科学边界：不虚构/不补数据；不确定列先问；派生列标注 derived；不静默拟合/平滑/归一化",
            "origin_status 的 capabilities.known_risks 是版本坑清单（如 plotxy 204/215 在 2026b）",
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


def _apply_style_overrides(gl, plots, short_name, overrides):
    """显式样式覆盖：逐项 applied / kept_default / rejected，绝不静默丢弃。

    支持字段（真机验证可靠）：series_colors / line_width_pt / x_title / y_title；
    其余字段（legend_*、page_size_cm 等）在真机探针验证写回 API 之前明确拒绝，
    并说明原因 —— 与"未验证不落图"的项目原则一致。
    """
    decisions = []
    if not overrides:
        return decisions
    if not isinstance(overrides, dict):
        return [{"field": "style_overrides", "decision": "rejected",
                 "reason": f"必须是 dict，收到 {type(overrides).__name__}"}]
    for key, val in overrides.items():
        key = str(key)
        if val is None:
            decisions.append({"field": key, "decision": "kept_default",
                              "reason": "未提供值，保持模板默认"})
            continue
        try:
            if key == "series_colors":
                if not isinstance(val, (list, tuple)) or not val:
                    raise ValueError("series_colors 必须是非空颜色列表")
                hexes = [str(v) for v in val]
                applied = 0
                for i, p in enumerate(plots):
                    if i >= len(hexes):
                        break
                    p.color = _hex_to_rgb_tuple(hexes[i])
                    applied += 1
                decisions.append({"field": key, "decision": "applied",
                                  "reason": f"已按顺序应用 {applied} 个序列颜色",
                                  "values": hexes[:applied]})
            elif key == "line_width_pt":
                lw = float(val)
                if lw <= 0:
                    raise ValueError("线宽必须为正")
                ok_attr = None
                for attr in ("linewidth", "line_width"):
                    try:
                        for p in plots:
                            setattr(p, attr, lw)
                        ok_attr = attr
                        break
                    except Exception:
                        continue
                if ok_attr:
                    decisions.append({"field": key, "decision": "applied",
                                      "reason": f"经 plot.{ok_attr} 写入 {lw}pt",
                                      "value": lw})
                else:
                    decisions.append({
                        "field": key, "decision": "rejected",
                        "reason": "当前 Origin 运行时不支持线宽属性写入，"
                                  "已拒绝以免错误渲染（可在 OPJU 中手动调整）"})
            elif key in ("x_title", "y_title"):
                ax = "x" if key == "x_title" else "y"
                gl.axis(ax).title = str(val)
                decisions.append({"field": key, "decision": "applied",
                                  "reason": f"{ax} 轴标题已写回并可靠落图",
                                  "value": str(val)})
            else:
                decisions.append({
                    "field": key, "decision": "rejected",
                    "reason": "该字段的 Origin 写回 API 尚未真机验证，明确拒绝"
                              "而非静默忽略；当前支持：series_colors / "
                              "line_width_pt / x_title / y_title"})
        except Exception as e:
            decisions.append({"field": key, "decision": "rejected",
                              "reason": f"应用失败: {e}"})
    return decisions


def _apply_style_impl(graph, plot_type=None, columns=None, style_mode="default",
                      family=None, x_title=None, style_overrides=None,
                      apply_axis_titles=True):
    """应用默认排版规则：调色板 + 多序列区分 + 语义轴标题（真机验证可靠）。

    返回 {ok, applied, applied_ops, style_plan}：每一步都给 reason，方便排查。
    仅当多序列或显式指定 style_mode/family 时改色；单序列保持 Origin 默认。
    apply_axis_titles=False 用于模板分支（dual_y/xrd/stacked/multi_panel 自己已
    设置语义标题，推断标题不得覆盖 —— 2026-09-16 c21/c10 标题损坏根因）。
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
        if apply_axis_titles and isinstance(style_plan["axis_titles"].get("y"), dict):
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
        style_decisions = _apply_style_overrides(gl, plots, graph, style_overrides)
        return oerr.ok(applied=True, applied_ops=applied_ops, style_plan=style_plan,
                       style_decisions=style_decisions)
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
# 文件导入（P0）：本地表格 -> Origin 工作表
# ---------------------------------------------------------------------------
def _load_file_impl(path, worksheet=None, sheet=None, max_preview_rows=5):
    try:
        import origin_fileio as fio
        r = fio.read_table(path, sheet=sheet)
        if not r.get("ok"):
            return r
        w = _write_data_impl(r["columns"], worksheet=worksheet)
        if not w.get("ok"):
            return w
        n_preview = max(0, int(max_preview_rows or 0))
        return oerr.ok(
            worksheet=w["worksheet"], columns=w["columns"], rows=w["rows"],
            source=r["path"], file_type=r.get("file_type"),
            encoding=r.get("encoding"), sheet=r.get("sheet"),
            column_types=r.get("column_types"),
            preview_rows=(r.get("preview_rows") or [])[:n_preview],
            file_n_rows=r.get("n_rows"), warning=r.get("warning"),
            detail=(f"已从 {os.path.basename(str(r['path']))} 导入 "
                    f"{w['columns'].__len__()} 列 x {w['rows']} 行 -> {w['worksheet']}；"
                    "可用 origin_plot / origin_plot_file 继续画图，"
                    "或 origin_plot_plan 生成确认计划"))
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


# ---------------------------------------------------------------------------
# OPJU 项目保存 + 交付目录（P0）：可编辑工程是一等交付物
# ---------------------------------------------------------------------------
def _save_project_impl(path):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        if not path:
            return oerr.fail("invalid_request", "path 不能为空")
        path = os.path.abspath(str(path))
        if not path.lower().endswith((".opju", ".ogg", ".opj")):
            path += ".opju"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        saved_ok = False
        try:
            saved_ok = bool(op.save(path))     # originpro 官方 API：另存当前项目
        except Exception:
            saved_ok = False
        if not saved_ok or not os.path.exists(path):
            # 兜底 1：先存 ASCII 临时路径再搬运（绕开 COM/LabTalk 的中文路径差异）
            try:
                import shutil
                import tempfile as _tf
                tmp = os.path.join(_tf.mkdtemp(prefix="dsh_opju_"), "proj.opju")
                if op.save(tmp) and os.path.exists(tmp):
                    shutil.move(tmp, path)
                    saved_ok = os.path.exists(path)
            except Exception:
                saved_ok = False
        if not saved_ok or not os.path.exists(path):
            # 兜底 2：LabTalk save（正斜杠路径）
            try:
                op.po.LT_execute('save "%s";' % path.replace("\\", "/"))
                saved_ok = os.path.exists(path)
            except Exception:
                saved_ok = False
        if not saved_ok or not os.path.exists(path):
            return oerr.fail("export_error",
                             "OPJU 保存失败（op.save / 临时搬运 / LabTalk save 均未落盘）",
                             path=path)
        return oerr.ok(file=path, size=os.path.getsize(path),
                       detail=(f"项目已保存 -> {path}"
                               "（保存的是当前项目全部页面，可在 Origin 中继续编辑）"))
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _export_delivery_impl(graph, source_path=None, output_dir=None, fmts="png,pdf",
                          width=1200, save_opju=True):
    """一键交付：源文件同级建 <数据名>_Origin_<时间戳>/ 目录，
    导出多格式图片 + OPJU，并逐文件核验完整性。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        fmt_list = [f.strip().lower().lstrip(".") for f in str(fmts).split(",")
                    if f.strip()]
        fmt_list = ["tif" if f == "tiff" else f for f in fmt_list]
        bad = [f for f in fmt_list if f not in ("png", "svg", "pdf", "tif", "emf")]
        if bad:
            return oerr.fail("invalid_request", f"不支持的导出格式: {bad}",
                             supported=["png", "svg", "pdf", "tif", "emf"])
        gp = op_find_graph(graph)
        if not gp:
            return oerr.fail("graph_not_found", f"图不存在: {graph}", graph=str(graph))
        ts = time.strftime("%Y%m%d_%H%M%S")
        if source_path:
            src = os.path.abspath(str(source_path))
            stem = re.sub(r"[^\w\-.]", "_",
                          os.path.splitext(os.path.basename(src))[0])
            ddir = os.path.join(os.path.dirname(src), f"{stem}_Origin_{ts}")
        elif output_dir:
            ddir = os.path.abspath(str(output_dir))
        else:
            ddir = os.path.join(DEFAULT_OUTPUT_DIR, f"delivery_{ts}")
        os.makedirs(ddir, exist_ok=True)
        safe_g = re.sub(r"[^\w\-.]", "_", str(graph))
        files, issues = [], []
        for fmt in fmt_list:
            r = _export_impl(graph, file_path=os.path.join(ddir, f"{safe_g}.{fmt}"),
                             fmt=fmt, width=width)
            if r.get("ok"):
                entry = {"format": fmt, "file": r["file"], "size": r["size"],
                         "ok": True}
                if not os.path.exists(r["file"]) or r["size"] == 0:
                    entry["ok"] = False
                    issues.append(f"{fmt} 文件为空: {r['file']}")
            else:
                entry = {"format": fmt, "ok": False, "error": r.get("error")}
                issues.append(f"{fmt} 导出失败: {r.get('error')}")
            files.append(entry)
        opju = None
        if save_opju:
            r2 = _save_project_impl(os.path.join(ddir, f"{safe_g}_{ts}.opju"))
            if r2.get("ok"):
                opju = r2["file"]
            else:
                issues.append(f"OPJU 保存失败: {r2.get('error')}")
        n_ok = sum(1 for f in files if f.get("ok"))
        return oerr.ok(
            delivery_dir=ddir, files=files, opju=opju, issues=issues,
            all_ok=(not issues),
            detail=(f"交付目录 {ddir}（图片 {n_ok}/{len(files)} + "
                    f"{'OPJU' if opju else '无 OPJU'}）"
                    + ("；存在问题：" + "; ".join(issues) if issues else "")))
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def op_find_graph(graph):
    """COM 线程内的图页查找（供交付/验证路径复用）。"""
    try:
        return _origin_app.find_graph(graph)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 确定性反读验证（P0）：与 origin_view_graph 组成"程序核 + 模型看"双保险
# ---------------------------------------------------------------------------
def _verify_graph_impl(graph=None, expected_x_title=None, expected_y_title=None,
                       min_font_pt=None, expected_series=None,
                       legend_visible=None, files=None,
                       allow_full_overlap=False):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        target = graph or _active_graph_shortname()
        if not target:
            return oerr.fail("graph_not_found", "未指定图名且没有活动图页")
        expected = {}
        if expected_x_title:
            expected["x_title"] = str(expected_x_title)
        if expected_y_title:
            expected["y_title"] = str(expected_y_title)
        if min_font_pt is not None:
            expected["min_font_pt"] = float(min_font_pt)
        if expected_series is not None:
            expected["series"] = int(expected_series)
        if legend_visible is not None:
            expected["legend_visible"] = bool(legend_visible)
        # 双 Y 类共享绘图区布局（dual_y/doubley）传 True，层间完全重叠判 pass
        expected["allow_full_overlap"] = bool(allow_full_overlap)
        import origin_verify as ovf
        return ovf.verify_graph(op, op.po, target, expected=expected,
                                files=files)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


# ---------------------------------------------------------------------------
# 领域模板（P1）：全部用真机验证过的原语组合（行号X/NaN断线/2点线/模板图型）
# ---------------------------------------------------------------------------
def _validate_template_data(template_id, data):
    """参数校验（在连接 Origin 之前调用，离线可测）。返回 (True, None) 或 (None, err)。"""
    if template_id not in PLOT_TEMPLATES:
        return None, oerr.fail("invalid_request", f"未知 template_id: {template_id!r}",
                               valid_templates=sorted(PLOT_TEMPLATES))
    if not isinstance(data, dict):
        return None, oerr.fail("invalid_request", "data 必须是 dict",
                               template=template_id, received_type=type(data).__name__)
    t = template_id

    def need_list(key):
        v = data.get(key)
        return isinstance(v, (list, tuple)) and len(v) > 0

    if t == "stacked_spectra":
        if not need_list("x") or not isinstance(data.get("spectra"), dict) \
                or not data.get("spectra"):
            return None, oerr.fail(
                "invalid_request",
                "stacked_spectra 需要 data={'x': [..], 'spectra': {'谱线名': [..], ...}}",
                template=t, received_keys=sorted(data))
        nx = len(data["x"])
        for k, v in data["spectra"].items():
            if not isinstance(v, (list, tuple)) or len(v) != nx:
                got = len(v) if isinstance(v, (list, tuple)) else type(v).__name__
                return None, oerr.fail(
                    "invalid_request",
                    f"谱线 {k!r} 长度必须与 x 一致（x={nx}，收到 {got}）",
                    template=t, spectrum=str(k))
    elif t == "xrd_pattern":
        for k in ("two_theta", "observed", "calculated"):
            if not need_list(k):
                return None, oerr.fail(
                    "invalid_request", f"xrd_pattern 需要 data['{k}'] 为非空列表",
                    template=t, received_keys=sorted(data))
        n = len(data["two_theta"])
        for k in ("observed", "calculated", "difference"):
            if k in data and len(data[k]) != n:
                return None, oerr.fail(
                    "invalid_request", f"{k} 长度必须与 two_theta 一致", template=t)
        ph = data.get("phases")
        if ph is not None and not (isinstance(ph, dict) and all(
                isinstance(v, (list, tuple)) for v in ph.values())):
            return None, oerr.fail(
                "invalid_request", "phases 必须是 {'相名': [2θ位置...]}",
                template=t)
    elif t == "dual_y":
        for k in ("x", "left", "right"):
            if not need_list(k):
                return None, oerr.fail("invalid_request",
                                       f"dual_y 需要 data['{k}'] 为非空列表",
                                       template=t, received_keys=sorted(data))
        n = len(data["x"])
        for k in ("left", "right"):
            if len(data[k]) != n:
                return None, oerr.fail("invalid_request",
                                       f"{k} 与 x 长度不一致", template=t)
    elif t == "forest":
        for k in ("labels", "effect", "ci_low", "ci_high"):
            if not need_list(k):
                return None, oerr.fail("invalid_request",
                                       f"forest 需要 data['{k}'] 为非空列表",
                                       template=t, received_keys=sorted(data))
        n = len(data["labels"])
        for k in ("effect", "ci_low", "ci_high"):
            if len(data[k]) != n:
                return None, oerr.fail("invalid_request",
                                       f"{k} 与 labels 长度不一致", template=t)
    elif t == "multi_panel":
        if not isinstance(data.get("panels"), dict) or not data.get("panels"):
            return None, oerr.fail(
                "invalid_request",
                "multi_panel 需要 data={'x': [..] 可选, 'panels': {'面板名': [..], ...}}",
                template=t, received_keys=sorted(data))
    return True, None


def _plot_template_impl(template_id, data, graph_name=None, title=None,
                        style_mode="default", family=None, offset="auto",
                        reverse_x=False, fmt=None, file_path=None, width=1200,
                        x_title=None, y_title=None, gradient=False):
    try:
        import numpy as np
        # 参数校验前置：离线可测，校验失败绝不拉起 Origin
        okv, errv = _validate_template_data(template_id, data)
        if not okv:
            return errv
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        t = template_id

        def _flist(vals):
            return [float(v) if v is not None else float("nan") for v in vals]

        if t == "stacked_spectra":
            x = _flist(data["x"])
            spectra = {str(k): _flist(v) for k, v in data["spectra"].items()}
            if reverse_x:
                x = x[::-1]
                spectra = {k: v[::-1] for k, v in spectra.items()}
            names = list(spectra)
            if str(offset).lower() == "auto":
                spans = []
                for v in spectra.values():
                    arr = np.asarray([b for b in v if np.isfinite(b)])
                    if arr.size:
                        spans.append(float(arr.max() - arr.min()))
                step = 1.2 * (max(spans) if spans else 1.0)
            else:
                step = float(offset)
            cols = {"x": x}
            offsets = {}
            for i, nm in enumerate(names):
                offsets[nm] = round(i * step, 6)
                cols[nm] = [b + i * step for b in spectra[nm]]
            w = _write_data_impl(cols)
            if not w.get("ok"):
                return w
            wsobj = op.find_sheet("w", w["worksheet"])
            lname = _ensure_graph_name(graph_name, title or "StackedSpectra")
            gp = op.new_graph(lname=lname)
            gl = gp[0]
            for i in range(1, len(cols)):
                gl.add_plot(wsobj, i, 0, type="l")
            gl.rescale()
            short = gp.obj.GetName()
            style = _apply_style_impl(short, plot_type="line", columns=names,
                                      style_mode=style_mode, family=family,
                                      apply_axis_titles=False)
            # 轴标题：显式覆盖 > 模板默认；写后读回验证（防 (%)/θ 类字符坑）
            import origin_edit as _oedit
            for ax, txt in (("x", x_title), ("y", y_title or "Intensity (a.u.)")):
                if txt:
                    _oedit.set_axis_title_checked(gl, ax, txt, po=op.po)
            # 渐变色（P3）：按系列顺序在调色板首尾色之间线性插值，
            # 让温度/浓度序列的层叠关系一眼可读
            grad_note = None
            if gradient and len(names) >= 3:
                try:
                    pal = style.get("style_plan", {}).get("palette", {}).get(
                        "colors") if isinstance(style, dict) else None
                    import plot_style as _pst
                    if not pal:
                        pal = _pst.choose_palette(len(names), family=family)["colors"]
                    c0, c1 = _hex_to_rgb_tuple(pal[0]), _hex_to_rgb_tuple(pal[-1])
                    pls = gl.plot_list() or []
                    for i, pl in enumerate(pls):
                        t_ = i / max(1, len(pls) - 1)
                        rgb = tuple(int(c0[k] + (c1[k] - c0[k]) * t_) for k in range(3))
                        pl.color = rgb
                    grad_note = f"渐变色 {pal[0]} -> {pal[-1]}"
                except Exception as _ge:
                    grad_note = f"渐变色失败(不影响出图): {_ge}"
            r = oerr.ok(graph=short, template=t, series=names,
                        offsets=offsets, step=step, style=style,
                        gradient=grad_note,
                        detail=(f"stacked_spectra 完成：{len(names)} 条谱线"
                                f"（间距 {step:.4g}）"
                                f"{f'；{grad_note}' if grad_note else ''}"
                                f" -> {short}"))
        elif t == "xrd_pattern":
            # D3 修复：改用**双层布局**（不再把差谱挤进主图同一 Y 轴）。
            # 缺陷根因：三序列共用一条 Y 轴时，差谱的向下偏移会占据大量量程，
            # 主峰被压扁/裁切（实测主峰仅占量程 ~62% 且下部 25% 是空的）。
            # 现方案：上层 = Observed 散点 + Calculated 线（满量程，峰值占 ~90%）；
            #         下层 = Difference（独立量程，X 轴与上层严格对齐）+ 相刻线。
            x = _flist(data["two_theta"])
            obs = _flist(data["observed"])
            calc = _flist(data["calculated"])
            diff = _flist(data["difference"]) if "difference" in data else None
            cols = {"two_theta": x, "Observed": obs, "Calculated": calc}
            if diff is not None:
                cols["Difference"] = diff
            w = _write_data_impl(cols)
            if not w.get("ok"):
                return w
            wsobj = op.find_sheet("w", w["worksheet"])
            lname = _ensure_graph_name(graph_name, title or "XRD")
            gp = op.new_graph(lname=lname)
            short = gp.obj.GetName()
            try:
                op.po.LT_execute(f"win -a {short};")
            except Exception:
                pass
            gl_top = gp[0]
            gl_top.add_plot(wsobj, 1, 0, type="s")     # Observed 散点
            gl_top.add_plot(wsobj, 2, 0, type="l")     # Calculated 线
            gl_top.rescale()
            # Observed 密集散点缩小（P1：散点淹没 Calculated 线的修复）
            try:
                _plts_top = gl_top.plot_list() or []
                if _plts_top:
                    _plts_top[0].symbol_size = 3
            except Exception:
                pass
            layers_ok = True
            gl_bot = None
            if diff is not None:
                gl_bot, e_add = safe_call(gp.add_layer)
                if gl_bot is None:
                    layers_ok = False
            if diff is not None and gl_bot is not None:
                gl_bot.add_plot(wsobj, 3, 0, type="l")  # Difference 线
                # 零参考线（2 点直线，便于判读偏差方向）
                zws = op.new_sheet("w", _new_unique_name("XRDZero"))
                zws.from_list(0, [float(x[0]), float(x[-1])], lname="zx")
                zws.from_list(1, [0.0, 0.0], lname="zy")
                gl_bot.add_plot(zws, 1, 0, type="l")
                gl_bot.rescale()
            # 几何：上层 8%~70%，下层 72%~94%（%页，layer.unit=1）
            geo = []
            if layers_ok and gl_bot is not None:
                geo = _set_layer_geometry(gl_top, left=14.0, top=8.0,
                                          width=80.0, height=60.0)
                geo += _set_layer_geometry(gl_bot, left=14.0, top=72.0,
                                           width=80.0, height=22.0)
            # X 轴严格对齐：把上层 X 范围抄给下层
            xlim = None
            try:
                lim = gl_top.axis("x").limits
                if isinstance(lim, (tuple, list)) and len(lim) >= 2:
                    xlim = (float(lim[0]), float(lim[1]))
            except Exception:
                xlim = None
            if xlim and gl_bot is not None:
                for gl_, tag in ((gl_top, "top"), (gl_bot, "bottom")):
                    try:
                        gl_.axis("x").sfrom = xlim[0]
                        gl_.axis("x").sto = xlim[1]
                    except Exception:
                        pass
            # 轴标题：上层 Y=Intensity (a.u.)，下层 Y=Difference；X 只在最下层
            # 全部走 set_axis_title_checked（写后读回验证；2θ/(%) 等字符实测需要）
            import origin_edit as _oedit
            _oedit.set_axis_title_checked(gl_top, "y",
                                          y_title or "Intensity (a.u.)", po=op.po)
            if x_title:
                _oedit.set_axis_title_checked(gl_top, "x", x_title, po=op.po)
            if gl_bot is not None:
                _oedit.set_axis_title_checked(gl_bot, "y", "Difference", po=op.po)
                try:
                    gl_top.set_int("x.showAxes", 0)      # 上层隐藏 X 轴与刻度
                    # 连上层残留的 x 标题对象一起清空，避免 "two_theta" 悬浮
                    gl_top.axis("x").title = ""
                except Exception:
                    pass
                _oedit.set_axis_title_checked(
                    gl_bot, "x", x_title or "2θ (degrees)", po=op.po)
            else:
                _oedit.set_axis_title_checked(
                    gl_top, "x", x_title or "2θ (degrees)", po=op.po)
            # 相刻线（画在下层，从下层底部到其 35% 高度处）
            phase_sheets = []
            if diff is not None and gl_bot is not None:
                dy_from, dy_to = None, None
                try:
                    dl = gl_bot.axis("y").limits
                    if isinstance(dl, (tuple, list)) and len(dl) >= 2:
                        dy_from, dy_to = float(dl[0]), float(dl[1])
                except Exception:
                    pass
                if dy_from is not None and dy_to is not None:
                    span_d = max(dy_to - dy_from, 1e-9)
                    for ph_name, positions in (data.get("phases") or {}).items():
                        seg_x, seg_y = [], []
                        for p in positions:
                            try:
                                pv = float(p)
                            except (TypeError, ValueError):
                                continue
                            seg_x += [pv, pv, float("nan")]
                            seg_y += [dy_from - 0.02 * span_d,
                                      dy_from + 0.33 * span_d, float("nan")]
                        if not seg_x:
                            continue
                        pws = op.new_sheet("w", _new_unique_name("Phase"))
                        pws.from_list(0, seg_x, lname=f"{ph_name}_x")
                        pws.from_list(1, seg_y, lname=f"{ph_name}_y")
                        pl_ph = gl_bot.add_plot(pws, 1, 0, type="l")
                        # P1 修复：刻线用品红红 + 加粗，不再与 Difference 同色
                        # 同宽混为噪声毛刺（实测 c10 教训）
                        try:
                            pl_ph.color = (204, 26, 26)
                        except Exception:
                            pass
                        try:
                            pl_ph.set_cmd("-wp 1.5")
                        except Exception:
                            try:
                                pl_ph.linewidth = 1.5
                            except Exception:
                                pass
                        try:
                            pl_ph.set_int("show", 1)  # 确保可见
                        except Exception:
                            pass
                        phase_sheets.append(str(ph_name))
            style = _apply_style_impl(short, plot_type="line",
                                      columns=["Observed", "Calculated"],
                                      style_mode=style_mode, family=family,
                                      apply_axis_titles=False)
            layout_note = ("双层布局（主谱满量程 + 差谱独立量程）"
                           if gl_bot is not None else
                           "单层兜底（add_layer 不可用，差谱与主谱同轴）")
            r = oerr.ok(graph=short, template=t, phases=phase_sheets,
                        layer_layout=("two_layer" if gl_bot is not None else "single_layer"),
                        geometry=geo, style=style,
                        detail=(f"xrd_pattern 完成：{layout_note}；"
                                f"Observed 散点 + Calculated 线"
                                f"{' + Difference 独立量程层' if diff is not None else ''}"
                                f"{' + ' + str(len(phase_sheets)) + ' 组相刻线' if phase_sheets else ''}"
                                f" -> {short}"))
        elif t == "dual_y":
            x = _flist(data["x"])
            left = _flist(data["left"])
            right = _flist(data["right"])
            left_name = str(data.get("left_name") or "Left")
            right_name = str(data.get("right_name") or "Right")
            lname = _ensure_graph_name(graph_name, title or "DualY")
            # 双 Y 模板名因版本而异（2026 实测 doubley/righty 可用，dualy 不存在）：
            # 逐个探测，全部失败则退回"普通图 + add_layer"并明确告知右轴需手动调整
            gp, used_template = None, None
            for tname in ("dualy", "doubley", "righty"):
                try:
                    gpx = op.new_graph(lname=lname, template=tname)
                except Exception:
                    gpx = None
                if gpx is not None:
                    gp, used_template = gpx, tname
                    break
            if gp is None:
                try:
                    gp = op.new_graph(lname=lname)
                    gp.add_layer()
                except Exception:
                    gp = None
            if gp is None:
                return oerr.fail(
                    "template_unavailable",
                    "双 Y 模板（dualy/doubley/righty）均不可用且 add_layer 失败",
                    workaround="改用 origin_plot 双序列，或 origin_plot_template="
                               "'stacked_spectra'")
            short = gp.obj.GetName()
            try:
                op.po.LT_execute(f"win -a {short};")
            except Exception:
                pass
            nlay = _lt_read_float("page.nlayers")
            if not nlay or nlay < 2:
                return oerr.fail(
                    "template_unavailable",
                    "双 Y 模板未能创建双图层", template_used=used_template,
                    workaround="改用 origin_plot 双序列，或 stacked_spectra")
            w = _write_data_impl({"x": x, left_name: left, right_name: right})
            if not w.get("ok"):
                return w
            wsobj = op.find_sheet("w", w["worksheet"])
            gl1, gl2 = gp[0], gp[1]
            gl1.add_plot(wsobj, 1, 0, type="l")
            gl1.rescale()
            gl2.add_plot(wsobj, 2, 0, type="l")
            gl2.rescale()
            try:
                pass  # 标题在 style 之后统一写（apply_axis_titles=False 防覆盖）
            except Exception:
                pass
            style = _apply_style_impl(short, plot_type="line",
                                      columns=[left_name, right_name],
                                      style_mode=style_mode, family=family,
                                      apply_axis_titles=False)
            # 标题走 checked 通道：doubley 模板可见右轴可能是 layer2.y2，
            # "库仑效率 (%)" 之类的文本曾被静默丢成 "% (1.2)"（c21 实测）
            import origin_edit as _oedit
            _t1 = _oedit.set_axis_title_checked(gl1, "y", y_title or left_name,
                                                po=op.po)
            _t2 = _oedit.set_axis_title_checked(gl2, "y", right_name, po=op.po)
            title_channels = {"left": _t1.get("channel"), "right": _t2.get("channel")}
            # 图例收尾：跨层引用重建后，右轴行可能残留 "%(1.2)" 字面占位符
            # （列 long name 含 "(%)" 时 substitution 失败）。直接写图例文本，
            # 用 \l(层.序) 引用线样式；文本里的 % 必须转义成 %%，
            # 否则赋值时 LabTalk 又会对 "(%)" 做 substitution（c21 二次实测）。
            try:
                esc_l = str(y_title or left_name).replace(
                    '"', '\\"').replace('%', '%%')
                esc_r = str(right_name).replace(
                    '"', '\\"').replace('%', '%%')
                op.po.LT_execute(
                    f'legend.text$ = "\\l(1.1) {esc_l}\\n\\l(2.1) {esc_r}";')
            except Exception:
                pass
            note = (f"模板 {used_template}" if used_template
                    else "普通双图层（右轴位置可能需在 OPJU 中手动调整）")
            r = oerr.ok(graph=short, template=t, template_used=used_template,
                        left_axis=left_name, right_axis=right_name,
                        title_channels=title_channels, style=style,
                        detail=f"dual_y 完成：左轴 {left_name} / 右轴 {right_name}"
                               f"（{note}；排版仅作用于第一层）-> {short}")
        elif t == "forest":
            labels = [str(v) for v in data["labels"]]
            effect = _flist(data["effect"])
            lo = _flist(data["ci_low"])
            hi = _flist(data["ci_high"])
            n = len(labels)
            try:
                zero = float(data.get("zero", 0))
            except (TypeError, ValueError):
                zero = 0.0
            ws = op.new_sheet("w", _new_unique_name("ForestPt"))
            ws.from_list(0, [float(i + 1) for i in range(n)], lname="study_index")
            ws.from_list(1, effect, lname="effect")
            ci_lo_f, ci_hi_f = _flist(data["ci_low"]), _flist(data["ci_high"])
            # CI 横线（NaN 断段）+ 零参考线：add_plot 保证可见性，
            # 图例用 legend.text$ 只保留效应量行（c25 教训：辅助系列泄漏图例）
            ci_x, ci_y = [], []
            for i in range(n):
                ci_x += [ci_lo_f[i], ci_hi_f[i], float("nan")]
                ci_y += [i + 1.0, i + 1.0, float("nan")]
            ws_ci = op.new_sheet("w", _new_unique_name("ForestCI"))
            ws_ci.from_list(0, ci_x, lname="ci_x")
            ws_ci.from_list(1, ci_y, lname="study_index")
            ws_z = op.new_sheet("w", _new_unique_name("ForestZero"))
            ws_z.from_list(0, [zero, zero], lname="zero_x")
            ws_z.from_list(1, [0.5, float(n) + 0.5], lname="zero_y")
            lname = _ensure_graph_name(graph_name, title or "Forest")
            gp = op.new_graph(lname=lname)
            gl = gp[0]
            gl.add_plot(ws_z, 1, 0, type="l")    # 零参考线（plot 1）
            gl.add_plot(ws_ci, 1, 0, type="l")   # CI 横线（plot 2）
            gl.add_plot(ws, 0, 1, type="s")      # 效应量点（plot 3）
            gl.rescale()
            short = gp.obj.GetName()
            # X 下限向左扩 18% 给研究名标签腾位
            lo_all = [v for v in ci_lo_f if np.isfinite(v)]
            hi_all = [v for v in ci_hi_f if np.isfinite(v)]
            xmin = min(lo_all + [zero]) if lo_all else zero
            xmax = max(hi_all + [zero]) if hi_all else zero
            span = max(xmax - xmin, 1e-9)
            x_new = xmin - 0.18 * span
            try:
                gl.axis("x").sfrom = float(x_new)
            except Exception:
                pass
            # 研究名逐行标注（labels 此前只显示第一个的修复）
            label_x = x_new + 0.02 * span
            n_label = 0
            for i, lab in enumerate(labels):
                try:
                    gl.add_label(str(lab), float(label_x), float(i + 1))
                    n_label += 1
                except Exception:
                    continue
            # 图例只保留效应量行（plot 3），CI/零线不进图例
            try:
                _ensure_active_graph(short)
                op.po.LT_execute('legend.text$ = "\\l(3) effect";')
            except Exception:
                pass
            style = _apply_style_impl(short, plot_type="scatter",
                                      columns=["effect"], style_mode=style_mode,
                                      family=family, x_title="Effect size",
                                      apply_axis_titles=True)
            r = oerr.ok(graph=short, template=t, labels=labels, zero=zero,
                        labels_placed=n_label,
                        style=style,
                        detail=f"forest 完成：{n} 项研究（点 + CI 线 + 零参考线，"
                               f"图例仅含效应量行），{n_label} 个研究名逐行标注"
                               f" -> {short}")
        elif t == "multi_panel":
            panels = {str(k): _flist(v) for k, v in data["panels"].items()}
            names = list(panels)
            has_x = "x" in data and isinstance(data["x"], (list, tuple)) and data["x"]
            cols = {}
            col_of = {}
            if has_x:
                cols["x"] = _flist(data["x"])
            else:
                nx = max(len(v) for v in panels.values())
                cols["index"] = [float(i + 1) for i in range(nx)]
            for nm in names:
                col_of[nm] = len(cols)
                cols[nm] = panels[nm]
            w = _write_data_impl(cols)
            if not w.get("ok"):
                return w
            wsobj = op.find_sheet("w", w["worksheet"])
            lname = _ensure_graph_name(graph_name, title or "MultiPanel")
            gp = op.new_graph(lname=lname)
            layers = [gp[0]]
            for _ in range(1, len(names)):
                try:
                    layers.append(gp.add_layer())
                except Exception:
                    break
            drawn = min(len(layers), len(names))
            # P0 修复（2026-09-16 c23 教训）：add_layer 默认把新层放在与第 0 层
            # 相同的位置（三面板完全重叠）。这里按层纵向均分 %页几何，
            # 层间留 3% 间隙；标题区另留 8%。
            geo_mp = []
            if drawn > 1:
                top0, total_h, gap = 8.0, 86.0, 3.0
                h_each = (total_h - gap * (drawn - 1)) / drawn
                for i, gli in enumerate(layers):
                    t_i = top0 + i * (h_each + gap)
                    geo_mp += _set_layer_geometry(gli, left=14.0, top=t_i,
                                                  width=80.0, height=h_each)
            import origin_edit as _oedit
            for i in range(drawn):
                gli = layers[i]
                nm = names[i]
                gli.add_plot(wsobj, col_of[nm], 0, type="l")
                gli.rescale()
                _oedit.set_axis_title_checked(gli, "y", nm, po=op.po)
                if i == drawn - 1:
                    _oedit.set_axis_title_checked(
                        gli, "x", x_title or "X", po=op.po)
                else:
                    try:   # 上方面板隐藏 X 刻度，避免拥挤（与 xrd 同策略）
                        gli.set_int("x.showAxes", 0)
                        gli.axis("x").title = ""
                    except Exception:
                        pass
            short = gp.obj.GetName()
            if drawn < len(names):
                return oerr.fail(
                    "template_unavailable",
                    f"multi_panel 仅创建了 {drawn}/{len(names)} 个图层面板"
                    "（本环境 GLPage.add_layer 不可用）",
                    graph=short, drawn=drawn, worksheet=w["worksheet"],
                    workaround="分多次调用 origin_plot（每面板一张图），"
                               "或改用 stacked_spectra")
            style = _apply_style_impl(short, plot_type="line", columns=names,
                                      style_mode=style_mode, family=family,
                                      apply_axis_titles=False)
            r = oerr.ok(graph=short, template=t, panels=names, style=style,
                        geometry=geo_mp,
                        detail=f"multi_panel 完成：{drawn} 个面板（纵向堆叠，"
                               f"层间已去重叠）-> {short}")
        else:  # 防御分支（理论上已被 _validate_template_data 拦截）
            return oerr.fail("invalid_request", f"未知 template_id: {template_id!r}")

        if fmt or file_path:
            rex = _export_impl(r["graph"], file_path=file_path, fmt=fmt or "png",
                               width=width)
            if rex.get("ok"):
                r["file"] = rex["file"]
                r["size"] = rex["size"]
                r["format"] = rex["format"]
            else:
                r["warning"] = f"导出失败: {rex.get('error')}"
        return r
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


# ---------------------------------------------------------------------------
# 计划执行（P1）：origin_plot_plan 缓存的计划 -> 写数/画图/导出
# ---------------------------------------------------------------------------
def _execute_plan_impl(plan_id, fmt=None, file_path=None, graph_name=None,
                       width=1200):
    try:
        import origin_plan as oplan
        plan = oplan.get_plan(plan_id)
        if plan is None:
            return oerr.fail("plan_not_found",
                             f"plan_id 不存在或已过期（服务端缓存容量 "
                             f"{oplan.PLAN_CACHE_MAX}，重启后清空）",
                             hint="重新调用 origin_plot_plan 生成")
        p = plan["params"]
        roles = plan["roles"]
        columns = dict(plan["data"]["columns"])
        x_col = roles.get("x")
        if x_col is None:
            # 无单调 X 候选：生成行号列作 X（计划流已向用户提示）
            n = plan["data"]["n_rows"]
            columns = {"row_index": [float(i + 1) for i in range(n)], **columns}
            x_col = "row_index"
        r1 = _write_data_impl(columns)
        if not r1.get("ok"):
            return r1
        r2 = _plot_impl(r1["worksheet"], y_columns=roles.get("y"),
                        x_column=x_col, plot_type=p.get("plot_type") or "line",
                        yerr_column=roles.get("yerr"),
                        graph_name=graph_name or p.get("graph_name"),
                        title=p.get("title"),
                        style_mode=p.get("style_mode") or "default",
                        family=p.get("family"))
        if not r2.get("ok"):
            return r2
        r3 = _export_impl(r2["graph"], file_path=file_path or p.get("file_path"),
                          fmt=fmt or p.get("fmt") or "png", width=width)
        if r3.get("ok"):
            r3["worksheet"] = r1.get("worksheet")
            r3["graph"] = r2.get("graph")
            r3["plan_id"] = plan["plan_id"]
            r3["style"] = r2.get("style")
        if plan.get("questions"):
            r3["confirmation_reminder"] = (
                "该计划存在待确认项，执行前应已获得用户确认："
                + json.dumps(plan["questions"], ensure_ascii=False))
        return r3
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


# ---------------------------------------------------------------------------
# 版本能力握手（P1）：origin_status 暴露版本/已知坑/特性可用性
# ---------------------------------------------------------------------------
def _origin_version_label(v):
    if v is None:
        return "unknown"
    for k, name in _ORIGIN_VERSION_LABELS.items():
        if abs(v - k) < 0.01:
            return f"{name} (@V={v:g})"
    if v >= 10.3:
        return f"Origin 2026 系列 (@V={v:g})"
    if v >= 10.0:
        return f"Origin 2022-2025 系列 (@V={v:g})"
    if v >= 9.5:
        return f"Origin 2021 系列 (@V={v:g})"
    return f"Origin legacy (@V={v:g})"


def _capabilities_impl():
    """版本能力表（只读探测，绝不阻塞状态查询）。"""
    feats = {
        "plot_basic_types": True,
        "plot_histogram_box_bar": True,
        "plotxy_type_204_215": False,
        "templates": sorted(PLOT_TEMPLATES),
        "xlsx_via_openpyxl": None,
        "fine_edit": True,
    }
    try:
        import openpyxl  # noqa: F401
        feats["xlsx_via_openpyxl"] = True
    except Exception:
        feats["xlsx_via_openpyxl"] = False
    ver = _lt_read_float("@V")
    return {
        "origin_version_raw": ver,
        "origin_version_label": _origin_version_label(ver),
        "known_risks": CAPABILITY_KNOWN_RISKS,
        "features": feats,
        "session_mode": os.environ.get("ORIGIN_SESSION", "attach"),
        "channel_policy": ("COM 作用域通道（窗口无关，优先）；LabTalk 仅在校验激活后使用"
                           "（未激活时会静默失靶，见 origin_verify 的 context 说明）"),
    }


# ---------------------------------------------------------------------------
# 细粒度编辑（origin_edit 模块；面向"只改一条线的颜色/加粗/关几个窗口"这类需求）
# ---------------------------------------------------------------------------
def _list_pages_impl():
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        import origin_edit as oedit
        return oedit.list_pages(_origin_app, _origin_app.po)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _inspect_graph_impl(graph=None, max_plots=40):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        import origin_edit as oedit
        return oedit.inspect_graph(_origin_app, _origin_app.po, graph,
                                   max_plots=max_plots)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _edit_plot_impl(graph, edits):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        import origin_edit as oedit
        return oedit.edit_plot(_origin_app, _origin_app.po, graph, edits)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _edit_axis_impl(graph, axis="x", layer=0, title=None, from_value=None,
                    to_value=None, scale=None, props=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        import origin_edit as oedit
        return oedit.edit_axis(_origin_app, _origin_app.po, graph, axis=axis,
                               layer=layer, title=title, from_=from_value,
                               to=to_value, scale=scale, **dict(props or {}))
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _edit_legend_impl(graph, options):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        import origin_edit as oedit
        return oedit.edit_legend(_origin_app, _origin_app.po, graph,
                                 **dict(options or {}))
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _edit_page_impl(graph, page_size_cm=None, background=None, layer=None,
                    layer_geometry_pct=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        import origin_edit as oedit
        return oedit.edit_page(_origin_app, _origin_app.po, graph,
                               page_size_cm=page_size_cm, background=background,
                               layer=layer, layer_geometry_pct=layer_geometry_pct)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _manage_pages_impl(action, pages=None, new_name=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        import origin_edit as oedit
        return oedit.manage_pages(_origin_app, _origin_app.po, action,
                                  pages=pages, new_name=new_name)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _add_text_impl(graph, text, x=None, y=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        import origin_edit as oedit
        return oedit.add_text(_origin_app, _origin_app.po, graph, text, x=x, y=y)
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


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
         style_mode="default", family=None, style_overrides=None):
    return _plot_impl(worksheet, y_columns=y_columns, x_column=x_column,
                      plot_type=plot_type, graph_name=graph_name, title=title,
                      yerr_column=yerr_column, style_mode=style_mode, family=family,
                      style_overrides=style_overrides)


@_synchronized
def export(graph, file_path=None, fmt="png", width=1200, output_dir=None):
    return _export_impl(graph, file_path=file_path, fmt=fmt, width=width,
                        output_dir=output_dir)


@_synchronized
def plot_file(columns, plot_type="line", fmt="png", file_path=None, width=1200,
              output_dir=None, x_column=None, y_columns=None, title=None,
              graph_name=None, style_mode="default", family=None,
              style_overrides=None):
    return _plot_file_impl(columns, plot_type=plot_type, fmt=fmt, file_path=file_path,
                           width=width, output_dir=output_dir, x_column=x_column,
                           y_columns=y_columns, title=title, graph_name=graph_name,
                           style_mode=style_mode, family=family,
                           style_overrides=style_overrides)


@_synchronized
def filter_data(worksheet, drop_rows=None, x_column=0, x_min=None, x_max=None):
    return _filter_data_impl(worksheet, drop_rows=drop_rows, x_column=x_column,
                             x_min=x_min, x_max=x_max)


@_synchronized
def fit(worksheet, x_column, y_column, kind="linear", plot_curve=True,
        graph=None, title=None, drop_report_pages=True):
    return _fit_impl(worksheet, x_column, y_column, kind=kind, plot_curve=plot_curve,
                     graph=graph, title=title, drop_report_pages=drop_report_pages)


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
              new_x=None, write_back=True, return_values=True):
    return _transform_impl(worksheet, column, op=op, window=window, method=method,
                           new_x=new_x, write_back=write_back,
                           return_values=return_values)


@_synchronized
def integrate(worksheet, x_column=0, y_column=1, baseline=None):
    return _integrate_impl(worksheet, x_column=x_column, y_column=y_column,
                           baseline=baseline)


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
              fmt="png", width=1200, color=None):
    return _histogram_impl(worksheet, column=column, bins=bins, plot=plot,
                           file_path=file_path, fmt=fmt, width=width, color=color)


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
                family=None, x_title=None, style_overrides=None):
    return _apply_style_impl(graph, plot_type=plot_type, columns=columns,
                             style_mode=style_mode, family=family, x_title=x_title,
                             style_overrides=style_overrides)


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


@_synchronized
def load_file(path, worksheet=None, sheet=None, max_preview_rows=5):
    return _load_file_impl(path, worksheet=worksheet, sheet=sheet,
                           max_preview_rows=max_preview_rows)


@_synchronized
def save_project(path):
    return _save_project_impl(path)


@_synchronized
def export_delivery(graph, source_path=None, output_dir=None, fmts="png,pdf",
                    width=1200, save_opju=True):
    return _export_delivery_impl(graph, source_path=source_path,
                                 output_dir=output_dir, fmts=fmts, width=width,
                                 save_opju=save_opju)


@_synchronized
def verify_graph(graph=None, expected_x_title=None, expected_y_title=None,
                 min_font_pt=None, expected_series=None, legend_visible=None,
                 files=None, allow_full_overlap=False):
    return _verify_graph_impl(graph=graph, expected_x_title=expected_x_title,
                              expected_y_title=expected_y_title,
                              min_font_pt=min_font_pt,
                              expected_series=expected_series,
                              legend_visible=legend_visible, files=files,
                              allow_full_overlap=allow_full_overlap)


@_synchronized
def plot_template(template_id, data, graph_name=None, title=None,
                  style_mode="default", family=None, offset="auto",
                  reverse_x=False, fmt=None, file_path=None, width=1200,
                  x_title=None, y_title=None, gradient=False):
    return _plot_template_impl(template_id, data, graph_name=graph_name,
                               title=title, style_mode=style_mode, family=family,
                               offset=offset, reverse_x=reverse_x, fmt=fmt,
                               file_path=file_path, width=width,
                               x_title=x_title, y_title=y_title, gradient=gradient)


@_synchronized
def execute_plan(plan_id, fmt=None, file_path=None, graph_name=None, width=1200):
    return _execute_plan_impl(plan_id, fmt=fmt, file_path=file_path,
                              graph_name=graph_name, width=width)


# ---------------------------------------------------------------------------
# 细粒度编辑公开 API（COM 线程；每项改动都带读回与 applied/rejected 状态）
# ---------------------------------------------------------------------------
@_synchronized
def list_pages():
    return _list_pages_impl()


@_synchronized
def inspect_graph(graph=None, max_plots=40):
    return _inspect_graph_impl(graph=graph, max_plots=max_plots)


@_synchronized
def edit_plot(graph, edits):
    return _edit_plot_impl(graph, edits)


@_synchronized
def edit_axis(graph, axis="x", layer=0, title=None, from_value=None,
              to_value=None, scale=None, props=None):
    return _edit_axis_impl(graph, axis=axis, layer=layer, title=title,
                           from_value=from_value, to_value=to_value,
                           scale=scale, props=props)


@_synchronized
def edit_legend(graph, options):
    return _edit_legend_impl(graph, options)


@_synchronized
def edit_page(graph, page_size_cm=None, background=None, layer=None,
              layer_geometry_pct=None):
    return _edit_page_impl(graph, page_size_cm=page_size_cm,
                           background=background, layer=layer,
                           layer_geometry_pct=layer_geometry_pct)


@_synchronized
def manage_pages(action, pages=None, new_name=None):
    return _manage_pages_impl(action, pages=pages, new_name=new_name)


@_synchronized
def add_text(graph, text, x=None, y=None):
    return _add_text_impl(graph, text, x=x, y=y)
