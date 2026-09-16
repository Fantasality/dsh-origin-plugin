# -*- coding: utf-8 -*-
"""
install.py —— dsh-origin-plugin 一键接入常见 AI 工具（v2.3.0 新增）
====================================================================

自动检测本机已安装的 AI 工具（Claude Desktop / Cursor / Windsurf / Cline /
VS Code / WorkBuddy / Kimi Code），把本插件的 MCP stdio 服务器合并写入其
mcpServers 配置（保留既有条目，写入前自动备份）。

用法（在本插件目录下）：
    python install.py                # 交互式：检测到哪些装哪些
    python install.py --yes          # 非交互：全部已检测到的都写入
    python install.py --list         # 只检测，不写入
    python install.py --client cursor --yes
    python install.py --remove --client cursor
    python install.py --python "C:/path/python.exe"   # 指定解释器（默认当前）

服务端命令形如：
    {"command": "<python>", "args": ["<本目录>/origin_mcp_stdio.py"]}
仅依赖官方 MCP SDK；Origin/LabTalk 依赖见 requirements.txt。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(PLUGIN_DIR, "origin_mcp_stdio.py")

# 客户端 -> 配置文件路径（存在即视为已安装）
CLIENTS = {
    "claude-desktop": os.path.expandvars(r"%APPDATA%\Claude\claude_desktop_config.json"),
    "cursor": os.path.expandvars(r"%USERPROFILE%\.cursor\mcp.json"),
    "windsurf": os.path.expandvars(r"%USERPROFILE%\.codeium\windsurf\mcp_config.json"),
    "cline": os.path.expandvars(
        r"%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev"
        r"\settings\cline_mcp_settings.json"),
    "vscode": os.path.expandvars(r"%APPDATA%\Code\User\mcp.json"),
    "workbuddy": os.path.expandvars(r"%USERPROFILE%\.workbuddy\mcp.json"),
    "kimi-code": os.path.expandvars(r"%USERPROFILE%\.kimi-code\mcp.json"),
}


def _detect_installed():
    found = {}
    for name, path in CLIENTS.items():
        if path and os.path.isfile(path):
            found[name] = path
    return found


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _backup(path):
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{path}.bak-{ts}"
    try:
        shutil.copy2(path, bak)
        return bak
    except Exception:
        return None


def _write_server(path, key, server_cfg, assume_yes=False):
    """把 server_cfg 合并进 path 的 mcpServers[key]；返回 (status, detail)。"""
    data = _load_json(path)
    mcp = data.get("mcpServers")
    if not isinstance(mcp, dict):
        mcp = {}
        # VS Code 新版把服务器放 mcpServers 顶层；cline 在独立字段——统一 mcpServers
        data["mcpServers"] = mcp
    if mcp.get(key) == server_cfg:
        return "skip", "已配置且无变化"
    if mcp.get(key) and not assume_yes:
        ans = input(f"  ! {key} 已存在旧配置，覆盖？[y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            return "skip", "用户跳过（保留旧配置）"
    bak = _backup(path)
    mcp[key] = server_cfg
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        return "fail", f"写入失败: {e}" + (f"（备份在 {bak}）" if bak else "")
    return "ok", ("已写入" + (f"（备份 {os.path.basename(bak)}）" if bak else ""))


def _remove_server(path, key):
    data = _load_json(path)
    mcp = data.get("mcpServers")
    if not isinstance(mcp, dict) or key not in mcp:
        return "skip", "未配置"
    bak = _backup(path)
    del mcp[key]
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        return "fail", f"写入失败: {e}"
    return "ok", "已移除" + (f"（备份 {os.path.basename(bak)}）" if bak else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把 dsh-origin MCP 服务器写入常见 AI 工具配置")
    ap.add_argument("--list", action="store_true", help="仅检测已安装的客户端")
    ap.add_argument("--yes", action="store_true", help="非交互确认")
    ap.add_argument("--client", default="", help="只处理指定客户端（见 --list 的键名）")
    ap.add_argument("--remove", action="store_true", help="移除配置而非写入")
    ap.add_argument("--name", default="dsh-origin", help="mcpServers 里的键名（默认 dsh-origin）")
    ap.add_argument("--python", default=sys.executable, help="Python 解释器路径")
    args = ap.parse_args(argv)

    installed = _detect_installed()
    if not installed:
        print("未检测到已知 AI 工具的配置文件。")
        print("可手动把以下片段合并进你的客户端 mcpServers：")
        print(json.dumps({args.name: {
            "command": args.python, "args": [ENTRY]}}, ensure_ascii=False, indent=2))
        return 0
    print("检测到已安装客户端：")
    for name, path in installed.items():
        print(f"  - {name}: {path}")
    if args.list:
        return 0

    server_cfg = {"command": args.python, "args": [ENTRY]}
    targets = {k: v for k, v in installed.items()
               if not args.client or k == args.client.lower()}
    if not targets:
        print(f"目标客户端 {args.client!r} 未检测到配置文件，无操作。")
        return 1
    print(f"\n服务器命令: {args.python} {ENTRY}")
    print(f"配置键名: {args.name}\n")
    rc = 0
    for name, path in targets.items():
        if args.remove:
            status, detail = _remove_server(path, args.name)
        else:
            status, detail = _write_server(path, args.name, server_cfg,
                                           assume_yes=args.yes)
        mark = {"ok": "[√]", "skip": "[-]", "fail": "[x]"}.get(status, "[?]")
        print(f"  {mark} {name}: {detail}")
        if status == "fail":
            rc = 1
    if not args.remove:
        print("\n完成。重启对应客户端后生效；在 Origin 未启动时首次调用会自动拉起（5~45 秒）。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
