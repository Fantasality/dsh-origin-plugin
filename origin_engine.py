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
    "cycle_overlay": {
        "desc": "多曲线同图叠放 + 渐变色（CV 多圈 / 充放电多循环 / 多轮次对比）",
        "data": "{'x': [..], 'series': {'曲线名': [..], ...}（≥2 条）,"
                "'legend_mode': 'all'|'first_last'|'none' 可选}",
        "options": "x_title/y_title 覆盖轴标题",
    },
    "eis_nyquist": {
        "desc": "电化学阻抗谱 Nyquist 图（-Z'' vs Z'，等轴比 + 近正方形图层）",
        "data": "{'z_real': [..], 'z_imag': [..]}",
        "options": "x_title/y_title 覆盖轴标题",
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

    def wait(self, timeout=None):
        """等待完成；带 timeout 时返回是否在时限内完成（不抛出结果）。"""
        if timeout is None:
            self._evt.wait()
            if self.exc is not None:
                raise self.exc
            return self.value
        ok = self._evt.wait(timeout)
        if ok and self.exc is not None:
            raise self.exc
        return ok


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


def _watchdog_origin_pids():
    """Origin 进程 PID 集合（tasklist 输出为 GBK，中文 Windows 实测）。"""
    import subprocess as _sp
    try:
        out = _sp.run(
            ["tasklist", "/FI", "IMAGENAME eq Origin64.exe", "/FO", "CSV"],
            capture_output=True).stdout.decode("gbk", errors="replace")
        return {int(line.split('","')[1]) for line in out.splitlines()
                if line.startswith('"Origin')}
    except Exception:
        return set()


def _watchdog_dismiss_modals():
    """枚举 Origin 的可见模态对话框并点击白名单按钮（看门狗，P0-1）。

    返回 [{"title": 窗口标题, "clicked": 按钮文本}]；找不到/点不掉的弹窗只记标题。
    机制（2026-09-16 探针实证）：EnumWindows 找 Origin PID 的 #32770 类
    可见对话框 → EnumChildWindows 找按钮 → PostMessage(BM_CLICK) 异步点击。
    """
    results = []
    try:
        import win32gui
        import win32con
        import win32process
    except ImportError:
        return results  # 无 pywin32：看门狗降级（直接走超时分支）
    pids = _watchdog_origin_pids()
    if not pids:
        return results
    found = []

    def _cb(h, _):
        try:
            if (win32gui.GetClassName(h) == "#32770"
                    and win32gui.IsWindowVisible(h)):
                _, pid = win32process.GetWindowThreadProcessId(h)
                if pid in pids:
                    found.append((h, win32gui.GetWindowText(h)))
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return results
    for h, title in found:
        entry = {"title": title, "clicked": None}
        try:
            buttons = []
            win32gui.EnumChildWindows(
                h, lambda b, _: buttons.append((b, win32gui.GetWindowText(b))),
                None)
            entry["buttons"] = [t for _, t in buttons]
            for b, txt in buttons:
                if txt in ("OK", "确定", "Yes", "是", "No", "否",
                           "Close", "关闭", "Cancel", "取消"):
                    win32gui.PostMessage(b, win32con.BM_CLICK, 0, 0)
                    entry["clicked"] = txt
                    break
        except Exception:
            pass
        results.append(entry)
    return results


def _watchdog_timeout_secs():
    """COM 软超时（秒）：DSH_ORIGIN_DISPATCH_TIMEOUT 覆盖，默认 90。

    依据：大项目（实测 800 页堆积）单次 close 枚举可达 59s——60s 会误杀
    慢但正常的调用，取 90s（对齐 youngminsw 默认）。
    """
    try:
        return max(10.0, float(os.environ.get("DSH_ORIGIN_DISPATCH_TIMEOUT", 90)))
    except (TypeError, ValueError):
        return 90.0


def _watchdog_grace_secs():
    """点击对话框后再等待（秒）：DSH_ORIGIN_WATCHDOG_GRACE，默认 15。"""
    try:
        return max(2.0, float(os.environ.get("DSH_ORIGIN_WATCHDOG_GRACE", 15)))
    except (TypeError, ValueError):
        return 15.0


def _run_on_com_thread(fn, *args, **kwargs):
    """把 fn 投递到专用 COM 线程执行并等待结果（线程安全，可被并发调用）。

    P0-1 看门狗（2026-09-16）：软超时后枚举 Origin 模态对话框并自动点击
    白名单按钮（OK/确定/取消类）；解除阻塞则正常返回并附
    watchdog_dismissed；仍未解除则硬超时——重建 COM 线程自愈，返回
    com_blocked_by_dialog 错误（含最后看到的对话框标题）。
    """
    global _com_thread
    if _com_thread is None or not _com_thread.is_alive():
        t = threading.Thread(target=_com_thread_loop, name="origin-com", daemon=True)
        t.start()
        _com_thread = t
    done = _TaskResult()
    _com_queue.put((fn, args, kwargs, done))
    # 阶段 1：软超时内正常等待
    if done.wait(timeout=_watchdog_timeout_secs()):
        return done.value
    # 阶段 2：看门狗——尝试点掉 Origin 的模态对话框
    dismissed = _watchdog_dismiss_modals()
    if done.wait(timeout=_watchdog_grace_secs()):
        v = done.value
        # 恢复正常：原结果附加看门狗信息（dict 与 connect 的 (bool, dict) 都覆盖）
        note = (f"本次调用曾被 Origin 模态对话框阻塞，看门狗已自动解除: {dismissed}")
        if isinstance(v, dict):
            v["watchdog_dismissed"] = dismissed
            v["warning"] = (v.get("warning") + "；" if v.get("warning") else "") + note
        elif (isinstance(v, tuple) and len(v) == 2 and isinstance(v[1], dict)):
            v[1]["watchdog_dismissed"] = dismissed
            v[1]["warning"] = ((v[1].get("warning") or "") + "；"
                               if v[1].get("warning") else "") + note
        return v
    # 阶段 3：硬超时——看门狗无效。旧 COM 线程仍堵在 Origin 内部调用上，
    # 只有 Origin 进程结束该调用才会异常返回（随后队列自愈继续）。
    titles = [d.get("title", "?") for d in dismissed]
    if _autokill_enabled():
        # isolated 会话（或显式开启）：Origin 视为自动化残留，直接清理，
        # COM 线程的下一次队列任务会收到异常并继续——系统自愈。
        kill = _kill_residual_origin(wait_seconds=2.0)
        return oerr.fail(
            "com_blocked_by_dialog",
            f"COM 调用被 Origin 模态对话框阻塞超过 "
            f"{_watchdog_timeout_secs():.0f}+{_watchdog_grace_secs():.0f} 秒，"
            f"看门狗未能解除（发现对话框: {titles or '未枚举到'}）。"
            f"已按 isolated 策略自动清理 Origin（killed={kill.get('killed')}），"
            "请重试；Origin 将以干净状态重连。",
            watchdog={"dismissed": dismissed, "autokill": kill},
            timeout_s=_watchdog_timeout_secs() + _watchdog_grace_secs())
    return oerr.fail(
        "com_blocked_by_dialog",
        f"COM 调用被 Origin 模态对话框阻塞超过 "
        f"{_watchdog_timeout_secs():.0f}+{_watchdog_grace_secs():.0f} 秒，"
        f"看门狗未能解除（发现对话框: {titles or '未枚举到'}）。"
        "请在 Origin 窗口手动关闭该对话框后原样重试；若 Origin 已无响应，"
        "taskkill /F /IM Origin64.exe 后重连。",
        watchdog={"dismissed": dismissed},
        timeout_s=_watchdog_timeout_secs() + _watchdog_grace_secs())


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
    每次调用自动附加 trace_id 与 duration_ms（#6），便于定位失败阶段。
    """
    def wrapper(*args, **kwargs):
        _configure_ipc_lock_if_requested()
        t0 = time.time()
        result = _run_on_com_thread(fn, *args, **kwargs)
        elapsed_ms = int((time.time() - t0) * 1000)
        trace_id = f"t{uuid.uuid4().hex[:12]}"
        # 常规 dict 返回直接附加；connect() 的 (bool, dict) 元组附加到第二元素
        if isinstance(result, dict):
            result["trace_id"] = trace_id
            result["duration_ms"] = elapsed_ms
            if not result.get("ok") and "error_code" not in result:
                return oerr.upgrade_legacy_failure(result)
            return result
        if (isinstance(result, tuple) and len(result) == 2
                and isinstance(result[1], dict)):
            result[1]["trace_id"] = trace_id
            result[1]["duration_ms"] = elapsed_ms
        return result
    return wrapper


# ---------------------------------------------------------------------------
# 连接（以下 *_impl 函数只在 COM 线程内执行）
# ---------------------------------------------------------------------------
def _safe_find_graph(op, name=None):
    """安全版 op.find_graph —— 绕过 originpro 1.1.15 的库缺陷。

    实测（2026-09-18 用户现场，Origin 2024 SR1）：originpro 1.1.15 的
    find_graph(name) 会抛 **TypeError**——它把对象传给了只接受 int/str 的 Pages()。
    这里先走库方法，抛 TypeError 时退化为遍历页面按短名匹配；都失败返回 None
    （上层按 graph_not_found 处理，**不让异常冒泡**）。
    """
    if op is None:
        return None
    try:
        return op.find_graph(name) if name else op.find_graph()
    except TypeError:
        pass
    except Exception:
        return None

    def _short(gp):
        try:
            return str(gp.GetName() if hasattr(gp, "GetName") else getattr(gp, "name", ""))
        except Exception:
            return ""
    try:
        pages = op.pages() if hasattr(op, "pages") else None
        if pages is None:
            return None
        for pg in pages:
            try:
                if name is None:
                    if "Graph" in _short(pg):
                        return pg
                elif _short(pg).split("]")[-1].lower() == str(name).lower():
                    return pg
            except Exception:
                continue
    except Exception:
        return None
    return None


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


def _autokill_enabled():
    """连接前自动清理残留 Origin 进程的开关（DSH_ORIGIN_AUTOKILL）。

    - 显式 "1"/"true"/"on"/"yes"：强制开启；"0"/"false"/"off"/"no"：强制关闭；
    - 未设置时：仅 ORIGIN_SESSION=isolated 默认开启（隔离会话里正在运行的
      Origin 视为上一轮自动化残留，可安全清理重建）；
    - attach 模式（默认）不开：用户手动打开的 Origin 可能有未保存工作，
      只在连接失败时通过 recovery.diagnose 建议手动 taskkill。
    """
    raw = os.environ.get("DSH_ORIGIN_AUTOKILL")
    if raw is not None and raw.strip():
        return raw.strip().lower() in ("1", "true", "on", "yes")
    return os.environ.get("ORIGIN_SESSION", "attach").lower() == "isolated"


def _kill_residual_origin(wait_seconds=2.0):
    """taskkill 清理全部 Origin64 进程并等待其完全退出（连接前自愈，#1）。

    约束：只应在 _autokill_enabled() 为真时调用（见该函数的策略说明）。
    返回诊断 dict；after>0 表示 taskkill 未能清干净（权限/守门进程）。
    """
    before = _origin_proc_count()
    info = {"enabled": True, "before": before, "killed": False, "after": None,
            "wait_ms": 0}
    if not before:
        info["after"] = before
        return info
    try:
        os.popen('taskkill /F /IM Origin64.exe /T >nul 2>&1').read()
        info["killed"] = True
    except Exception:
        pass
    t0 = time.time()
    time.sleep(max(0.0, float(wait_seconds)))   # 要求：等 2 秒再让 COM 重启它
    deadline = time.time() + 8.0                # 进程退出最多再等 8 秒
    while time.time() < deadline:
        if not _origin_proc_count():
            break
        time.sleep(0.5)
    info["wait_ms"] = int((time.time() - t0) * 1000)
    info["after"] = _origin_proc_count()
    return info


def _connect_impl():
    global _connected, _origin_app
    if _connected and _origin_app is not None:
        return True, _describe_impl()
    # 连接前自愈（#1）：清理残留 Origin64 进程 -> 等 2 秒 -> 再让 COM 启动/复用
    autokill = _kill_residual_origin() if _autokill_enabled() else None
    blocked = _isolated_session_blocked()
    if blocked is not None:
        if autokill and autokill.get("after"):
            blocked["autokill"] = autokill
            blocked.setdefault("next_actions", []).append(
                "已尝试自动 taskkill 但仍有 Origin64 进程存活（权限不足），"
                "请手动关闭或用管理员权限重试")
        return False, blocked
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
        if autokill is not None:
            info["autokill"] = autokill
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
        result = oerr.fail(
            "connection_error",
            str(e) + extra,
            origin_running=_origin_running(),
            next_actions=[
                "确认已安装 Origin（C:\\Program Files\\OriginLab\\Origin2026b\\Origin64.exe）",
                "确认 Origin 可被脚本启动（首次启动约 5~45 秒）",
                "Origin 与脚本的运行权限需一致（管理员/普通）",
                "taskkill /F /IM Origin64.exe 清理残留进程，等 2 秒后重试"
                "（或设置 DSH_ORIGIN_AUTOKILL=1 自动清理）",
                "调 origin_diagnose 定位安装/COM 注册/目录权限问题",
            ])
        if autokill is not None:
            result["autokill"] = autokill
        return False, result


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
        gp = _safe_find_graph(op, graph_name)
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
# --- P2-8（2026-09-16）：文件访问白名单（安全场景限制可读写的目录前缀） ---
def _path_allowed(path):
    """DSH_ORIGIN_ALLOWED_ROOTS 未设=不限制；设置后 path 必须落在前缀内。

    多个前缀用分号分隔；大小写与斜杠方向不敏感（Windows 语义）。
    """
    roots = os.environ.get("DSH_ORIGIN_ALLOWED_ROOTS", "").strip()
    if not roots or not path:
        return True
    p = os.path.abspath(str(path)).lower().replace("/", "\\")
    for r in re.split(r"[;,]", roots):
        r2 = os.path.abspath(r.strip()).lower().replace("/", "\\")
        if r2 and (p == r2 or p.startswith(r2 + "\\")):
            return True
    return False


def _path_guard(path, what="路径"):
    """白名单检查：放行返回 None，否则返回 fail(path_not_allowed)。"""
    if not _path_allowed(path):
        return oerr.fail(
            "path_not_allowed",
            f"{what} {path!r} 不在 DSH_ORIGIN_ALLOWED_ROOTS 白名单内",
            path=str(path),
            hint="让用户调整白名单（分号分隔多个目录前缀）或把文件移入允许目录")
    return None


def _file_valid_for(path, fmt):
    """导出产物有效性：存在、非空；png/pdf/eps 再校验文件头（防假成功）。"""
    try:
        if not path or not os.path.exists(path):
            return False
        if os.path.getsize(path) <= 0:
            return False
        with open(path, "rb") as fh:
            head = fh.read(8)
        if fmt == "png" and not head.startswith(b"\x89PNG"):
            return False
        if fmt == "pdf" and not head.startswith(b"%PDF"):
            return False
        if fmt == "eps" and not head.startswith(b"%!PS"):
            return False
        return True
    except Exception:
        return False


def _com_image_export(op, file_path, fmt):
    """COM 通道 2：Origin 自动化服务器的 ImageExport 对象（page 级导出）。

    属性名在不同 Origin 版本间可能有差异，任何异常都交给上层回退链处理；
    成功与否最终以 _file_valid_for 的文件头校验为准。
    """
    po = getattr(op, "po", None)
    ie = getattr(po, "ImageExport", None) if po is not None else None
    if ie is None:
        raise AttributeError("ImageExport 对象不可用")
    ie.FileName = file_path
    ie.Export()
    return file_path


def _lt_exp_graph(op, graph, file_path, fmt, width):
    """LabTalk 通道 3：显式 expGraph 命令导出（最后回退）。

    注意 LabTalk 静默失败特性：命令不抛异常不代表成功，
    上层必须用文件存在性/文件头校验裁决。
    """
    folder, name = os.path.split(str(file_path).replace("\\", "/"))
    stem = os.path.splitext(name)[0]
    tr = ""
    if fmt == "png" and width and width > 0:
        tr = " tr1.Unit:=2 tr1.Width:=%d" % int(width)
    cmd = ('expGraph igp:=%s type:=%s path:="%s" filename:="%s" '
           'overwrite:=replace%s;' % (graph, fmt, folder, stem, tr))
    op.po.LT_execute(cmd)
    return file_path


def _export_impl(graph, file_path=None, fmt="png", width=1200, output_dir=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app

        fmt = (fmt or "png").lower().lstrip(".")
        if fmt == "tiff":
            fmt = "tif"
        if fmt not in ("png", "svg", "pdf", "tif", "emf", "eps"):
            return oerr.fail("invalid_request",
                             f"fmt 只支持 png/svg/pdf/tif/emf/eps，收到 {fmt!r}")

        gp = _safe_find_graph(op, graph)
        if not gp:
            return oerr.fail("graph_not_found", f"图不存在: {graph}")

        if file_path:
            file_path = os.path.abspath(file_path)
            _g = _path_guard(file_path, "导出路径")
            if _g is not None:
                return _g
            ext = os.path.splitext(file_path)[1].lstrip(".").lower()
            if ext and ext != fmt:
                fmt = ext
        else:
            out_dir = os.path.abspath(output_dir or DEFAULT_OUTPUT_DIR)
            _g = _path_guard(out_dir, "导出目录")
            if _g is not None:
                return _g
            os.makedirs(out_dir, exist_ok=True)
            base = re.sub(r"[^\w\-.]", "_", str(graph).replace(" ", "_"))
            file_path = os.path.join(out_dir, f"{base}.{fmt}")

        kwargs = {"replace": True}
        if fmt == "png" and width and width > 0:
            kwargs["width"] = int(width)

        # 三级导出回退链（#2）：save_fig(expGraph 封装) -> COM ImageExport
        # -> LabTalk expGraph；每级都做文件存在性 + 文件头校验（防 LabTalk
        # 静默失败 / 0 字节 / 错误格式文件冒充成功）。
        attempts = []
        final = None

        res1, err1 = safe_call(gp.save_fig, file_path, **kwargs)
        attempts.append({"channel": "save_fig(expGraph)", "ok": _file_valid_for(res1, fmt),
                         "returned": str(res1) if res1 else None, "error": err1})
        if _file_valid_for(res1, fmt):
            final = res1

        if final is None:
            res2, err2 = safe_call(_com_image_export, op, file_path, fmt)
            attempts.append({"channel": "com_image_export", "ok": _file_valid_for(file_path, fmt),
                             "returned": str(res2) if res2 else None, "error": err2})
            if _file_valid_for(file_path, fmt):
                final = file_path

        if final is None:
            res3, err3 = safe_call(_lt_exp_graph, op, graph, file_path, fmt, width)
            attempts.append({"channel": "labtalk_expGraph", "ok": _file_valid_for(file_path, fmt),
                             "returned": str(res3) if res3 else None, "error": err3})
            if _file_valid_for(file_path, fmt):
                final = file_path

        if not _file_valid_for(final, fmt):
            return oerr.fail(
                "export_error",
                f"导出失败：三级通道（save_fig -> COM ImageExport -> LabTalk expGraph）"
                f"均未生成有效文件，目标 {file_path}",
                attempts=attempts, file_path=file_path, format=fmt,
                next_actions=["查看 attempts 字段确认各通道失败原因",
                              "确认输出目录存在且有写权限",
                              "调 origin_diagnose 检查导出目录权限后重试"])
        channel = next((a["channel"] for a in attempts if a["ok"]),
                       "save_fig(expGraph)")
        return {
            "ok": True,
            "file": final,
            "size": os.path.getsize(final),
            "format": fmt,
            "channel": channel,
            "detail": f"已导出 {fmt.upper()} -> {final} ({os.path.getsize(final)} bytes, 通道 {channel})",
        }
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


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
              graph=None, title=None, drop_report_pages=True,
              initial_params=None, fixed_params=None, weight_col=None):
    """拟合：linear（线性）或 Origin 内置拟合函数名（如 ExpDec1/Gauss/...）。

    supported_kinds（2026-09-16 补齐，此前模型名是"暗知识"只能蒙）：
    本机 Origin 2026 常见可用 NLFit 名：ExpDec1 / ExpGrow1 / Gauss / Lorentz /
    Boltzmann / DoseResp / MichaelisMenten / Logistic / Poly2（以 Origin 内置
    函数目录为准，未知名会返回可用列表）。

    drop_report_pages=True（默认）：自动关闭 NLFit 产生的 FitLine*/Residual*
    报告副产品页面 —— 参数与报告已在返回值里，页面留着会爆窗口
    （2026-09-16 实测 7 次 fit 多开 14 页）。

    P0-4（2026-09-16 探针实证 originpro 1.1.15 API）：
    initial_params={"A": 1.5, ...}   NLFit 初值（set_param）；显著影响收敛速度
    fixed_params={"A": true} 或 {"slope": 1.0}
        - NLFit：fix_param(name, True) 固定该参数（不回归，误差 e_=0）
        - linear：{"slope": v} -> lr.fix_slope(v)，{"intercept": v} -> fix_intercept(v)
    weight_col=y误差列               NLFit 加权拟合（set_data 的 yerr 通道；
        linear 不支持加权，显式拒绝）

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
        # 空列 / 有效点过少防护（2026-09-16 d05 实测：column_formula 静默未赋值后
        # 目标列全 missing，拟合器返回 Origin missing 值 -1.23456789e-300 当 slope，
        # 是"假成功"。这里在读数据阶段就拦截。）
        try:
            _xi1 = _col_index_impl(wks, x_column)
            _yi1 = _col_index_impl(wks, y_column)
            if _xi1 is not None and _yi1 is not None:
                _nx = sum(1 for v in wks.to_list(_xi1)
                          if isinstance(v, (int, float)) and v == v)
                _ny = sum(1 for v in wks.to_list(_yi1)
                          if isinstance(v, (int, float)) and v == v)
                if _ny == 0 or _nx == 0:
                    return oerr.fail(
                        "no_data_to_fit",
                        f"拟合列为空（x 有效点 {_nx}，y 有效点 {_ny}）；"
                        "请先确认数据列已写入（例如 column_formula 是否真正生效）",
                        worksheet=str(wks), x_column=str(x_column),
                        y_column=str(y_column))
                if min(_nx, _ny) < 3:
                    return oerr.fail(
                        "insufficient_data",
                        f"有效数据点过少（x {_nx}，y {_ny}），至少需要 3 点才能拟合",
                        worksheet=str(wks), valid_points=min(_nx, _ny))
        except Exception:
            pass
        # NaN 行过滤（2026-09-16 d05 实测：mask_points 屏蔽后的列含 NaN，
        # Origin 拟合器对 NaN 行会产生数值爆炸 slope≈-8e53）。
        # 有 NaN 时把有效行写临时表再拟合。
        nan_dropped = 0
        _initial_applied, _fixed_applied = {}, {}
        _widx = None
        if weight_col is not None:
            _widx = _col_index_impl(wks, weight_col)
            if _widx is None:
                return oerr.fail("invalid_request",
                                 f"weight_col 列不存在: {weight_col!r}",
                                 worksheet=str(wks))
        try:
            import numpy as _npf
            _xi0 = _col_index_impl(wks, x_column)
            _yi0 = _col_index_impl(wks, y_column)
            if _xi0 is not None and _yi0 is not None:
                _xv0 = _npf.asarray(wks.to_list(_xi0), dtype=float)
                _yv0 = _npf.asarray(wks.to_list(_yi0), dtype=float)
                _wv0 = (_npf.asarray(wks.to_list(_widx), dtype=float)
                        if _widx is not None else None)
                _m = _npf.isfinite(_xv0) & _npf.isfinite(_yv0)
                if _wv0 is not None:
                    _m = _m & _npf.isfinite(_wv0)
                if not _m.all():
                    _tmp = op.new_sheet("w", _new_unique_name("FitClean"))
                    _tmp.from_list(0, [round(float(v), 10)
                                       for v in _xv0[_m]], lname="x")
                    _tmp.from_list(1, [round(float(v), 10)
                                       for v in _yv0[_m]], lname="y")
                    if _wv0 is not None:
                        _tmp.from_list(2, [round(float(v), 10)
                                           for v in _wv0[_m]], lname="w")
                    nan_dropped = int((~_m).sum())
                    wks, x_column, y_column = _tmp, 0, 1
                    if _wv0 is not None:
                        weight_col = 2      # 权重列已搬到临时表第 3 列
        except Exception:
            nan_dropped = 0
        if kind == "linear":
            if weight_col is not None:
                return oerr.fail(
                    "invalid_request",
                    "linear 拟合暂不支持 weight_col 加权（originpro LinearFit "
                    "无 yerr 通道）；需要加权请改用 NLFit kind（如 Gauss）",
                    kind="linear", weight_col=str(weight_col))
            lr = op.LinearFit()
            lr.set_data(wks, x_column, y_column)
            if isinstance(fixed_params, dict):
                for _fp, _fv in fixed_params.items():
                    _k = str(_fp).lower()
                    try:
                        if _k in ("slope", "斜率"):
                            lr.fix_slope(float(_fv))
                        elif _k in ("intercept", "截距"):
                            lr.fix_intercept(float(_fv))
                        else:
                            continue
                        _fixed_applied[_k] = float(_fv)
                    except Exception:
                        pass
            res = lr.result()
            try:
                parameters = {
                    "slope": res["Parameters"]["Slope"]["Value"],
                    "slope_error": res["Parameters"]["Slope"].get("Error"),
                    "intercept": res["Parameters"]["Intercept"]["Value"],
                    "intercept_error": res["Parameters"]["Intercept"].get("Error"),
                    "x_intercept": (-res["Parameters"]["Intercept"]["Value"]
                                    / res["Parameters"]["Slope"]["Value"]
                                    if res["Parameters"]["Slope"]["Value"]
                                    else None),
                    "note": "R² 等统计量见报告表（report 字段）；x_intercept 为拟合线"
                            "与 x 轴交点（y=0 处），拟合线交 y 轴即 intercept",
                }
            except Exception:
                parameters = {"raw": res}
            rep, curves = lr.report(0)
            fit_kind = "linear"
        else:
            model = op.NLFit(kind)          # kind = Origin 内置函数名
            # P0-4：weight_col 走 set_data 的 yerr 通道（探针实证签名）
            if weight_col is not None:
                model.set_data(wks, x_column, y_column, yerr=weight_col)
            else:
                model.set_data(wks, x_column, y_column)
            # 初值与固定参数（探针实证：set_param('A', 1.5) / fix_param('A', True)）
            if isinstance(initial_params, dict):
                for _ip, _iv in initial_params.items():
                    try:
                        model.set_param(str(_ip), float(_iv))
                        _initial_applied[str(_ip)] = float(_iv)
                    except Exception:
                        pass
            if isinstance(fixed_params, dict):
                for _fp, _fv in fixed_params.items():
                    if isinstance(_fv, str) and _fv.lower() in ("true", "yes", "1"):
                        _fv = True
                    try:
                        if _fv is True or _fv == 1:
                            model.fix_param(str(_fp), True)
                            _fixed_applied[str(_fp)] = True
                        else:               # 给了数值 = 固定为该值
                            model.set_param(str(_fp), float(_fv))
                            model.fix_param(str(_fp), True)
                            _fixed_applied[str(_fp)] = float(_fv)
                    except Exception:
                        pass
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
            # 固定参数的误差显式回传（固定后 e_=0.0，是"确实没动"的证据）
            for _fk in _fixed_applied:
                _ek = f"e_{_fk}"
                if _ek in res and isinstance(res[_ek], (int, float)):
                    parameters[_ek] = res[_ek]
            fit_kind = kind

        result = {
            "ok": True,
            "kind": fit_kind,
            "parameters": parameters,
            "report": rep,
            "fit_curves": curves,
            "worksheet": str(wks),
            "nan_dropped": nan_dropped,
            "supported_kinds": FIT_SUPPORTED_KINDS,
            "fit_options": {
                "initial_params_applied": _initial_applied,
                "fixed_params_applied": _fixed_applied,
                "weight_col": (str(weight_col)
                               if weight_col is not None else None),
            },
            "detail": (f"{fit_kind} 拟合完成，参数见 parameters"
                       + (f"（已滤除 {nan_dropped} 个 NaN 行）"
                          if nan_dropped else "")),
        }
        # Origin missing 值消毒：Origin 内部缺测用 -1.23456789e-300 表示（非 NaN），
        # 上一版会让它冒充 slope 返回。这里统一替换为 None 并加 warning。
        _MISSING_VAL = -1.23456789e-300
        _miss_keys = []
        for _k, _v in list(parameters.items()):
            if isinstance(_v, (int, float)) and abs(_v - _MISSING_VAL) < 1e-310:
                parameters[_k] = None
                _miss_keys.append(_k)
        if _miss_keys:
            result["warning"] = (f"以下参数为 Origin 缺测值（已置 None）："
                                 f"{', '.join(_miss_keys)}；通常意味着拟合未真正收敛，"
                                 "请检查数据范围或换 kind")

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
                gp = _safe_find_graph(op, graph)
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


# ---------------------------------------------------------------------------
# Origin 原生列公式（数据准确化：ln 等计算走 Origin 表格引擎而非 numpy，
# AI 只负责识图/给原始 csv —— 2026-09-16 v2.3.0）
# ---------------------------------------------------------------------------
# LabTalk 支持的数学函数（probe 实证）：ln=自然对数、log=以 10 为底对数（不是 log10!）、
# exp/sqrt/abs。Origin 的 LabTalk **没有 log10() 函数**，写 log10(x) 不报错但静默不赋值。
_LT_FUNC_ALIASES = {"log10": "log", "log2": "log", "loge": "ln"}

# numpy 复核用的同名映射（LabTalk 函数名 -> numpy 函数）
_NP_FUNC_MAP = {
    "ln": "log", "log": "log10", "exp": "exp", "sqrt": "sqrt",
    "abs": "abs", "sin": "sin", "cos": "cos", "tan": "tan",
    "atan": "arctan", "asin": "arcsin", "acos": "arccos",
    "sinh": "sinh", "cosh": "cosh", "tanh": "tanh", "floor": "floor",
    "ceil": "ceil", "int": "trunc", "sign": "sign",
}


def _column_formula_impl(worksheet, target, formula, lname=None):
    """对目标列写入 LabTalk 公式并立即求值（range 作用域，无需激活任何窗口）。

    Args:
        worksheet: 工作表引用（[BookN]Sheet1）。
        target: 目标列（数字索引 0 起，超出则自动 AddCol；或新列名）。
        formula: LabTalk 表达式，用 col(N)（1 起）引用本表列，如
            "ln(col(2))"、"log(col(2))"（以 10 为底）、"1/col(1)"、
            "col(2)/col(3)*100"、"exp(col(2))"。
            注意：LabTalk 的以 10 为底对数是 log()，**不是 log10()**；
            传 log10() 会被自动纠正为 log()。
        lname: 目标列 long name（可选，默认沿用 formula 提示）。
    返回: {"ok", "worksheet", "column", "points", "first_values", "sample_check"}
        sample_check 用 numpy 对首个有限值复核（数据准确化内置验证）。
        写入后**必须**读回；目标列全空 = 失败（Origin 的 LT_execute 对
        log10() 这类无效函数不报错但静默不写，是典型假成功来源）。
    """
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return oerr.fail("worksheet_not_found", f"工作表不存在: {worksheet}",
                             worksheet=worksheet)
        import re as _re
        if not isinstance(formula, str) or "col(" not in formula:
            return oerr.fail("invalid_request",
                             "formula 必须是含 col(N) 的 LabTalk 表达式，"
                             "如 'ln(col(2))'（N 为 1 起始列号）",
                             formula=str(formula)[:120])
        sheet_ref = str(wks)                       # [BookN]Sheet1
        ncol = int(wks.obj.Cols)

        # ---- 公式规范化：纠正 LabTalk 无效函数名（probe 实证 log10 不存在）----
        norm_formula = formula
        fixed_funcs = []
        for bad, good in _LT_FUNC_ALIASES.items():
            if _re.search(rf"\b{_re.escape(bad)}\s*\(", norm_formula):
                norm_formula = _re.sub(rf"\b{_re.escape(bad)}\s*\(",
                                       f"{good}(", norm_formula)
                fixed_funcs.append(f"{bad}() -> {good}()")
        if "log(" in norm_formula and "log10(" not in norm_formula \
                and "log10()" not in fixed_funcs:
            pass  # log() 已被 Origin 原生支持，无需改写

        # 目标列定位/创建（originpro 无 AddCol()，用 Cols 属性写——probe 实证）
        if isinstance(target, int) or (isinstance(target, str) and target.isdigit()):
            ti = int(target)
            while ti >= int(wks.obj.Cols):
                wks.obj.Cols = int(wks.obj.Cols) + 1
            ci = ti
        else:
            ci = None
            for j in range(int(wks.obj.Cols)):
                try:
                    if (wks.obj[j].GetLongName() or "") == str(target):
                        ci = j
                        break
                except Exception:
                    pass
            if ci is None:
                wks.obj.Cols = int(wks.obj.Cols) + 1
                ci = int(wks.obj.Cols) - 1
                try:
                    wks.obj[ci].SetLongName(str(target))
                except Exception:
                    pass
        if lname:
            try:
                wks.obj[ci].SetLongName(str(lname))
            except Exception:
                pass

        # 目标列清空（避免旧残值被误读为"本次写成功"）
        target_ref = f"{sheet_ref}!col({ci + 1})"
        safe_call(op.po.LT_execute, f"range __clr = {target_ref}; __clr = 0/0;")

        # col(N) → **先绑定为 range 变量**，再把公式里的 col(N) 换成该变量名。
        # probe 实证（V1~V4）：LabTalk 不允许把 `[Book]Sheet!col(N)` 以内联形式
        # 当右值操作数 —— `range __t=[B]S!col(2); __t=ln([B]S!col(1));` 静默不赋值；
        # 必须先 `range __s=[B]S!col(1);` 再 `__t=ln(__s);` 才生效。
        src_cols = sorted({int(m.group(1))
                           for m in _re.finditer(r"col\((\d+)\)", norm_formula)})
        binds = []
        var_of = {}
        for n in src_cols:
            if n == ci + 1:
                var = "__rc"            # 目标列即自身（自引用公式）
            else:
                var = f"__c{n}"
            var_of[n] = var
            binds.append(f"range {var} = {sheet_ref}!col({n});")
        lt_formula = _re.sub(r"col\((\d+)\)",
                             lambda m: var_of.get(int(m.group(1)),
                                                  f"__c{m.group(1)}"),
                             norm_formula)
        scr = (f"range __rc = {target_ref};"
               + "".join(binds)
               + f"__rc = {lt_formula};")
        _, e1 = safe_call(op.po.LT_execute, scr)
        if e1 is not None:
            return oerr.fail("origin_operation_error",
                             f"列公式执行失败: {e1}", script=scr[:200],
                             hint="检查 col(N) 列号是否存在（1 起始）与函数名")
        # 读回验证
        out = wks.to_list(ci)
        n_fin = sum(1 for v in out if isinstance(v, (int, float))
                    and v == v)
        first_vals = [round(float(v), 8) for v in out[:5]
                      if isinstance(v, (int, float))]

        # ---- 硬失败检测：源列有数据但目标列全空 → 静默失败 ----
        if n_fin == 0:
            # 看源列是否有数据（判断是"公式无效"还是"源列本来就空"）
            src_has = False
            for m in _re.finditer(r"col\((\d+)\)", formula):
                sj = int(m.group(1)) - 1
                try:
                    sv = wks.to_list(sj)
                    if any(isinstance(v, (int, float)) and v == v for v in sv):
                        src_has = True
                        break
                except Exception:
                    pass
            return oerr.fail(
                "formula_no_effect",
                f"列公式未产生任何数值（Origin 静默未赋值）: {formula!r}"
                + (f"；已自动纠正: {', '.join(fixed_funcs)}" if fixed_funcs else "")
                + ("；源列有数据，说明公式语法/函数名无效"
                   if src_has else "；源列本身为空，请先填充源列"),
                worksheet=sheet_ref, column=ci, formula=formula,
                normalized_formula=norm_formula,
                script=scr[:200],
                hint="LabTalk 以 10 为底对数是 log() 不是 log10()；"
                     "可用算子 ln/log/exp/sqrt/abs/+ - * / ^")

        # numpy 抽样复核（首个有限值）：把 col(N) 换成占位变量名在 Python 重算。
        # 注意：re.sub 的替换函数**必须返回字符串**，不能返回 ndarray
        # （上一版直接返回数组 → "expected str instance, numpy.ndarray found"）。
        sample = None
        try:
            import numpy as _np
            arrs = {}
            def _col_var(m):
                j = int(m.group(1)) - 1
                vname = f"__a{j}"
                if vname not in arrs:
                    arrs[vname] = _np.asarray(wks.to_list(j), dtype=float)
                return vname
            py_expr = _re.sub(r"col\((\d+)\)", _col_var, norm_formula)
            # LabTalk 函数名 -> numpy（LabTalk 的 log 是底 10，numpy 对应 log10）
            env = {"__builtins__": {}, "np": _np}
            for lt_name, np_name in _NP_FUNC_MAP.items():
                fn = getattr(_np, np_name, None)
                if fn is not None:
                    env[lt_name] = fn
            env.update(arrs)
            py_out = eval(py_expr, {"__builtins__": {}}, env)
            py_out = _np.atleast_1d(_np.asarray(py_out, dtype=float))
            fin = [k for k, v in enumerate(out)
                   if isinstance(v, (int, float)) and v == v]
            if fin:
                k0 = fin[0]
                ov = float(out[k0])
                pv = float(py_out[k0]) if k0 < len(py_out) else float("nan")
                match = (pv == pv
                         and abs(ov - pv) <= max(1e-9, 1e-9 * abs(pv)))
                sample = {"row": k0, "origin": ov, "numpy": pv,
                          "match": bool(match),
                          "checked": "首个有限值 numpy 独立复算"}
        except Exception as _se:
            sample = {"skipped": f"numpy 复核跳过: {_se}"}

        detail = (f"列公式 {formula!r} -> col({ci + 1}) 完成"
                  f"（{n_fin} 个有限值，Origin 原生计算）")
        if fixed_funcs:
            detail += f"；已自动纠正 {', '.join(fixed_funcs)}"
        result = oerr.ok(worksheet=sheet_ref, column=ci,
                         column_name=(lname or str(target)),
                         points=len(out), finite_points=n_fin,
                         first_values=first_vals, sample_check=sample,
                         detail=detail)
        if fixed_funcs:
            result["normalized_formula"] = norm_formula
            result["auto_fixes"] = fixed_funcs
        # numpy 复核不一致 → 提示但不失败（可能是 LabTalk 与 numpy 的边界差异）
        if isinstance(sample, dict) and sample.get("match") is False:
            result["warning"] = (f"numpy 复核不一致（row {sample['row']}: "
                                 f"Origin={sample['origin']:.6g} vs "
                                 f"numpy={sample['numpy']:.6g}），请人工确认公式语义")
        return result
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


# ---------------------------------------------------------------------------
# 多峰拟合（核磁/XPS/拉曼/红外分峰：numpy Levenberg-Marquardt，自包含）
# ---------------------------------------------------------------------------
def _peak_shape(x, c, h, w, kind):
    """单峰：h 峰高、w 半高宽 FWHM。gauss/lorentz。"""
    import numpy as _np
    if kind == "lorentz":
        return h / (1.0 + 4.0 * ((x - c) / w) ** 2)
    return h * _np.exp(-4.0 * _np.log(2.0) * ((x - c) / w) ** 2)


def _peak_area(h, w, kind):
    import numpy as _np
    if kind == "lorentz":
        return h * w * _np.pi / 2.0
    return h * w * _np.sqrt(_np.pi / (4.0 * _np.log(2.0)))


def _peak_fit_impl(worksheet, x_column, y_column, n_peaks=1, kind="gauss",
                   centers_hint=None, baseline=True, plot_curve=True,
                   graph=None, title=None, show_components=True):
    """多峰拟合（分峰解析）。每峰返回 center/height/fwhm/area + 总拟合 R²。

    初值：centers_hint 给定峰位列表；否则用 peak_find 自动探测。
    LM 阻尼高斯牛顿迭代（数值雅可比），纯 numpy 实现，不依赖 Origin 拟合器。
    """
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        import numpy as np
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        xi = _col_index_impl(wks, x_column)
        yi = _col_index_impl(wks, y_column)
        if xi is None or yi is None:
            return {"ok": False, "error": "x/y 列不存在"}
        xv = np.asarray(wks.to_list(xi), dtype=float)
        yv = np.asarray(wks.to_list(yi), dtype=float)
        mask = np.isfinite(xv) & np.isfinite(yv)
        xv, yv = xv[mask], yv[mask]
        kind = (kind or "gauss").lower()
        if kind not in ("gauss", "lorentz"):
            return oerr.fail("invalid_request", "kind 支持 gauss/lorentz")

        # ---- 初值 ----
        n_peaks = int(n_peaks or 1)
        if centers_hint:
            centers = [float(c) for c in centers_hint][:n_peaks]
        else:
            rpk = _peak_find_impl(worksheet, x_column, y_column,
                                  min_height=float(yv.max() * 0.1),
                                  min_distance=max(3, len(xv) // 40))
            peaks0 = rpk.get("peaks") or []
            centers = [float(p.get("x")) for p in peaks0][:n_peaks]
        while len(centers) < n_peaks:     # 不够就均匀铺开
            centers.append(float(xv[0] + (len(centers) + 1)
                               * (xv[-1] - xv[0]) / (n_peaks + 1)))
        yspan = float(yv.max() - yv.min())
        # abs 取幅值：XPS/FTIR 等"结合能/波数降序"数据 x[-1]<x[0]，
        # 负 xspan 会把 FWHM 初值钳到 1e-9 让 LM 全灭（d02 实测教训）
        xspan = abs(float(xv[-1] - xv[0]))
        p0 = [float(yv.min() if baseline else 0.0)]
        for c in centers:
            p0 += [c, max(yspan, 1e-9), max(xspan / (4.0 * n_peaks), 1e-9)]
        p = np.asarray(p0, dtype=float)

        def model(pp, x):
            y = np.full_like(x, pp[0])
            for i in range(n_peaks):
                y = y + _peak_shape(x, pp[1 + 3 * i], pp[2 + 3 * i],
                                    pp[3 + 3 * i], kind)
            return y

        # ---- LM 迭代（数值雅可比） ----
        lam, best = 1e-3, None
        r = yv - model(p, xv)
        cost = float(np.dot(r, r))
        for _ in range(300):
            J = np.empty((xv.size, p.size))
            for j in range(p.size):
                dp = max(1e-8, abs(p[j]) * 1e-6)
                pj = p.copy()
                pj[j] += dp
                J[:, j] = (model(pj, xv) - model(p, xv)) / dp
            JTJ, JTr = J.T @ J, J.T @ r
            improved = False
            for _inner in range(20):
                try:
                    d = np.linalg.solve(JTJ + lam * np.diag(np.diag(JTJ)), JTr)
                except np.linalg.LinAlgError:
                    lam *= 10
                    continue
                pn = p + d
                # 约束：fwhm>0、height 不为大负
                for i in range(n_peaks):
                    pn[3 + 3 * i] = abs(pn[3 + 3 * i])
                rn = yv - model(pn, xv)
                cn = float(np.dot(rn, rn))
                if cn < cost:
                    p, r, cost = pn, rn, cn
                    lam = max(lam / 10.0, 1e-9)
                    improved = True
                    break
                lam *= 10.0
            if not improved and lam > 1e8:
                break
        ss_tot = float(np.dot(yv - yv.mean(), yv - yv.mean()))
        r2 = 1.0 - cost / ss_tot if ss_tot > 0 else 0.0
        peaks_out = []
        for i in range(n_peaks):
            c, h, w = p[1 + 3 * i], p[2 + 3 * i], p[3 + 3 * i]
            peaks_out.append({"center": round(float(c), 6),
                              "height": round(float(h), 6),
                              "fwhm": round(float(w), 6),
                              "area": round(float(_peak_area(h, w, kind)), 6)})
        result = {"ok": True, "kind": kind, "n_peaks": n_peaks,
                  "baseline": round(float(p[0]), 6),
                  "peaks": peaks_out, "r_squared": round(r2, 6),
                  "worksheet": str(wks),
                  "detail": f"{n_peaks} 峰 {kind} 拟合完成，R²={r2:.5f}"}

        # ---- 上图：原始散点 + 总拟合线 + 各分峰 ----
        if plot_curve:
            xs = np.linspace(xv[0], xv[-1], max(400, xv.size))
            yfit = model(p, xs)
            ws2 = op.new_sheet("w", _new_unique_name("PeakFit"))
            ws2.from_list(0, [round(float(v), 8) for v in xs], lname="x_fit")
            ws2.from_list(1, [round(float(v), 8) for v in yfit], lname="y_total")
            if show_components:
                for i in range(n_peaks):
                    yc = _peak_shape(xs, p[1 + 3 * i], p[2 + 3 * i],
                                     p[3 + 3 * i], kind) + p[0]
                    ws2.from_list(2 + i, [round(float(v), 8) for v in yc],
                                  lname=f"peak_{i + 1}")
            if graph:
                gp = _safe_find_graph(op, graph)
            else:
                gp = None
            if gp is None:
                gp = op.new_graph(lname=title or "PeakFit")
            gl = gp[0]
            gl.add_plot(wks, yi, xi, type="s")
            gl.add_plot(ws2, 1, 0, type="l")
            if show_components:
                for i in range(n_peaks):
                    gl.add_plot(ws2, 2 + i, 0, type="l")
            gl.rescale()
            try:
                pls = gl.plot_list() or []
                if pls:
                    pls[0].symbol_size = 4
                if len(pls) > 1:
                    pls[1].color = (204, 26, 26)
                for i in range(2, len(pls)):
                    pls[i].set_int("show", 1)
                    pls[i].color = (90, 90, 90)
                    try:
                        pls[i].set_cmd("-wp 1")
                    except Exception:
                        pass
            except Exception:
                pass
            result["graph"] = gp.obj.GetName()
            result["detail"] += f"，拟合曲线已上图（{result['graph']}）"
        return result
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


# ---------------------------------------------------------------------------
# 屏蔽数据点（NaN 化隐藏错误点，不物理删除 —— 高度自定义化）
# ---------------------------------------------------------------------------
def _mask_points_impl(worksheet, y_column, rows=None, x_min=None, x_max=None,
                      x_column=None, backup=True):
    """把指定行/区间的 y 值置为 NaN（图上自动隐藏，数据可恢复）。

    rows: 行号列表（0 起）；或 x_min/x_max 区间（配合 x_column）。
    backup=True 时先把原列复制为 <name>_raw 再置 NaN（可逆）。
    """
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        import numpy as np
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return {"ok": False, "error": f"工作表不存在: {worksheet}"}
        yi = _col_index_impl(wks, y_column)
        if yi is None:
            return {"ok": False, "error": f"列不存在: {y_column}"}
        yv = np.asarray(wks.to_list(yi), dtype=float)
        n = yv.size
        sel = np.zeros(n, dtype=bool)
        if rows:
            for r0 in rows:
                try:
                    i0 = int(r0)
                    if 0 <= i0 < n:
                        sel[i0] = True
                except (TypeError, ValueError):
                    continue
        if x_min is not None or x_max is not None:
            xi = _col_index_impl(wks, x_column if x_column is not None else 0)
            if xi is None:
                return {"ok": False, "error": "x 列不存在（区间屏蔽需要 x_column）"}
            xv = np.asarray(wks.to_list(xi), dtype=float)
            lo = float(x_min) if x_min is not None else -np.inf
            hi = float(x_max) if x_max is not None else np.inf
            sel |= (xv >= lo) & (xv <= hi)
        n_mask = int(sel.sum())
        if n_mask == 0:
            # 区分"区间没命中"与"整列没数据/全 NaN"（后者多半是上游列公式未生效）
            n_valid_y = int(np.isfinite(yv).sum())
            if n < 1:
                return oerr.fail(
                    "column_empty",
                    f"y 列没有行数据（列长度 0），无法屏蔽任何点；"
                    f"请确认上游列公式（column_formula）是否真正写入了数据",
                    worksheet=str(wks), y_column=str(y_column))
            if n_valid_y == 0:
                return oerr.fail(
                    "column_all_nan",
                    f"y 列 {n} 行全部为 NaN，没有可屏蔽的有效点；"
                    f"请检查上游列公式是否真正生效",
                    worksheet=str(wks), y_column=str(y_column), rows=n)
            return oerr.fail("invalid_request", "没有命中任何数据点",
                             rows=(rows or [])[:10], x_min=x_min, x_max=x_max,
                             y_column=str(y_column), rows_total=n,
                             valid_points=n_valid_y,
                             hint="rows 是 0 起行号；x_min/x_max 需配合 x_column")
        backup_col = None
        if backup:
            cname = _col_name_impl(wks, yi)
            backup_col = _write_col_impl(wks, list(yv), lname=f"{cname}_raw")
        yv[sel] = np.nan
        wks.from_list(yi, [None if (isinstance(v, float) and v != v)
                           else round(float(v), 10) if v == v else v
                           for v in yv])
        return oerr.ok(worksheet=str(wks), masked=n_mask,
                       backup_column=backup_col,
                       detail=(f"已屏蔽 {n_mask} 个点（置 NaN，图上隐藏）"
                               + (f"，原值备份于 {backup_col}" if backup_col else "")))
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


# ---------------------------------------------------------------------------
# 辅助线（Tafel 外推/零参考/阈值线）
# ---------------------------------------------------------------------------
def _add_line_impl(graph, orientation="vertical", at=None, slope=None,
                   intercept=None, color="#D55E00", line_style=1,
                   label=None, layer=0):
    """在图上画辅助线。vertical/horizontal 需 at（轴截点）；slope 需
    slope+intercept（y=slope*x+intercept）。线会进图例（条目=label 或列名）。
    """
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        gp = _safe_find_graph(op, graph)
        if gp is None:
            return oerr.fail("graph_not_found", f"图不存在: {graph}", graph=graph)
        gl, _ = safe_call(gp.__getitem__, int(layer or 0))
        if gl is None:
            return oerr.fail("layer_not_found", f"图层不存在: {layer}",
                             graph=graph)
        orientation = (orientation or "vertical").lower()
        try:
            xl = gl.axis("x").limits
            yl = gl.axis("y").limits
        except Exception:
            xl = yl = None
        xs, ys = None, None
        if orientation == "vertical":
            if at is None:
                return oerr.fail("invalid_request", "vertical 需要 at（x 截点）")
            y0, y1 = (float(yl[0]), float(yl[1])) if yl else (0.0, 1.0)
            xs, ys = [float(at), float(at)], [y0, y1]
        elif orientation == "horizontal":
            if at is None:
                return oerr.fail("invalid_request", "horizontal 需要 at（y 截点）")
            x0, x1 = (float(xl[0]), float(xl[1])) if xl else (0.0, 1.0)
            xs, ys = [x0, x1], [float(at), float(at)]
        elif orientation == "slope":
            if slope is None or intercept is None:
                return oerr.fail("invalid_request",
                                 "slope 线需要 slope + intercept")
            x0, x1 = (float(xl[0]), float(xl[1])) if xl else (0.0, 1.0)
            xs = [x0, x1]
            ys = [float(slope) * x0 + float(intercept),
                  float(slope) * x1 + float(intercept)]
        else:
            return oerr.fail("invalid_request",
                             "orientation 支持 vertical/horizontal/slope")
        ws = op.new_sheet("w", _new_unique_name("RefLine"))
        ws.from_list(0, xs, lname="ref_x")
        ws.from_list(1, ys, lname=str(label or "reference"))
        pl = gl.add_plot(ws, 1, 0, type="l")
        try:
            pl.color = _hex_to_rgb_tuple(color)
        except Exception:
            pass
        try:
            pl.set_int("linestyle", int(line_style))
        except Exception:
            pass
        if label:
            try:
                gl.add_label(str(label), xs[1] if orientation == "vertical"
                             else xs[0], ys[0] if orientation != "vertical"
                             else ys[1])
            except Exception:
                pass
        return oerr.ok(graph=graph, orientation=orientation,
                       points=[xs, ys],
                       detail=f"辅助线已画（{orientation}"
                              f"{f' at={at}' if at is not None else ''}）-> {graph}")
    except Exception as e:
        return {"ok": False, "error": f"{e}", "trace": traceback.format_exc(limit=3)}


# ---------------------------------------------------------------------------
# 任意 LabTalk 执行（逃生舱；带激活 + 读回 + NaN 防护）
# ---------------------------------------------------------------------------
# P0-2（2026-09-16）：LabTalk 破坏命令门禁。语句首 token 匹配黑名单即拒绝，
# confirm=True 显式放行。字符串字面量与注释内的 token 豁免（分词时维护引号状态）。
_LABTALK_DESTRUCTIVE = {
    "delete",     # 删除对象（delete Book1 / delete %H...）
    "exit",       # 退出 Origin
    "quit",       # 退出 Origin（别名）
    "doc",        # doc -s 清空工程 / doc -uw 等破坏性子开关
    "kill",       # 删除数据集/变量
    "purge",      # 清理工程对象
}
_LABTALK_DESTRUCTIVE_SUBCMD = {"doc": {"-s", "-sr", "-uw"}}


def _labtalk_gate(script):
    """返回 None（放行）或 (bad_token, context)（拦截）。P0-2。

    按语句（分号/换行分隔）取首 token；引号内的内容跳过，避免
    'wks.colWidth$="delete"' 这类字符串误杀。
    """
    s = str(script or "")
    tokens, i, n, in_str = [], 0, len(s), False
    cur = []
    statements = []
    while i < n:
        ch = s[i]
        if in_str:
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            if cur:
                statements.append("".join(cur))
                cur = []
            i += 1
            continue
        if ch in ";#\n":
            if cur:
                statements.append("".join(cur))
                cur = []
            if ch == "#":          # 注释：跳过本行剩余
                while i < n and s[i] not in "\n":
                    i += 1
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        statements.append("".join(cur))
    for stmt in statements:
        parts = stmt.strip().split()
        if not parts:
            continue
        head = parts[0].lower()
        if head in _LABTALK_DESTRUCTIVE:
            sub = {p.lower() for p in parts[1:]}
            if head == "doc" and not (sub & _LABTALK_DESTRUCTIVE_SUBCMD["doc"]):
                continue          # doc 非 -s/-sr/-uw 子开关放行
            return head, stmt.strip()[:120]
        # win -c（关窗口）/win -ch 也破坏
        if head == "win" and len(parts) > 1 and parts[1].lower() in ("-c", "-ch", "-cd"):
            return "win -c", stmt.strip()[:120]
    return None


# 阶段 D（2026-09-17）：LabTalk **静默失败**陷阱名单（甲烷 NMR 案例复盘）。
# 这些命令不报错、不返回错误码，只会"悄悄不干活"（写空列/中断脚本/读不到值），
# AI 只能靠渲染-看图-再调来发现——每踩一个坑烧 2-4 轮。这里在引擎层直接拦截
# 并给出替代写法，把"文档级提醒"升级为"引擎级门禁"。
import re as _re

_LABTALK_SILENT_TRAPS = [
    (_re.compile(r"\bgrand\s*\(", _re.I),
     "grand()（高斯随机）在本构建不可用且静默写空。替代：rnd()（均匀随机），"
     "或在数据源端预生成高斯噪声"),
    (_re.compile(r"\bdata\s*\(\s*-?\d", _re.I),
     "data(n1,n2) 在本构建不填充列（静默失败）。替代：loop(ii,1,n) 显式逐行赋值"),
    (_re.compile(r"\[LName\]\s*\$", _re.I),
     "col(N)[LName]$ 会静默中断脚本。替代：wks.colN.lname$ = \"长名\""),
    (_re.compile(r"\bnlabels\b|\blabel\.count\b|\bnobjects\b", _re.I),
     "label.count / layer.nlabels / layer.nobjects 在本构建不可解析。"
     "替代：标注对象自动命名 Text1..TextN，用 origin_layout_info 列出"),
    (_re.compile(r"type\s*:?=\s*(204|215)\b", _re.I),
     "plotxy 的 type 204/215 在 2026b 不可用（已知风险）。"
     "替代：originpro gl.add_plot(..., type='l'/'s'/'y'/'c')"),
]


def _labtalk_silent_trap(script):
    """返回 None（放行）或 (trap_label, alternative, statement)。

    与破坏门禁不同：静默陷阱**默认拦截**（因为它们必然造成隐性返工），
    force_silent=True 时放行给确知风险的高级用户。
    """
    s = str(script or "")
    for pat, alt in _LABTALK_SILENT_TRAPS:
        m = pat.search(s)
        if m:
            frag = s[max(0, m.start() - 20):m.end() + 40].strip()
            return pat.pattern[:36], alt, frag[:120]
    return None


def _labtalk_impl(script, read_expr=None, graph=None, read_kind="auto",
                  confirm=False, force_silent=False):
    """执行一段 LabTalk 并可选读回表达式值（带激活复核与 NaN 判定）。

    这是给高级用户的逃生舱：SKILL 里没有覆盖到的 Origin 功能可由此直达。
    graph 给定且非空时先激活该图页（否则 LabTalk 裸表达式可能静默落到
    错误窗口 —— 通道纪律，见 SKILL 附录 C）。
    read_expr: 如 "layer.x.from"、"page.nlayers"、'layer.y.title$'。
    confirm=True 时放行破坏性命令（delete/doc -s/exit 等，P0-2 默认拦截）。
    force_silent=True 时放行已知静默失败命令（grand()/data()/[LName]$ 等，
    阶段 D 默认拦截并给替代写法——这些命令不报错只悄悄不干活，返工成本极高）。
    """
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        # P0-2：破坏命令门禁（confirm=True 显式放行）
        bad = _labtalk_gate(script)
        if bad is not None and not confirm:
            return oerr.fail(
                "labtalk_blocked",
                f"脚本包含破坏性命令 {bad[0]!r}（语句: {bad[1]}），默认拦截",
                script=str(script)[:200],
                next_actions=["确认无误后带 confirm=true 重发",
                              "或改用等价的非破坏命令"],
                destructive_token=bad[0], statement=bad[1])
        # 阶段 D：静默失败陷阱门禁（force_silent=True 显式放行）
        if not force_silent:
            trap = _labtalk_silent_trap(script)
            if trap is not None:
                return oerr.fail(
                    "labtalk_silent_trap",
                    f"脚本包含已知静默失败命令 {trap[0]!r}（片段: {trap[2]}），"
                    "默认拦截——这些命令不报错但悄悄不干活",
                    script=str(script)[:200],
                    alternative=trap[1],
                    next_actions=["按 alternative 里的替代写法改写脚本",
                                  "确知风险仍要执行则带 force_silent=true 重发"],
                    trap=trap[0])
        op = _origin_app
        # 阻塞型命令防护（d15 实测：type -b 弹模态对话框把 Origin 卡死 10 分钟，
        # COM 全程无响应，只能 GUI 点击解除）。逃生舱不放火烧船。
        low = str(script).lower()
        for bad in ("type -b", "type -a", "dlg.", "dlg ", "getn", "getstr"):
            if bad in low:
                return oerr.fail(
                    "invalid_request",
                    f"脚本含阻塞型命令 {bad.strip()!r}（会弹模态对话框卡死 Origin）",
                    script=str(script)[:200],
                    next_actions=["改用纯计算/赋值命令；交互式弹窗只能在 Origin 内手动操作"])
        activated = None
        if graph:
            import origin_edit as _oedit
            _, aerr = _oedit.ensure_active_graph(op, op.po, graph)
            if aerr is not None:
                return oerr.fail("window_activation_failed",
                                 f"目标图页激活失败: {aerr}", graph=graph,
                                 next_actions=["origin_list_pages 复核短名后重试"])
            activated = True
        _, e1 = safe_call(op.po.LT_execute, str(script))
        if e1 is not None:
            return oerr.fail("origin_operation_error",
                             f"LabTalk 执行失败: {e1}",
                             script=str(script)[:200])
        out = {"ok": True, "executed": True, "graph": graph,
               "activated": activated}
        if read_expr:
            expr = str(read_expr)
            is_str = expr.endswith("$")
            if read_kind == "str":
                is_str = True
            elif read_kind == "float":
                is_str = False
            if is_str:
                v = _lt_read_str(expr)
            else:
                v = _lt_read_float(expr)
            if isinstance(v, float) and v != v:  # NaN 自比较判定（无 import 依赖）
                out["readback"] = None
                out["readback_status"] = "nan_unreliable"
                out["detail"] = ("读回为 NaN：该表达式在当前上下文未解析"
                                 "（检查 graph 是否活动窗口 / 属性名是否正确）")
            elif v is None:
                out["readback"] = None
                out["readback_status"] = "unreadable"
            else:
                out["readback"] = v
                out["readback_status"] = "ok"
                out["detail"] = f"执行完成，{expr} = {v!r}"
        else:
            out["detail"] = "LabTalk 已执行（无读回请求；建议带 read_expr 复核）"
        return out
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
            gp = _safe_find_graph(op, gname)
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


# 自研统计的科学边界（#9）：探索性结论可用，正式发表需专业软件复核
CONFIDENCE_NOTE = ("本结果由插件内置统计实现（numpy）快速计算，仅供探索性分析与图表初稿；"
                   "正式发表前请用 SPSS/R 或 Origin 原生统计功能复核。")


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
                "confidence_note": CONFIDENCE_NOTE,
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
            "confidence_note": CONFIDENCE_NOTE,
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
            "origin_diagnose": "系统级自检：Origin 安装/COM 注册/残留进程/导出目录权限（连接失败先调它）",
            "origin_cookbook": "场景→工具组合速查：常见调用链 + 推荐默认参数 + 快速/正式路径",
            "origin_release": "释放自动化连接（Origin 保持打开交给用户手动操作；下次调用自动重连）",
            "origin_reconnect": "显式重连 Origin（release 后恢复；已连接时幂等）",
            "origin_manage_plots": "数据图管理：remove 删曲线 / change_data 换数据源",
            "origin_manage_data": "工作表数据管理：sort 按列排序整表 / transpose 行列转置（新表）",
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
        gp = _safe_find_graph(op, graph)
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
    """从图层第一条曲线的 dataset 名（如 "Book1_B"）解析出其工作簿。

    originpro plot 对象没有 ws 属性（probe 实证全是 None），
    唯一可靠通道是 p.name 的 "BookN_列" 前缀。
    """
    try:
        pl = gl.plot_list() or []
        if pl:
            nm = getattr(pl[0], "name", None)
            if nm:
                book = str(nm).split("_")[0].split("]")[0].strip("[")
                return _origin_app.find_sheet("w", book)
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
                       column_b=str(column_b) if column_b else None,
                       confidence_note=CONFIDENCE_NOTE, **r)
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
        return oerr.ok(worksheet=str(wks), columns=used,
                       confidence_note=CONFIDENCE_NOTE, **r)
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
                       n_columns=len(used), confidence_note=CONFIDENCE_NOTE, **r)
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
                       event_column=str(event_column),
                       confidence_note=CONFIDENCE_NOTE, **r)
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
                if _safe_find_graph(op, name) is not None:
                    names.append(name)
            except Exception:
                pass
        return oerr.ok(pages=names, count=len(names),
                       detail=f"共 {len(names)} 个图页")
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def _error_codes_impl():
    """列出全部稳定错误码、恢复建议与恢复动作映射。"""
    out = {}
    for code, (recoverable, actions) in oerr.CODE_META.items():
        out[code] = {"recoverable": recoverable, "next_actions": actions,
                     "recovery": oerr.recovery_info(code)}
    return oerr.ok(error_codes=out, count=len(out))


