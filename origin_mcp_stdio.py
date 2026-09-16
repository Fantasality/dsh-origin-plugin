# -*- coding: utf-8 -*-
"""
origin_mcp_stdio —— 通用 MCP stdio 启动入口（v2.3.0 新增）
==========================================================

面向 Kimi Code / Cursor / Claude Desktop / WorkBuddy / Cline 等标准 MCP
客户端的一键接入入口。与 origin_mcp_server.py 共享全部 50 个工具，仅依赖：

  - 官方 MCP SDK（pip install "mcp>=1.0"，本机实测 2.2.0 自带
    mcp.server.mcpserver.MCPServer 高层 API）
  - originpro / pywin32 / numpy / openpyxl（Origin 自动化与文件 IO）
  - 本目录的纯 Python 引擎（origin_engine，无任何宿主 SDK 依赖）

不包含 DSH 专有依赖；DSH 内通过 cordis.patch.yml 的 mcp-origin 加载器
启动 origin_mcp_server.py，二者共用同一引擎与工具面。

用法：
    python origin_mcp_stdio.py                    # 启动 stdio MCP 服务器
    python origin_mcp_stdio.py --print-config     # 打印各客户端接入配置
    python origin_mcp_stdio.py --doctor           # 系统级自检（同 origin_diagnose）

客户端接入（把 <python> 换成你的解释器、<dir> 换成本目录绝对路径）：
    {
      "mcpServers": {
        "dsh-origin": {
          "command": "<python>",
          "args": ["<dir>/origin_mcp_stdio.py"]
        }
      }
    }
"""
from __future__ import annotations

import json
import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

_PREFLIGHT = [
    ("mcp", 'pip install "mcp>=1.0"'),
    ("originpro", "pip install originpro"),
    ("win32com", "pip install pywin32"),
    ("numpy", "pip install numpy"),
    ("openpyxl", "pip install openpyxl（XLSX 导入需要）"),
]


def _preflight():
    """依赖预检：缺什么、怎么装，一次性说清楚。返回缺失列表。"""
    missing = []
    for mod, hint in _PREFLIGHT:
        try:
            __import__(mod)
        except Exception:
            missing.append(f"{mod}  ->  {hint}")
    return missing


def _print_config():
    """打印常见 MCP 客户端的接入配置片段（#25 适配入口）。"""
    py = sys.executable
    entry = os.path.join(PLUGIN_DIR, "origin_mcp_stdio.py").replace("\\", "/")
    server = {"command": py, "args": [entry]}
    configs = {
        "Claude Desktop": os.path.expandvars(
            r"%APPDATA%\Claude\claude_desktop_config.json"),
        "Cursor": os.path.expandvars(r"%USERPROFILE%\.cursor\mcp.json"),
        "Windsurf": os.path.expandvars(
            r"%USERPROFILE%\.codeium\windsurf\mcp_config.json"),
        "Cline (VS Code)": os.path.expandvars(
            r"%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev"
            r"\settings\cline_mcp_settings.json"),
        "VS Code (Copilot MCP)": os.path.expandvars(
            r"%APPDATA%\Code\User\mcp.json"),
        "WorkBuddy": os.path.expandvars(r"%USERPROFILE%\.workbuddy\mcp.json"),
        "Kimi Code": os.path.expandvars(r"%USERPROFILE%\.kimi-code\mcp.json"),
    }
    print(json.dumps({
        "ok": True,
        "server_name": "dsh-origin",
        "server": server,
        "note": "把下面片段合并进对应客户端配置文件的 mcpServers 字段；"
                "也可直接运行 python install.py 自动写入。",
        "clients": {name: {"config_path": path,
                           "mcpServers": {"dsh-origin": server}}
                    for name, path in configs.items()},
    }, ensure_ascii=False, indent=2))
    return 0


def _doctor():
    """环境自检（等价 origin_diagnose，不启动 Origin）。"""
    sys.path.insert(0, PLUGIN_DIR)
    import origin_engine as engine
    print(json.dumps(engine.diagnose(connect_probe=False),
                     ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--print-config" in argv:
        return _print_config()
    if "--doctor" in argv:
        return _doctor()
    missing = _preflight()
    if missing:
        print(json.dumps({
            "ok": False,
            "error_code": "dependency_missing",
            "error": "缺少运行依赖：" + "; ".join(missing),
            "next_actions": ["在当前 Python 环境执行: pip install -r "
                             f"{os.path.join(PLUGIN_DIR, 'requirements.txt')}"],
        }, ensure_ascii=False))
        return 1
    # 委托主服务器（共享 50 工具与同步 stdio JSON-RPC 主循环）
    sys.path.insert(0, PLUGIN_DIR)
    import origin_mcp_server as server
    server._sync_stdio_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
