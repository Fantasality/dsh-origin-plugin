# -*- coding: utf-8 -*-
"""
bridge_manager.py —— DSH Origin Bridge 生命周期管理（sidecar 后台进程）
======================================================================

被 origin_app/launch.ogs 的按钮调用，也支持在 Origin Command Window 手动跑。
职责（参考竞品 erannave/originlab-mcp 的 "点按钮→启动脱离 UI 线程的后台进程"
思路，但封装成可单测、可 --dry-run 引用的纯 Python 模块）：

    python bridge_manager.py --start     # 拉起桥接 sidecar（detached 后台进程）
    python bridge_manager.py --stop      # 杀掉 sidecar
    python bridge_manager.py --restart   # 先停后起
    python bridge_manager.py --toggle    # 在跑则停、没跑则起（按钮默认）
    python bridge_manager.py --status    # 打印 JSON：running / pid / transport / port
    python bridge_manager.py --cleanup   # Origin 关闭时清理（由 launch.ogs 的收尾或
                                          #   用户退出 Hook 调用；杀进程 + 清 pidfile）

启动方式：
    --start 默认走 transport=stdio，用 subprocess 拉起插件根的 origin_mcp_stdio.py，
    记录 PID 到 %LOCALAPPDATA%\\OriginLab\\dsh-origin\\bridge.pid。
    设环境变量 ORIGIN_BRIDGE_TRANSPORT=http 可切换到后续 HTTP server 形态
    （sidecar 监听 http://127.0.0.1:8000/mcp，AI 客户端走 HTTP transport 连接；
    需要另有一个 bridge_http_server.py 时再启用，本文件只负责拉起/管理进程）。

为什么用 detached 后台进程，而不是在 Origin UI 线程里跑：
    MCP / Origin COM 调用会阻塞；若直接在按钮脚本里跑，Origin 界面会卡死。
    用 CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS 拉起的子进程独立于 Origin 的
    UI 消息循环，Origin 关掉也还能由本文件的 --cleanup 收尸。

PID 文件位置：
    %LOCALAPPDATA%\\OriginLab\\dsh-origin\\bridge.pid
    （不放 App 目录内，避免重装 App 时残留；锁目录跨 App 版本共享。）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------
# bridge_manager.py 位于 <插件根>/origin_app/ 下；插件根是其父目录。
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(APP_DIR)

# 被拉起的桥接入口：插件根的 origin_mcp_stdio.py
STDIO_ENTRY = os.path.join(PLUGIN_DIR, "origin_mcp_stdio.py")
HTTP_ENTRY = os.path.join(PLUGIN_DIR, "origin_mcp_http.py")

# 状态/锁目录（跨 App 版本共享）
STATE_DIR = os.path.expandvars(r"%LOCALAPPDATA%\OriginLab\dsh-origin")
PIDFILE = os.path.join(STATE_DIR, "bridge.pid")

# 默认监听端口（HTTP transport 形态用）
DEFAULT_PORT = int(os.environ.get("ORIGIN_BRIDGE_PORT", "8731"))

# 拉起桥接用的 Python 解释器：默认用"跑本文件的那个 python"（Origin 内嵌 Python
# 或系统 python）。若你的 mcp/originpro 装在别的虚拟环境，设：
#   ORIGIN_BRIDGE_PYTHON = D:/workbuddyworkspace/compare/_chem_venv/Scripts/python.exe
BRIDGE_PYTHON = os.environ.get("ORIGIN_BRIDGE_PYTHON", sys.executable)

# transport：stdio（默认，拉起 origin_mcp_stdio.py）或 http（后续 server）
TRANSPORT = os.environ.get("ORIGIN_BRIDGE_TRANSPORT", "http").lower()


# ---------------------------------------------------------------------------
# 小工具：进程存活判断（纯 Windows API，无第三方依赖）
# ---------------------------------------------------------------------------
def is_process_alive(pid: int) -> bool:
    """用 WaitForSingleObject(pid, 0) 判断进程是否还活着（Windows）。

    返回 True=进程仍存在；False=已退出或 PID 不存在。
    优先用 ctypes 直接调 kernel32，避免引入 psutil；拿不到 API 时退化为 os.kill。
    """
    if pid is None or pid <= 0:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        # 等待 0ms：仍在跑返回 WAIT_TIMEOUT(258)，已退出返回 WAIT_OBJECT_0(0)
        res = kernel32.WaitForSingleObject(handle, 0)
        kernel32.CloseHandle(handle)
        return res == 0x00000102  # WAIT_TIMEOUT
    except Exception:
        # 退化方案：os.kill(pid, 0) 在 Windows 上"能发信号即存在"
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _read_pid() -> int | None:
    try:
        with open(PIDFILE, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        return int(raw) if raw else None
    except Exception:
        return None


def _write_pid(pid: int) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(PIDFILE, "w", encoding="utf-8") as fh:
        fh.write(str(pid))


def _clear_pid() -> None:
    try:
        os.remove(PIDFILE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 组装要拉起的子进程命令
# ---------------------------------------------------------------------------
def _build_sidecar_cmd() -> list[str]:
    """返回拉起 sidecar 的命令 argv 列表。

    stdio 形态：直接跑 origin_mcp_stdio.py（MCP stdio 服务器）。
    http  形态：跑后续 HTTP server（默认占位 origin_mcp_stdio.py --http，
               待 bridge_http_server 就绪后替换；这里仅保证"能拉起、能管理 PID"。
    """
    # 默认 http：按钮形态需要"常驻 + 多客户端共享一个 Origin"，
    # stdio 只适合被单个 MCP 客户端以子进程方式拉起，不能脱离父进程常驻
    # （实测：detached 拉起 stdio 会因无 stdin 立即退出，status 读到 running=False）。
    if TRANSPORT == "stdio":
        return [BRIDGE_PYTHON, STDIO_ENTRY]
    return [BRIDGE_PYTHON, HTTP_ENTRY, "--host", "127.0.0.1",
            "--port", str(DEFAULT_PORT)]


def _spawn_detached(cmd: list[str]) -> int:
    """以脱离 Origin UI 线程的方式拉起子进程，返回 PID。

    用 CREATE_NEW_PROCESS_GROUP(0x200) + DETACHED_PROCESS(0x80) 让子进程
    拥有独立进程组、不绑定父进程控制台，Origin 关掉也不会被一起带走。
    日志写到 STATE_DIR/bridge.log，方便排查为什么没起来。
    """
    os.makedirs(STATE_DIR, exist_ok=True)
    log_path = os.path.join(STATE_DIR, "bridge.log")
    # 正斜杠路径 Origin/mkOPX 会卡，但这里是 Python subprocess，正反斜杠都行；
    # 为统一习惯仍 normpath 成当前系统的原生分隔符。
    cmd = [os.path.normpath(c) for c in cmd]
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n[{_now()}] spawn: {' '.join(cmd)}\n")
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=PLUGIN_DIR,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x80,  # DETACHED_PROCESS
            close_fds=True,
        )
        log.write(f"[{_now()}] spawned pid={proc.pid}\n")
    return proc.pid


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 对外动作
# ---------------------------------------------------------------------------
def do_start() -> dict:
    pid = _read_pid()
    if pid and is_process_alive(pid):
        return _ok(running=True, pid=pid,
                   detail=f"桥接已在运行（pid={pid}）；无需重复启动")
    # 防残留死 pid
    if pid:
        _clear_pid()
    if not os.path.isfile(STDIO_ENTRY):
        return _fail(f"找不到桥接入口：{STDIO_ENTRY}（请确认插件目录完整）")
    new_pid = _spawn_detached(_build_sidecar_cmd())
    _write_pid(new_pid)
    return _ok(running=True, pid=new_pid, transport=TRANSPORT,
               detail=f"已启动桥接 sidecar（pid={new_pid}，transport={TRANSPORT}）；"
                      f"日志见 {os.path.join(STATE_DIR, 'bridge.log')}")


def do_stop() -> dict:
    pid = _read_pid()
    if not pid or not is_process_alive(pid):
        _clear_pid()
        return _ok(running=False, pid=None, detail="桥接未运行或已停止")
    # 先优雅终止，2 秒后强制杀
    try:
        proc = subprocess.Process(pid)
        proc.terminate()
    except Exception:
        try:
            os.kill(pid, 9)
        except Exception:
            pass
    deadline = time.time() + 2.0
    while time.time() < deadline and is_process_alive(pid):
        time.sleep(0.1)
    if is_process_alive(pid):
        try:
            os.kill(pid, 9)
        except Exception:
            pass
    _clear_pid()
    return _ok(running=False, pid=pid, detail=f"已停止桥接 sidecar（pid={pid}）")


def _endpoint_alive(timeout: float = 0.6) -> bool:
    """HTTP 形态的存活判据：能连上端口才算活着。

    比"进程存在"更准：detached 子进程的 PID 查询在本机会话里会误判为已退出
    （实测：进程确实在跑、日志已打印监听，PID 探测却返回 False）。
    """
    import socket
    try:
        with socket.create_connection(("127.0.0.1", DEFAULT_PORT),
                                      timeout=timeout):
            return True
    except OSError:
        return False


def do_status() -> dict:
    pid = _read_pid()
    # HTTP 形态优先用端口探测（PID 探测对 detached 子进程会误判）
    if TRANSPORT == "http":
        alive = _endpoint_alive()
        return _ok(running=alive, pid=pid if alive else None,
                   transport=TRANSPORT, endpoint=_endpoint(),
                   pidfile=PIDFILE,
                   detail=("桥接运行中，AI 客户端连 " + _endpoint()) if alive
                          else "桥接未运行；点按钮或运行 --start 启动")
    alive = bool(pid) and is_process_alive(pid)
    if alive:
        return _ok(running=True, pid=pid, transport=TRANSPORT,
                   endpoint=_endpoint(), pidfile=PIDFILE,
                   detail=f"桥接运行中（pid={pid}，transport={TRANSPORT}）")
    # 死 pid 顺手清掉
    if pid:
        _clear_pid()
    return _ok(running=False, pid=None, transport=TRANSPORT,
               endpoint=_endpoint(), pidfile=PIDFILE,
               detail="桥接未运行；点按钮或运行 --start 启动")


def do_restart() -> dict:
    do_stop()
    return do_start()


def do_cleanup() -> dict:
    """Origin 关闭时调用：杀掉 sidecar + 清 pidfile。"""
    return do_stop()


def _endpoint() -> str:
    if TRANSPORT == "http":
        return f"http://127.0.0.1:{DEFAULT_PORT}/mcp"
    return "stdio（由 AI 客户端以 stdio 子进程方式连接）"


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _fail(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _emit(result: dict) -> int:
    """把结果同时打到 stdout（Origin Command Window 可见）并以退出码返回。"""
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DSH Origin Bridge 生命周期管理")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--start", action="store_true", help="启动桥接 sidecar")
    grp.add_argument("--stop", action="store_true", help="停止桥接 sidecar")
    grp.add_argument("--restart", action="store_true", help="先停后起")
    grp.add_argument("--toggle", action="store_true",
                     help="在跑则停、没跑则起（App 按钮默认）")
    grp.add_argument("--status", action="store_true", help="打印当前状态")
    grp.add_argument("--cleanup", action="store_true", help="Origin 关闭时清理")
    args = ap.parse_args(argv)

    if args.start:
        return _emit(do_start())
    if args.stop:
        return _emit(do_stop())
    if args.restart:
        return _emit(do_restart())
    if args.toggle:
        st = do_status()
        if st.get("running"):
            return _emit(do_stop())
        return _emit(do_start())
    if args.status:
        return _emit(do_status())
    if args.cleanup:
        return _emit(do_cleanup())
    return _emit(_fail("未指定动作"))


if __name__ == "__main__":
    raise SystemExit(main())