# ---------------------------------------------------------------------------
# 系统级诊断（#5）：不依赖 COM 连接，连接/导出失败时先跑它定位
# ---------------------------------------------------------------------------
_ORIGIN_INSTALL_GLOBS = [
    r"C:\Program Files\OriginLab\Origin*\Origin64.exe",
    r"C:\Program Files (x86)\OriginLab\Origin*\Origin64.exe",
    r"D:\Program Files\OriginLab\Origin*\Origin64.exe",
    r"C:\OriginLab\Origin*\Origin64.exe",
]


def _probe_com_registration():
    """只读注册表探测 COM 注册（不启动 Origin，无副作用）。"""
    try:
        import winreg
        for key in ("Origin.ApplicationSI", "Origin.Application"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key):
                    return True, f"HKCR\\{key} 已注册"
            except OSError:
                continue
        return False, "HKCR 未找到 Origin.ApplicationSI / Origin.Application（COM 组件未注册）"
    except Exception as e:      # 非 Windows / 权限受限环境
        return None, f"注册表探测失败: {type(e).__name__}: {e}"


def _probe_dir_writable(path):
    """探针文件法验证目录可写（创建-写入-删除）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".dsh_write_probe_{uuid.uuid4().hex[:6]}.tmp")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("probe")
        os.remove(probe)
        return True, "可写"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _diagnose_impl(connect_probe=False):
    """系统级自检：Origin 安装 / COM 注册 / 残留进程 / 导出目录权限 / 环境策略。"""
    report = {"ok": True, "checks": {}, "recommendations": []}
    checks = report["checks"]
    recs = report["recommendations"]

    # 1) Origin 安装探测
    import glob as _glob
    installs = []
    for pattern in _ORIGIN_INSTALL_GLOBS:
        try:
            installs.extend(_glob.glob(pattern))
        except Exception:
            pass
    checks["origin_install"] = {
        "found": bool(installs), "exes": installs,
        "note": None if installs else "常见路径未找到 Origin64.exe",
    }
    if not installs:
        recs.append("未在常见安装路径找到 Origin64.exe：确认已安装 OriginLab Origin，"
                    "或把实际安装路径告知支持人员")

    # 2) Origin 进程状态 + 自动清理策略
    nproc = _origin_proc_count()
    checks["origin_processes"] = {"count": nproc, "running": bool(nproc)}
    checks["autokill"] = {
        "enabled": _autokill_enabled(),
        "env_DSH_ORIGIN_AUTOKILL": os.environ.get("DSH_ORIGIN_AUTOKILL"),
    }
    if nproc and nproc > 1:
        recs.append(f"检测到 {nproc} 个 Origin64 进程：多实例会让 COM 连错实例，"
                    "taskkill /F /IM Origin64.exe 清理后等 2 秒再重连"
                    "（或设置 DSH_ORIGIN_AUTOKILL=1 让引擎连接前自动清理）")

    # 3) COM 注册探测（只读，无副作用）
    com_ok, com_detail = _probe_com_registration()
    checks["com_registration"] = {"ok": com_ok, "detail": com_detail}
    if com_ok is False:
        recs.append("Origin COM 组件未注册：重装/修复 Origin，或以管理员运行一次 Origin")

    # 4) 引擎连接状态（不主动连接）
    checks["engine_connected"] = {"connected": bool(_connected and _origin_app),
                                  "com_thread_alive": bool(_com_thread and _com_thread.is_alive())}
    checks["env_policy"] = {
        "ORIGIN_SESSION": os.environ.get("ORIGIN_SESSION", "attach（默认）"),
        "DSH_ORIGIN_AUTOKILL": os.environ.get("DSH_ORIGIN_AUTOKILL") or "未设置",
        "DSH_ORIGIN_NO_AUTO_SAVE": os.environ.get("DSH_ORIGIN_NO_AUTO_SAVE") or "未设置",
        "DSH_ORIGIN_IPC_LOCK": os.environ.get("DSH_ORIGIN_IPC_LOCK") or "未设置",
    }

    # 5) 默认导出目录可写性
    dir_ok, dir_detail = _probe_dir_writable(DEFAULT_OUTPUT_DIR)
    checks["export_dir"] = {"path": DEFAULT_OUTPUT_DIR,
                            "writable": dir_ok, "detail": dir_detail}
    if not dir_ok:
        recs.append(f"默认导出目录不可写（{dir_detail}）：导出时显式传 file_path 或 output_dir")

    # 5.5) Origin 自动化能力探测（2026-09-18 用户现场实证：Origin 2024 SR1 上
    # 「新建文档窗口」与「导出文件」两类操作被运行时静默禁用——newbook/expGraph
    # 一律返回 False 且不报错，排查花了半小时。这里提前探测，一次说清楚。）
    if connect_probe:
        # 注意：_diagnose_impl 本身运行在 COM 线程内，**必须调裸 _connect_impl**，
        # 调 @_synchronized 包装的 connect() 会二次投递队列导致死锁（项目纪律）。
        ok_c, conn0 = _connect_impl()
        if isinstance(conn0, tuple) and len(conn0) == 2 and isinstance(conn0[1], dict):
            conn0 = conn0[1]
        if ok_c:
            checks["automation_capability"] = _probe_automation_capability()
            ac = checks["automation_capability"]
            if not ac.get("all_ok"):
                report["ok"] = False
                recs.append(
                    "Origin 自动化能力受限：" + "；".join(ac.get("issues") or [])
                    + " ——命令行/GUI 手动试一次：Origin 里手动新建工作簿并导出 PNG；"
                      "手动也失败说明安装损坏 → 控制面板 → Origin → 更改 → 修复；"
                      "手动成功说明只是 COM 附加模式受限 → 让 AI 只写数据、图上你在 GUI 里导出")
        checks["connect_probe"] = conn0
        if not ok_c:
            report["ok"] = False
    else:
        recs.append("需要判断 Origin 是否能真正建图/导出时，带 connect_probe=true 重跑"
                    "（会额外做新建窗口与导出能力探测，5~45 秒）")

    report["recommendations"] = recs
    return report


def _probe_automation_capability():
    """探测 Origin 自动化是否真的可用：新建文档窗口 + 导出文件。

    两类操作在某些受限运行时（实证：Origin 2024 SR1 受限模式）会**静默失败**——
    命令返回 False 但不抛异常，导致上层以为"执行了"却没有产物。
    这里主动试探，把"静默失败"变成"明确报告"。
    """
    issues, detail = [], {}
    op = _origin_app
    # a) 新建工作簿能力
    try:
        wks = op.new_sheet("w", "DSH_CAP_PROBE")
        ok_new = wks is not None
    except Exception as e:
        ok_new, wks = False, None
        detail["newdoc_error"] = str(e)[:120]
    if wks is not None:
        try:
            wks.destroy()
        except Exception:
            try:
                op.po.LT_execute("window -c DSH_CAP_PROBE;")
            except Exception:
                pass
    detail["new_doc"] = bool(ok_new)
    if not ok_new:
        issues.append("newbook/新建工作表 被静默拒绝")

    # b) 导出能力：建临时图 → 导出 → 校验文件（不依赖 __LASTEXP 变量回读，
    #    因为受限环境下系统变量读不出来，只返回空串——实证坑）
    ok_exp, exp_file = False, None
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="dsh_exp_probe_")
        gp = None
        try:
            wks2 = op.new_sheet("w", "DSH_EXP_PROBE")
            if wks2 is not None:
                try:
                    wks2.from_list(0, [1.0, 2.0, 3.0], lname="x")
                    wks2.from_list(1, [1.0, 4.0, 9.0], lname="y")
                except Exception:
                    pass
                gp = op.new_graph(lname="DSH_EXP_GRAPH")
                gl = gp[0] if gp else None
                if gl is not None:
                    try:
                        gl.add_plot(wks2, 1, 0)
                        gl.rescale()
                    except Exception:
                        pass
        except Exception:
            gp = None
        if gp is not None:
            exp_file = os.path.join(tmpdir, "probe.png")
            try:
                gp.save_fig(exp_file, width=600)
            except Exception:
                pass
            ok_exp = bool(exp_file and os.path.isfile(exp_file)
                          and os.path.getsize(exp_file) > 0)
            try:
                op.po.LT_execute("window -c DSH_EXP_GRAPH; window -c DSH_EXP_PROBE;")
            except Exception:
                pass
    except Exception as e:
        detail["export_error"] = str(e)[:120]
    detail["export"] = bool(ok_exp)
    detail["export_probe_file"] = exp_file
    if not ok_exp:
        issues.append("expGraph/导出 被静默拒绝（或只产生空文件）")

    return {"all_ok": not issues, "issues": issues, "detail": detail,
            "note": ("探测用临时对象已清理。这两类操作失败时 Origin 不报错——"
                     "上层一律以「文件是否真的落盘」裁决，不信任通道返回值。")}


# ---------------------------------------------------------------------------
# 场景速查（#22/#20/#23）：常见调用链 + 推荐默认参数 + 快速/正式路径
# ---------------------------------------------------------------------------
_COOKBOOK_SCENARIOS = {
    "quick_plot": {
        "title": "快速出图（最快路径，数据已在手上）",
        "steps": [
            "origin_plot_file(columns={'x':[..], 'y':[..]}, plot_type='line_symbol', "
            "fmt='png', width=1200, title='可选')",
            "返回 {'ok': true, 'file': 绝对路径} 即完成；需要微调再走 origin_edit_*",
        ],
    },
    "from_file": {
        "title": "从 CSV/Excel 导入并画图",
        "steps": [
            "origin_load_file(path='D:/data/样品1.csv') -> 看列画像与角色建议",
            "origin_plot(worksheet, y_columns=[..], x_column=.., plot_type='line_symbol')",
            "origin_export(graph, fmt='png', width=1200)",
        ],
    },
    "journal": {
        "title": "发表级图表（正式路径：plan → 确认 → execute → verify → delivery）",
        "steps": [
            "origin_plot_plan(columns=...) -> 逐列画像 + 待确认问题（plan_id + plan_hash）",
            "有 questions 先向用户确认，再 origin_execute_plan(plan_id)",
            "origin_verify_graph(graph) 确定性反读核验（轴标题/字号/图例/文件完整性）",
            "origin_export_delivery(graph, source_path=数据文件, fmts='png,pdf') 一键交付",
            "export_delivery 默认自动保存 OPJU；若设置了 DSH_ORIGIN_NO_AUTO_SAVE=1，"
            "请提醒用户在 Origin 内按 Ctrl+S 手动保存",
        ],
    },
    "multi_compare": {
        "title": "多组数据对比（语义不明确时必须先 plan 确认）",
        "steps": [
            "origin_plot_plan(columns={...多列...}) -> 确认列角色/分组语义",
            "origin_execute_plan(plan_id)",
            "origin_apply_style(graph, style_mode='group', family='..') 区分多序列",
            "origin_verify_graph(graph)",
        ],
    },
    "edit": {
        "title": "微调已有图",
        "steps": [
            "origin_inspect_graph(graph) 看现状（几何/曲线/轴/图例）",
            "origin_edit_plot / origin_edit_axis / origin_edit_legend / origin_edit_page",
            "origin_view_graph(graph) 内联预览确认效果",
        ],
    },
    "fit": {
        "title": "拟合与统计分析",
        "steps": [
            "origin_write_data 或 origin_load_file 准备数据",
            "origin_fit(worksheet, kind='linear'|'ExpDec1'|'Gauss'...) -> 拟合曲线上图",
            "origin_stats / origin_ttest / origin_anova / origin_pca 按需",
            "注意：自研统计结果仅作快速探索，正式发表请用 SPSS/R/Origin 原生复核",
        ],
    },
    "recover": {
        "title": "失败恢复速查（error_code → 动作）",
        "steps": [
            "读失败返回的 error_code + recovery.policy + recovery.diagnose",
            "connection_error / origin_operation_error → origin_diagnose 定位，"
            "必要时 taskkill /F /IM Origin64.exe 等 2 秒重连",
            "graph/worksheet/column not found → origin_list_graphs / origin_status 复核",
            "export_error → 看返回 attempts 字段确认三级导出通道失败原因",
            "仍失败：同一问题 2 轮修复未果时停止重试，如实报告用户",
        ],
    },
    "deliver": {
        "title": "保存工程与交付",
        "steps": [
            "origin_export_delivery(graph, source_path=.., fmts='png,pdf') 一键交付",
            "或 origin_save_project(path) 单独保存 OPJU（可编辑工程）",
            "DSH_ORIGIN_NO_AUTO_SAVE=1 时工具不自动写 .opju，改提示 Ctrl+S",
        ],
    },
}

# 高频工具推荐默认参数（#20）：模型不指定时按这里出合理结果
_COOKBOOK_DEFAULTS = {
    "origin_plot_file": {"plot_type": "line_symbol", "fmt": "png", "width": 1200},
    "origin_plot": {"plot_type": "line_symbol", "style_mode": "default"},
    "origin_export": {"fmt": "png", "width": 1200},
    "origin_plot_template": {"fmt": "png", "width": 1200, "legend_mode": "auto"},
    "origin_plot3d": {"plot_type": "surface", "fmt": "png", "width": 1200},
    "origin_histogram": {"bins": 10, "plot": True, "fmt": "png"},
    "origin_fit": {"kind": "linear", "plot_result": True},
    "origin_export_delivery": {"fmts": "png,pdf", "width": 1200, "export_data_csv": True},
    "语义规范": {
        "折线/散点默认": "line_symbol（点线结合，审稿友好）",
        "期刊风格": "journal 模板 + 300dpi 等效宽度（export width>=1800）",
        "轴标题": "语义化（物理量 + 单位，如 'Time (s)'），拒绝 A/B/C 占位名",
    },
}

# 两条主路径（#23）
_COOKBOOK_PATHS = {
    "快速路径": "origin_plot_file / origin_load_file+origin_plot —— 数据语义明确、"
               "临时查看时使用，一次调用出图",
    "正式路径": "origin_plot_plan → 用户确认 → origin_execute_plan → origin_verify_graph "
               "→ origin_export_delivery —— 发表级/多组对比/列语义不明时强制使用",
}


def _cookbook_impl(scenario=""):
    """场景 → 工具组合速查（离线秒回）。scenario 支持前缀匹配。"""
    if scenario:
        key = str(scenario).strip().lower()
        hit = {k: v for k, v in _COOKBOOK_SCENARIOS.items() if k.startswith(key)}
        if not hit:
            return oerr.fail(
                "invalid_request",
                f"未知场景 {scenario!r}",
                available=sorted(_COOKBOOK_SCENARIOS),
                next_actions=["留空 scenario 返回全部场景，选一个 key 再查"])
        return oerr.ok(scenarios=hit, defaults=_COOKBOOK_DEFAULTS, paths=_COOKBOOK_PATHS)
    return oerr.ok(scenarios=_COOKBOOK_SCENARIOS, defaults=_COOKBOOK_DEFAULTS,
                   paths=_COOKBOOK_PATHS, count=len(_COOKBOOK_SCENARIOS))


# ---------------------------------------------------------------------------
# 文件导入（P0）：本地表格 -> Origin 工作表
# ---------------------------------------------------------------------------
def _load_file_impl(path, worksheet=None, sheet=None, max_preview_rows=5):
    try:
        _g = _path_guard(path, "导入路径")
        if _g is not None:
            return _g
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
        # 保存策略开关（#3）：DSH_ORIGIN_NO_AUTO_SAVE=1 时禁止脚本自动写 .opju，
        # 改为提示用户在 Origin 内按 Ctrl+S 手动保存（防止自动化覆盖/锁定用户工程）。
        if os.environ.get("DSH_ORIGIN_NO_AUTO_SAVE", "").strip().lower() in \
                ("1", "true", "on", "yes"):
            return oerr.fail(
                "manual_save_required",
                "已按策略（DSH_ORIGIN_NO_AUTO_SAVE=1）禁止脚本自动保存 .opju 项目文件",
                hint="请在 Origin 窗口按 Ctrl+S 手动保存当前项目；"
                     "如需恢复自动保存，取消该环境变量后重试")
        path = os.path.abspath(str(path))
        _g = _path_guard(path, "保存路径")
        if _g is not None:
            return _g
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
                          width=1200, save_opju=True, report_text=None,
                          export_data_csv=True):
    """一键交付：源文件同级建 <数据名>_Origin_<时间戳>/ 目录，
    导出多格式图片 + OPJU + （v2.3.0 新增）图对应工作表数据 csv + 分析报告
    txt（图表一体交付），并逐文件核验完整性。"""
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        fmt_list = [f.strip().lower().lstrip(".") for f in str(fmts).split(",")
                    if f.strip()]
        fmt_list = ["tif" if f == "tiff" else f for f in fmt_list]
        bad = [f for f in fmt_list if f not in ("png", "svg", "pdf", "tif", "emf", "eps")]
        if bad:
            return oerr.fail("invalid_request", f"不支持的导出格式: {bad}",
                             supported=["png", "svg", "pdf", "tif", "emf", "eps"])
        gp = op_find_graph(graph)
        if not gp:
            return oerr.fail("graph_not_found", f"图不存在: {graph}", graph=str(graph))
        ts = time.strftime("%Y%m%d_%H%M%S")
        if source_path:
            src = os.path.abspath(str(source_path))
            _g = _path_guard(src, "交付源路径")
            if _g is not None:
                return _g
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
            elif r2.get("error_code") == "manual_save_required":
                issues.append("OPJU 未自动保存（DSH_ORIGIN_NO_AUTO_SAVE=1 策略）："
                              "请在 Origin 窗口按 Ctrl+S 手动保存")
            else:
                issues.append(f"OPJU 保存失败: {r2.get('error')}")
        n_ok = sum(1 for f in files if f.get("ok"))
        # v2.3.0 图表一体交付：数据 csv + 分析报告 txt
        data_csv = None
        if export_data_csv:
            try:
                import csv as _csv
                gp_ = _origin_app.find_graph(graph)
                src_wks = None
                if gp_ is not None:
                    for li in range(int(gp_.obj.Layers.Count)):
                        src_wks = _find_wks_of_plot(gp_.__getitem__(li))
                        if src_wks is not None:
                            break
                if src_wks is not None:
                    ncol = int(src_wks.obj.Cols)
                    names = []
                    cols_data = []
                    for ci in range(ncol):
                        nm = None
                        try:
                            nm = src_wks.obj[ci].GetLongName() or f"col{ci + 1}"
                        except Exception:
                            nm = f"col{ci + 1}"
                        names.append(str(nm))
                        cols_data.append(src_wks.to_list(ci))
                    nrow = max((len(c) for c in cols_data), default=0)
                    data_csv = os.path.join(ddir, "data.csv")
                    with open(data_csv, "w", newline="", encoding="utf-8-sig") as fcsv:
                        wcsv = _csv.writer(fcsv)
                        wcsv.writerow(names)
                        for r_ in range(nrow):
                            wcsv.writerow([
                                c[r_] if r_ < len(c) else "" for c in cols_data])
            except Exception as _dc:
                issues.append(f"数据 csv 导出失败: {_dc}")
                data_csv = None
        report_txt = None
        if report_text:
            try:
                report_txt = os.path.join(ddir, "report.txt")
                with open(report_txt, "w", encoding="utf-8") as frt:
                    frt.write(str(report_text))
            except Exception as _rt:
                issues.append(f"报告 txt 写入失败: {_rt}")
                report_txt = None
        return oerr.ok(
            delivery_dir=ddir, files=files, opju=opju, issues=issues,
            data_csv=data_csv, report_txt=report_txt,
            all_ok=(not issues),
            detail=(f"交付目录 {ddir}（图片 {n_ok}/{len(files)} + "
                    f"{'OPJU' if opju else '无 OPJU'}"
                    f"{' + data.csv' if data_csv else ''}"
                    f"{' + report.txt' if report_txt else ''}）"
                    + ("；存在问题：" + "; ".join(issues) if issues else "")))
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


def op_find_graph(graph):
    """COM 线程内的图页查找（供交付/验证路径复用）。

    走 _safe_find_graph：规避 originpro 1.1.15 的 find_graph TypeError 缺陷。
    """
    try:
        return _safe_find_graph(_origin_app, graph)
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
                                       f"{k} 长度必须与 labels 一致", template=t)
    elif t == "cycle_overlay":
        if not need_list("x"):
            return None, oerr.fail("invalid_request",
                                   "cycle_overlay 需要 data['x'] 为非空列表",
                                   template=t, received_keys=sorted(data))
        se = data.get("series")
        if not (isinstance(se, dict) and len(se) >= 2):
            return None, oerr.fail(
                "invalid_request",
                "cycle_overlay 需要 data['series'] 为 {'曲线名': [..], ...}（≥2 条）",
                template=t, received_keys=sorted(data))
        nx = len(data["x"])
        for k, v in se.items():
            if not isinstance(v, (list, tuple)) or len(v) != nx:
                return None, oerr.fail(
                    "invalid_request",
                    f"曲线 {k!r} 长度必须与 x 一致", template=t)
        lm = str(data.get("legend_mode") or "all").lower()
        if lm not in ("all", "first_last", "none"):
            return None, oerr.fail(
                "invalid_request",
                "legend_mode 支持 all/first_last/none", template=t)
    elif t == "eis_nyquist":
        for k in ("z_real", "z_imag"):
            if not need_list(k):
                return None, oerr.fail(
                    "invalid_request", f"eis_nyquist 需要 data['{k}'] 为非空列表",
                    template=t, received_keys=sorted(data))
        if len(data["z_real"]) != len(data["z_imag"]):
            return None, oerr.fail("invalid_request",
                                   "z_real 与 z_imag 长度不一致", template=t)
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
                # 换行必须用真 LF：LabTalk 对象属性赋值不解析 "\n" 转义
                # （probe 实证：字面 "\n" 原样存进 legend 并显示；真换行被
                # Origin 规范化为 CRLF，图例正确分两行）
                op.po.LT_execute(
                    f'legend.text$ = "\\l(1.1) {esc_l}\n\\l(2.1) {esc_r}";')
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
        elif t == "cycle_overlay":
            # 多曲线同图叠放 + 渐变色（CV 多圈/充放电多循环/动力学多轮次）。
            # 与 stacked_spectra 的差别：不做纵向偏移，强调"同一张图上好看地
            # 放很多条曲线"。legend_mode: all | first_last | none。
            x = _flist(data["x"])
            series = {str(k): _flist(v) for k, v in data["series"].items()}
            names = list(series)
            cols = {"x": x}
            for nm in names:
                cols[nm] = series[nm]
            w = _write_data_impl(cols)
            if not w.get("ok"):
                return w
            wsobj = op.find_sheet("w", w["worksheet"])
            lname = _ensure_graph_name(graph_name, title or "CycleOverlay")
            gp = op.new_graph(lname=lname)
            gl = gp[0]
            for i in range(1, len(cols)):
                gl.add_plot(wsobj, i, 0, type="l")
            gl.rescale()
            short = gp.obj.GetName()
            style = _apply_style_impl(short, plot_type="line", columns=names,
                                      style_mode=style_mode, family=family,
                                      apply_axis_titles=False)
            # 渐变色（默认开：叠放图的价值就在颜色层次）
            # 注意：pl.color 写入 + 同任务内 expGraph 会令该页 DataPlots 枚举
            # 事后失效（曲线与颜色都在，仅 COM 枚举坏）——导出已由公共层拆到
            # 独立 COM 任务执行（probe: gradient+export 同任务必坏，分任务必好）
            import plot_style as _pst
            try:
                pal = _pst.choose_palette(len(names), family=family)["colors"]
                c0, c1 = _hex_to_rgb_tuple(pal[0]), _hex_to_rgb_tuple(pal[-1])
                pls = gl.plot_list() or []
                for i, pl in enumerate(pls):
                    t_ = i / max(1, len(pls) - 1)
                    pl.color = tuple(
                        int(c0[k] + (c1[k] - c0[k]) * t_) for k in range(3))
            except Exception:
                pass
            import origin_edit as _oedit
            title_checks = {}
            for ax, txt in (("x", x_title), ("y", y_title)):
                if txt:
                    title_checks[ax] = _oedit.set_axis_title_checked(
                        gl, ax, txt, po=op.po, graph=short)
            legend_mode = str(data.get("legend_mode") or "all").lower()
            try:
                if legend_mode == "none":
                    _oedit.edit_legend(op, op.po, short, visible=False)
                elif legend_mode == "first_last" and len(names) >= 2:
                    esc1 = names[0].replace('"', '\\"')
                    esc2 = names[-1].replace('"', '\\"')
                    # 真换行（probe 实证："\n" 字面不解析，真 LF 才分行）
                    op.po.LT_execute(
                        f'legend.text$ = "\\l(1) {esc1}\n\\l({len(names)}) {esc2}";')
            except Exception:
                pass
            r = oerr.ok(graph=short, template=t, series=names,
                        legend_mode=legend_mode, style=style,
                        axis_titles={k: v.get("readback") for k, v in
                                     title_checks.items()},
                        detail=(f"cycle_overlay 完成：{len(names)} 条曲线同图叠放"
                                f"（渐变色，图例 {legend_mode}）-> {short}"))
            _bad_titles = [k for k, v in title_checks.items() if not v.get("ok")]
            if _bad_titles:
                r["warning"] = (f"轴标题写回未通过读回验证: {_bad_titles}；"
                                "图上可能仍显示占位符")
        elif t == "eis_nyquist":
            # 电化学阻抗谱 Nyquist 图（-Z'' vs Z'，等轴比是判读半圆的前提）
            zr = _flist(data["z_real"])
            zi = _flist(data["z_imag"])
            zi_neg = [-v if np.isfinite(v) else float("nan") for v in zi]
            w = _write_data_impl({"z_real": zr, "neg_z_imag": zi_neg})
            if not w.get("ok"):
                return w
            wsobj = op.find_sheet("w", w["worksheet"])
            lname = _ensure_graph_name(graph_name, title or "EIS-Nyquist")
            gp = op.new_graph(lname=lname)
            gl = gp[0]
            gl.add_plot(wsobj, 1, 0, type="y")     # 线+符号
            gl.rescale()
            short = gp.obj.GetName()
            # 等轴比：两轴 span 对齐 + 图层几何近正方形
            try:
                xr = max(zr) - min(zr)
                yr = max(zi_neg) - min(zi_neg)
                span = max(xr, yr, 1e-9)
                cx, cy = (max(zr) + min(zr)) / 2, (max(zi_neg) + min(zi_neg)) / 2
                gl.axis("x").sfrom = float(cx - span / 2 * 1.1)
                gl.axis("x").sto = float(cx + span / 2 * 1.1)
                gl.axis("y").sfrom = float(cy - span / 2 * 1.1)
                gl.axis("y").sto = float(cy + span / 2 * 1.1)
                _set_layer_geometry(gl, left=14.0, top=8.0,
                                    width=62.0, height=62.0)
            except Exception:
                pass
            import origin_edit as _oedit
            _oedit.set_axis_title_checked(gl, "x", x_title or "Z' (Ω)", po=op.po)
            _oedit.set_axis_title_checked(gl, "y", y_title or "-Z'' (Ω)", po=op.po)
            # 单系列无图例（轴标题已表达 Z'/-Z''；内部列名不上图）
            try:
                _oedit.edit_legend(op, op.po, short, visible=False)
            except Exception:
                pass
            style = _apply_style_impl(short, plot_type="line_symbol",
                                      columns=["Z"], style_mode=style_mode,
                                      family=family, apply_axis_titles=False)
            r = oerr.ok(graph=short, template=t, style=style,
                        detail=f"eis_nyquist 完成（等轴比）-> {short}")
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

        # 注意：此处**不做内联导出**。真机实证（2026-09-16 probe RB/B/DBG）：
        # 模板内对 plot 逐条设色（pl.color → layer 域 LabTalk plotN.color）后
        # 若在同一 COM 任务内执行 expGraph，该页 DataPlots 的 COM 枚举会失效
        # （曲线/颜色/导出 PNG 全部正常，仅事后 plot_list() 读回为空，
        # verify_graph 会因此误报 series_count=0）。导出由公共层
        # plot_template 拆分为独立 COM 任务执行 —— 分任务导出实证必好。
        return r
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


@_synchronized
def _plot_template_export_task(graph, file_path, fmt, width):
    """独立 COM 任务执行模板导出（见 _plot_template_impl 尾注）。"""
    return _export_impl(graph, file_path=file_path, fmt=fmt, width=width)


# ---------------------------------------------------------------------------
# 计划执行（P1）：origin_plot_plan 缓存的计划 -> 写数/画图/导出
# ---------------------------------------------------------------------------
def _execute_plan_impl(plan_id, fmt=None, file_path=None, graph_name=None,
                       width=1200, expect_hash=None, force=False):
    try:
        import origin_plan as oplan
        plan = oplan.get_plan(plan_id)
        if plan is None:
            return oerr.fail("plan_not_found",
                             f"plan_id 不存在或已过期（服务端缓存容量 "
                             f"{oplan.PLAN_CACHE_MAX}，重启后清空）",
                             hint="重新调用 origin_plot_plan 生成")
        # 计划陈旧校验（#7）：缓存完整性 / plan_hash 匹配 / 是否有更新的同签名计划
        stale = oplan.check_stale(plan, expect_hash=expect_hash, force=force)
        if stale is not None:
            return stale
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


# P0-5（2026-09-16）：细粒度数据图管理 —— remove_plot / change_data
# （探针实证：gl.remove_plot(int) 走 obj.Destroy()；pl.change_data(wks, x=, y=)
#   按设计标签换数据源）
def _manage_plots_impl(graph, action, plot_index=0,
                       data_worksheet=None, x_col=None, y_col=None):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        gp = _safe_find_graph(op, graph)
        if not gp:
            return oerr.fail("graph_not_found",
                             f"图不存在: {graph}", graph=graph)
        gl = gp[0]
        plots = gl.plot_list()
        n = len(plots)
        if action == "remove":
            if not (0 <= int(plot_index) < n):
                return oerr.fail("invalid_request",
                                 f"plot_index {plot_index} 越界（图内共 {n} 条曲线）",
                                 plot_count=n)
            plots[int(plot_index)].remove()
            gl.rescale()
            after = len(gl.plot_list())
            return oerr.ok(
                graph=graph, removed=plot_index, plots_after=after,
                detail=f"已删除第 {int(plot_index) + 1} 条曲线"
                       f"（索引 {plot_index}），剩余 {after} 条")
        if action == "change_data":
            if data_worksheet is None:
                return oerr.fail("invalid_request",
                                 "change_data 需要 data_worksheet（工作表名）与 "
                                 "x_col/y_col（新数据列）")
            wks = op.find_sheet("w", data_worksheet)
            if not wks:
                return oerr.fail("worksheet_not_found",
                                 f"工作表不存在: {data_worksheet}")
            if not (0 <= int(plot_index) < n):
                return oerr.fail("invalid_request",
                                 f"plot_index {plot_index} 越界（图内共 {n} 条曲线）",
                                 plot_count=n)
            kw = {}
            if y_col is not None:
                kw["y"] = y_col
            if x_col is not None:
                kw["x"] = x_col
            if not kw:
                return oerr.fail("invalid_request",
                                 "change_data 至少需要 x_col 或 y_col 之一")
            plots[int(plot_index)].change_data(wks, **kw)
            gl.rescale()
            return oerr.ok(
                graph=graph, changed=plot_index, data_worksheet=str(wks),
                x_col=x_col, y_col=y_col,
                detail=f"第 {int(plot_index) + 1} 条曲线数据源已换为 "
                       f"{data_worksheet}（x={x_col}, y={y_col}）；"
                       "建议 origin_verify_graph 复核")
        return oerr.fail("invalid_request",
                         f"action 只支持 remove/change_data，收到 {action!r}")
    except Exception as e:
        return oerr.from_exception(e, trace=traceback.format_exc(limit=3))


# P0-5（2026-09-16）：工作表 sort / transpose
# （探针实证：wks.sort(col, dec=False) 签名；transpose 无原生 API，走 Python 侧
#   读列-转置-写新表，首行不自动当列标题）
def _manage_data_impl(worksheet, action, col=0, dec=False):
    try:
        ok, conn = _connect_impl()
        if not ok:
            return conn
        op = _origin_app
        wks = op.find_sheet("w", worksheet)
        if not wks:
            return oerr.fail("worksheet_not_found",
                             f"工作表不存在: {worksheet}")
        if action == "sort":
            cidx = _col_index_impl(wks, col)
            if cidx is None:
                return oerr.fail("invalid_request",
                                 f"排序列不存在: {col!r}", worksheet=str(wks))
            wks.sort(cidx, bool(dec))
            first = (wks.to_list(0)[:1] or [None])[0]
            return oerr.ok(
                worksheet=str(wks), sorted_by=str(col), descending=bool(dec),
                first_row_sample=first,
                detail=f"已按列 {col!r} {'降' if dec else '升'}序排序整表")
        if action == "transpose":
            ncols, nrows = wks.cols, wks.rows
            if nrows == 0 or ncols == 0:
                return oerr.fail("invalid_request", "空表无法转置",
                                 worksheet=str(wks))
            if nrows > 1000:
                return oerr.fail(
                    "invalid_request",
                    f"转置保护：原表 {nrows} 行将变成 {nrows} 列，超过 1000 列上限；"
                    "如确需大表转置请在 Origin 内手动操作",
                    rows=nrows, cols=ncols)
            cols_data = [wks.to_list(j) for j in range(ncols)]
            bk = op.new_book("w", _new_unique_name("Transposed"))
            tw = bk[0]
            for i in range(nrows):               # 原表第 i 行 -> 新表第 i 列
                tw.from_list(i, [cols_data[j][i]
                                 for j in range(ncols)],
                             lname=f"R{i + 1}")
            return oerr.ok(
                worksheet=str(tw), source=str(wks),
                rows=ncols, cols=nrows,
                detail=f"已转置 {nrows}行×{ncols}列 -> {nrows}列×{ncols}行，"
                       f"写入新表 {tw}（原表未动；转置后首行是数据不是标题）")
        return oerr.fail("invalid_request",
                         f"action 只支持 sort/transpose，收到 {action!r}")
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


def shutdown(timeout=5.0):
    """优雅停机（#10）：排空任务队列并停掉专用 COM 线程。

    只停引擎自身线程，不杀 Origin 进程（用户可能仍在手动使用 Origin）；
    残留 Origin 进程的清理交给 DSH_ORIGIN_AUTOKILL 策略或用户手动 taskkill。
    供插件宿主在卸载（ctx.effect）时显式调用；Python 进程退出时 COM 线程
    为 daemon，会随进程自然回收。
    """
    global _com_thread
    while True:
        try:
            pending = _com_queue.get_nowait()
        except queue.Empty:
            break
        try:
            pending[3].set_exception(RuntimeError("引擎停机：任务未执行"))
        except Exception:
            pass
    _com_queue.put(None)
    t, _com_thread = _com_thread, None
    stopped = True
    if t is not None and t.is_alive() and t is not threading.current_thread():
        t.join(timeout=max(0.1, float(timeout)))
        stopped = not t.is_alive()
    return {"ok": True, "thread_stopped": stopped}


# --- P0-3（2026-09-16）：release / reconnect —— 把 Origin 让给用户手动操作 ---
def _release_impl():
    """释放自动化引用但**不关闭 Origin**：用户可立即手动操作 Origin 窗口。

    注意：只清 COM 引用（_connected/_origin_app），**不停专用 COM 线程**——
    线程内已 CoInitialize 的 originpro 状态必须与线程绑定（实测停线程后
    新线程重连会原生崩溃 EXIT=127）；下次任意工具调用在同一线程重连（Attach）。
    """
    global _connected, _origin_app
    origin_still_running = _origin_running()
    _connected = False
    _origin_app = None
    return {"ok": True, "released": True,
            "origin_still_running": origin_still_running,
            "detail": "已释放自动化连接；Origin 窗口保持打开，可手动操作。"
                      "下次任意工具调用将自动重连。"}


def _reconnect_impl():
    """显式重连 Origin（release 后恢复自动化；幂等——已连接时原样返回状态）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    conn["detail"] = "已重连 Origin COM 自动化服务器" + (
        "（此前为 release 状态）" if not conn.get("origin_running_before", True) else "")
    return conn


