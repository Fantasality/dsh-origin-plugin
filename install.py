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
# 结构分三类：标准 mcpServers / 特殊 JSON 结构 / TOML（Codex CLI）
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
    # —— 2026-09-17 扩充：更多 agent（结构各异的单独处理，见 _write_special）——
    "gemini-cli": os.path.expandvars(r"%USERPROFILE%\.gemini\settings.json"),
    "trae": os.path.expandvars(r"%USERPROFILE%\.trae\mcp.json"),
    "zed": os.path.expandvars(r"%APPDATA%\Zed\settings.json"),
    "continue": os.path.expandvars(r"%USERPROFILE%\.continue\config.json"),
    "codex": os.path.expandvars(r"%USERPROFILE%\.codex\config.toml"),
}
# 注意：DSH 不用 mcp.json（它走 cordis.patch.yml / 插件市场），单独提示，不在这里配置
DSH_HOME = os.path.expandvars(r"%USERPROFILE%\.dsh")

# 结构与标准 mcpServers 不同的客户端（键路径不同，写入器也不同）
SPECIAL_JSON = {
    # Continue：experimental.modelContextProtocolServers 是数组，元素含 transport
    "continue": ("experimental", "modelContextProtocolServers"),
    # Zed：context_servers.<name>.command 是 {path, args} 对象
    "zed": ("context_servers",),
}
TOML_CLIENTS = {"codex"}


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


def _write_special(name, path, key, server_cfg, assume_yes=False):
    """处理结构与标准 mcpServers 不同的客户端：Continue（数组）/ Zed（嵌套对象）/ Codex（TOML）。

    这些客户端的配置结构可能随版本变化，写入失败时明确告诉用户去核对，
    不要让用户以为"装好了"。
    """
    if name in TOML_CLIENTS:                      # Codex CLI: ~/.codex/config.toml
        block = (f"\n[mcp_servers.{key}]\n"
                 f'command = "{server_cfg["command"].replace(chr(92), chr(92) * 2)}"\n'
                 f'args = ["{server_cfg["args"][0].replace(chr(92), chr(92) * 2)}"]\n')
        try:
            old = ""
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    old = fh.read()
            if f"[mcp_servers.{key}]" in old:
                return "skip", "已配置（如需更新请手动编辑该 TOML 段）"
            bak = _backup(path) if os.path.isfile(path) else None
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(block)
            return "ok", "已追加 TOML 段" + (f"（备份 {os.path.basename(bak)}）" if bak else "")
        except Exception as e:
            return "fail", f"写入失败: {e}（请手动把 [mcp_servers.{key}] 段加进 {path}）"

    data = _load_json(path)
    bak = _backup(path) if os.path.isfile(path) else None
    try:
        if name == "continue":
            # Continue: experimental.modelContextProtocolServers = [ {name, transport:{...}} ]
            exp = data.setdefault("experimental", {})
            arr = exp.setdefault("modelContextProtocolServers", [])
            entry = {"name": key, "transport": {
                "type": "stdio", "command": server_cfg["command"],
                "args": list(server_cfg["args"])}}
            for i, it in enumerate(arr):
                if isinstance(it, dict) and it.get("name") == key:
                    arr[i] = entry
                    break
            else:
                arr.append(entry)
        elif name == "zed":
            # Zed: context_servers.<name> = { command: { path, args } }
            cs = data.setdefault("context_servers", {})
            cs[key] = {"command": {"path": server_cfg["command"],
                                   "args": list(server_cfg["args"])}}
        else:
            return "fail", f"未知特殊结构: {name}"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return "ok", "已写入" + (f"（备份 {os.path.basename(bak)}）" if bak else "")
    except Exception as e:
        return "fail", f"写入失败: {e}（请手动核对 {path} 的结构后粘贴命令）"


def _print_fallback(python, entry, name="dsh-origin"):
    """兜底：一个都没检测到（或用户用的是小众/自研 agent）时，给出可照抄的方案。"""
    print("=" * 68)
    print("没有检测到常见 AI 客户端的配置文件。别急，两条路：")
    print()
    print("【路 1】直接问你的 AI（最省事）")
    print("  把这句话发给你正在用的 AI：")
    print(f'    "我是用 XXX 客户端的，请帮我配置 MCP 服务器：命令 {python}，'
          f'参数 {entry}，名字叫 {name}"')
    print("  它知道自己的配置文件在哪，会自己写好。")
    print()
    print("【路 2】手动粘贴（通用 JSON 片段）")
    print(json.dumps({"mcpServers": {name: {"command": python, "args": [entry]}}},
                     ensure_ascii=False, indent=2))
    print()
    print("  把它合并到你客户端的 MCP 配置里（找不到就把这段话发给 AI 让它找）。")
    if os.path.isdir(DSH_HOME):
        print()
        print("【检测到你在用 DSH】DSH 不走 mcp.json —— 请在 DSH 插件市场搜")
        print("  dsh-origin，或让 AI 读 skills/install-dsh-origin/SKILL.md 来装。")
    print("=" * 68)


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
        _print_fallback(args.python, ENTRY, args.name)
        return 0
    print("检测到已安装客户端：")
    for name, path in installed.items():
        mark = "（特殊结构）" if name in SPECIAL_JSON or name in TOML_CLIENTS else ""
        print(f"  - {name}: {path} {mark}")
    if args.list:
        return 0

    server_cfg = {"command": args.python, "args": [ENTRY]}
    targets = {k: v for k, v in installed.items()
               if not args.client or k == args.client.lower()}
    if not targets:
        print(f"目标客户端 {args.client!r} 未检测到配置文件，无操作。")
        print("可用键名：" + ", ".join(sorted(CLIENTS)))
        return 1
    print(f"\n服务器命令: {args.python} {ENTRY}")
    print(f"配置键名: {args.name}\n")
    rc = 0
    for name, path in targets.items():
        if name in SPECIAL_JSON or name in TOML_CLIENTS:
            if args.remove:
                status, detail = "skip", "特殊结构客户端请手动移除对应段"
            else:
                status, detail = _write_special(name, path, args.name, server_cfg,
                                                assume_yes=args.yes)
        elif args.remove:
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
        print("验证：在客户端里说「调用 origin_status」，应返回 connected: true。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