@_synchronized
def status():
    return _status_impl()


@_synchronized
def release():
    """P0-3：释放自动化连接（Origin 保持打开，交给用户手动操作）。"""
    return _release_impl()


@_synchronized
def reconnect():
    """P0-3：显式重连 Origin COM 自动化服务器。"""
    return _reconnect_impl()


@_synchronized
def manage_plots(graph, action, plot_index=0,
                 data_worksheet=None, x_col=None, y_col=None):
    """P0-5：数据图管理（remove 删曲线 / change_data 换数据源）。"""
    return _manage_plots_impl(graph, action, plot_index=plot_index,
                              data_worksheet=data_worksheet,
                              x_col=x_col, y_col=y_col)


@_synchronized
def manage_data(worksheet, action, col=0, dec=False):
    """P0-5：工作表数据管理（sort 排序 / transpose 转置）。"""
    return _manage_data_impl(worksheet, action, col=col, dec=dec)


# --- P1-3（2026-09-16）：矩阵页读写与矩阵绘图 ---
@_synchronized
def matrix_write(data, matrix_name=None):
    """P1-3：写矩阵页（{'z': 2D 网格} 或直接 2D 网格；from_np 自动 resize）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_matrix as _om
    return _om.matrix_write_impl(_origin_app, data, matrix_name=matrix_name)


@_synchronized
def matrix_read(matrix):
    """P1-3：读矩阵数据（2D 列表 + shape）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_matrix as _om
    return _om.matrix_read_impl(_origin_app, matrix)


@_synchronized
def matrix_plot(matrix, plot_type="surface", fmt=None, file_path=None,
                width=1200, title=None):
    """P1-3：用已有矩阵绘图（surface/scatter/contour/contour_fill/3d_wire）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_matrix as _om
    return _om.matrix_plot_impl(_origin_app, matrix, plot_type=plot_type,
                                fmt=fmt, file_path=file_path, width=width,
                                title=title)


# --- P1-1（2026-09-16）：FigureSpec 声明式图协议（YAML 落盘/导入，离线秒回） ---
def spec_export(plan_id, path):
    """P1-1：把已有 plan 导出为 FigureSpec YAML（可 diff/可重放/可版本化）。"""
    import origin_plan as oplan
    import origin_spec as _os
    plan = oplan.get_plan(plan_id)
    if plan is None:
        return oerr.fail(
            "plan_not_found",
            f"plan_id 不存在或已过期（服务端缓存容量 {oplan.PLAN_CACHE_MAX}）",
            plan_id=str(plan_id)[:24])
    return _os.spec_to_yaml(_os.spec_from_plan(plan), path)


def spec_import(path):
    """P1-1：读取 FigureSpec YAML 重建计划（进确认流，返回 plan_id/questions）。"""
    import origin_spec as _os
    spec, err = _os.spec_from_yaml(path)
    if err is not None:
        return err
    return _os.spec_import(spec)


def spec_validate(spec):
    """P1-1：离线校验 spec 结构（不落盘不连 Origin）。"""
    import origin_spec as _os
    return _os.spec_validate(spec)


# --- P2-6/P2-7（2026-09-16）：matplotlib 桥 + PPT 组图交付 ---
@_synchronized
def import_matplotlib(pickle_path, graph_name=None, title=None):
    """P2-6：导入 matplotlib Figure pickle（提取 Line2D 数据+样式映射画图）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_bridge as _ob
    return _ob.import_matplotlib_impl(_origin_app, pickle_path,
                                      graph_name=graph_name, title=title)


@_synchronized
def export_pptx(graph, file_path, width=2400, title=None,
                panel_label=None, notes=None):
    """P2-7：图导出高清 PNG 并组 PowerPoint 页（面板字母+来源注记）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_bridge as _ob
    return _ob.export_pptx_impl(_origin_app, graph, file_path, width=width,
                                title=title, panel_label=panel_label,
                                notes=notes)


def template_search(keyword, max_items=5, download_dir=None):
    """P2-5：搜索/下载 OriginLab Graph Gallery 模板（离线可调；网络失败如实报告）。"""
    import origin_gallery as _og
    return _og.search_impl(keyword, max_items=max_items,
                           download_dir=download_dir)


# --- 阶段 A/B/C（2026-09-17）：端到端提速 + 模板资产 + 能力表 + 证据分级 ---
@_synchronized
def figure(columns=None, data_source=None, intent="auto", plot_type=None,
           x_column=None, y_columns=None, style_mode="default", family=None,
           fmt="png", file_path=None, output_dir=None, width=1200,
           graph_name=None, title=None, verify=True, deliver=False,
           source_path=None, label_peaks=False, peak_top_n=5):
    """端到端一张图：导入/写数 → 画图 →（可选）verify → 导出 →（可选）交付。

    把常用路径从 6-10 次工具调用收敛为 1 次（性能分析：感知耗时的 80% 在
    模型决策轮次）。返回带 steps 逐步耗时与汇总 proof_level。
    label_peaks=True 时自动找峰并标注（NMR/PL/拉曼标峰场景）。
    """
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_e2e as _e2e
    import origin_proof as _pf
    r = _e2e.figure_impl(_origin_app, columns=columns, data_source=data_source,
                         intent=intent, plot_type=plot_type, x_column=x_column,
                         y_columns=y_columns, style_mode=style_mode,
                         family=family, fmt=fmt, file_path=file_path,
                         output_dir=output_dir, width=width,
                         graph_name=graph_name, title=title, verify=verify,
                         deliver=deliver, source_path=source_path,
                         label_peaks=label_peaks, peak_top_n=peak_top_n)
    return _pf.annotate(r)


@_synchronized
def warmup(start_origin=True):
    """预热：确保 Origin 已连并完成一次完整 COM 往返（把冷启动挪出用户视野）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_e2e as _e2e
    return _e2e.warmup_impl(_origin_app, start_origin=start_origin)


@_synchronized
def pages_gc(threshold=200, dry_run=True):
    """页堆积治理：项目页超阈值时报告/清理（实测 793 页让枚举慢 30 倍）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_e2e as _e2e
    return _e2e.pages_gc_impl(_origin_app, threshold=threshold, dry_run=dry_run)


@_synchronized
def template_save(graph, template_name, category=None, overwrite=False):
    """把成品图存为可复用模板（.otpu 通道实测不可用 → JSON 样式快照 + opju 备份）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_template as _ot
    return _ot.template_save_impl(_origin_app, graph, template_name,
                                  category=category, overwrite=overwrite)


def template_list(category=None):
    """列出可用模板/样式快照（离线，不连 Origin）。"""
    import origin_template as _ot
    return _ot.template_list_impl(None, category=category)


@_synchronized
def template_apply(graph, template_name):
    """把模板样式套到指定图（逐项返回 applied/readback_only/unverified）。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_template as _ot
    return _ot.template_apply_impl(_origin_app, graph, template_name)


def capabilities(force_refresh=False):
    """从本机 Origin 安装的 oPlotIDs.h 提取真实图型能力表，并与硬编码表比对。"""
    import origin_capabilities as _oc
    return _oc.capabilities_impl(force_refresh=force_refresh)


def capability_diff():
    """能力表 vs 引擎硬编码 PLOT_TYPES 的差异（暴露文档/实现脱节）。"""
    import origin_capabilities as _oc
    return _oc.compare_against_hardcoded_impl()


# --- 阶段 D（2026-09-17）：标注盲试治理 —— layout_info / annotate / simulate / find_peaks ---
@_synchronized
def origin_annotate(graph, items, style=None):
    """批量文本标注：一次调用加 N 个文本（统一样式、可选左对齐）。

    治理"标注盲试循环"（甲烷 NMR 案例：放两行注释烧了 15 次 add_text）。
    返回逐项落位与 LabTalk 对象名（TextN，可后续微调）。
    """
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_annotate as _oa
    return _oa.annotate_impl(_origin_app, _origin_app.po, graph, items,
                             style=style)


@_synchronized
def layout_info(graph, width_px=1100):
    """返回布局几何：坐标映射（数据↔像素，反向轴自动处理）、轴范围、页尺寸、
    现有文本对象清单、图例位置。AI 用它精确落位，不再渲染-看图-再调。"""
    ok, conn = _connect_impl()
    if not ok:
        return conn
    import origin_annotate as _oa
    return _oa.layout_info_impl(_origin_app, _origin_app.po, graph,
                                width_px=width_px)


def simulate(kind="lorentzian", centers=None, widths=None, heights=None,
             n_points=2000, x_range=None, noise=0.01, seed=None,
             x_label="x", y_label="y_simulated"):
    """物理模型谱图模拟（多峰 + 噪声，纯 numpy 不连 Origin）。

    返回强制带 simulated=True——图注与报告必须注明"模拟数据"，
    不得作为实验数据呈现（项目纪律：不虚构数据）。
    """
    import origin_annotate as _oa
    return _oa.simulate_impl(kind=kind, centers=centers, widths=widths,
                             heights=heights, n_points=n_points,
                             x_range=x_range, noise=noise, seed=seed,
                             x_label=x_label, y_label=y_label)


def find_peaks(x_list, y_list, top_n=5, min_height_frac=0.05,
               label_template="{x:.2f}", x_prefix=""):
    """找局部极大峰（纯 numpy），返回可直接转 origin_annotate items 的峰列表。"""
    import origin_annotate as _oa
    return _oa.find_peaks_impl(x_list, y_list, top_n=top_n,
                               min_height_frac=min_height_frac,
                               label_template=label_template,
                               x_prefix=x_prefix)


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
        graph=None, title=None, drop_report_pages=True,
        initial_params=None, fixed_params=None, weight_col=None):
    return _fit_impl(worksheet, x_column, y_column, kind=kind, plot_curve=plot_curve,
                     graph=graph, title=title, drop_report_pages=drop_report_pages,
                     initial_params=initial_params, fixed_params=fixed_params,
                     weight_col=weight_col)


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


def diagnose(connect_probe=False):
    """系统级自检（不走 COM 线程，连接失败时也可用）；connect_probe=True 才尝试真实连接。

    注意：connect_probe 内部会经 engine.connect() 走专用 COM 线程。
    """
    return _diagnose_impl(connect_probe=connect_probe)


def cookbook(scenario=""):
    """场景速查（纯静态内容，离线秒回，不连 Origin）。"""
    return _cookbook_impl(scenario=scenario)


@_synchronized
def load_file(path, worksheet=None, sheet=None, max_preview_rows=5):
    return _load_file_impl(path, worksheet=worksheet, sheet=sheet,
                           max_preview_rows=max_preview_rows)


@_synchronized
def save_project(path):
    return _save_project_impl(path)


@_synchronized
def export_delivery(graph, source_path=None, output_dir=None, fmts="png,pdf",
                    width=1200, save_opju=True, report_text=None,
                    export_data_csv=True):
    return _export_delivery_impl(graph, source_path=source_path,
                                 output_dir=output_dir, fmts=fmts, width=width,
                                 save_opju=save_opju, report_text=report_text,
                                 export_data_csv=export_data_csv)


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
def column_formula(worksheet, target, formula, lname=None):
    return _column_formula_impl(worksheet, target, formula, lname=lname)


@_synchronized
def peak_fit(worksheet, x_column, y_column, n_peaks=1, kind="gauss",
             centers_hint=None, baseline=True, plot_curve=True,
             graph=None, title=None, show_components=True):
    return _peak_fit_impl(worksheet, x_column, y_column, n_peaks=n_peaks,
                          kind=kind, centers_hint=centers_hint,
                          baseline=baseline, plot_curve=plot_curve,
                          graph=graph, title=title,
                          show_components=show_components)


@_synchronized
def mask_points(worksheet, y_column, rows=None, x_min=None, x_max=None,
                x_column=None, backup=True):
    return _mask_points_impl(worksheet, y_column, rows=rows, x_min=x_min,
                             x_max=x_max, x_column=x_column, backup=backup)


@_synchronized
def add_line(graph, orientation="vertical", at=None, slope=None,
             intercept=None, color="#D55E00", line_style=1, label=None,
             layer=0):
    return _add_line_impl(graph, orientation=orientation, at=at, slope=slope,
                          intercept=intercept, color=color,
                          line_style=line_style, label=label, layer=layer)


@_synchronized
def labtalk(script, read_expr=None, graph=None, read_kind="auto",
            confirm=False, force_silent=False):
    return _labtalk_impl(script, read_expr=read_expr, graph=graph,
                         read_kind=read_kind, confirm=bool(confirm),
                         force_silent=bool(force_silent))


def plot_template(template_id, data, graph_name=None, title=None,
                  style_mode="default", family=None, offset="auto",
                  reverse_x=False, fmt=None, file_path=None, width=1200,
                  x_title=None, y_title=None, gradient=False):
    """公共入口：模板绘制（COM 任务 1）+ 导出（COM 任务 2，独立投递）。

    导出为何单独一个任务：见 _plot_template_impl 尾注（同任务内联导出会
    令设色页的 DataPlots 枚举事后失效，verify_graph 误报 0 曲线）。
    """
    r = _plot_template_task(template_id, data, graph_name=graph_name,
                            title=title, style_mode=style_mode, family=family,
                            offset=offset, reverse_x=reverse_x,
                            x_title=x_title, y_title=y_title,
                            gradient=gradient)
    if (fmt or file_path) and isinstance(r, dict) and r.get("ok") \
            and r.get("graph"):
        rex = _plot_template_export_task(r["graph"], file_path,
                                         fmt or "png", width)
        if rex.get("ok"):
            r["file"] = rex["file"]
            r["size"] = rex["size"]
            r["format"] = rex["format"]
        else:
            r["warning"] = f"导出失败: {rex.get('error')}"
    return r


@_synchronized
def _plot_template_task(template_id, data, graph_name=None, title=None,
                        style_mode="default", family=None, offset="auto",
                        reverse_x=False, x_title=None, y_title=None,
                        gradient=False):
    return _plot_template_impl(template_id, data, graph_name=graph_name,
                               title=title, style_mode=style_mode,
                               family=family, offset=offset,
                               reverse_x=reverse_x,
                               x_title=x_title, y_title=y_title,
                               gradient=gradient)


@_synchronized
def execute_plan(plan_id, fmt=None, file_path=None, graph_name=None, width=1200,
                 expect_hash=None, force=False):
    return _execute_plan_impl(plan_id, fmt=fmt, file_path=file_path,
                              graph_name=graph_name, width=width,
                              expect_hash=expect_hash, force=force)


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
