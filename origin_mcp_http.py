# -*- coding: utf-8 -*-
"""origin_mcp_http —— Origin MCP 的 HTTP transport（多客户端共享一个 Origin）
============================================================================

动机（为什么做这个入口）
-----------------------
stdio 模式每个 MCP 客户端起一个进程、抢同一个 Origin COM 实例；竞品
erannave/originlab-mcp 用 HTTP 让多个客户端共享一个 Origin 实例，且对 WSL /
远程客户端可达。本模块提供纯标准库（http.server + json）实现的 HTTP 端点。

设计要点
--------
- 只绑定 127.0.0.1（安全，不暴露到公网）。
- 端口：--port 或环境变量 DSH_ORIGIN_HTTP_PORT，默认 8731。
- 可选 token 鉴权：环境变量 DSH_ORIGIN_HTTP_TOKEN 设置时，POST /mcp 必须带
  `Authorization: Bearer <token>`，否则 401（参考竞品 Ge-Shun 做法）。
- GET /health：{"ok": true, "connected": <bool>, "tools": <n>}（origin_engine.status）。
- POST /mcp（或 /）：单条 JSON-RPC 请求 → 单条 JSON-RPC 响应；复用
  origin_mcp_server 的工具注册表，并合并本插件的模板工具。
- 单线程串行处理：Origin 共用一个 COM 实例，并发会踩踏（引擎内部已串行化，
  但 HTTP 层仍保持单线程，契合「多客户端共享一个 Origin」的语义）。
- 不引入任何新依赖（仅 http.server / json / os / sys）。

注意：本文件不改任何既有文件；工具注册表通过导入 origin_mcp_server 复用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# 运行期配置（由 run_server 填充），handler 直接读全局，避免线程传参复杂度。
_CFG = {"token": None, "registry": None, "tool_fns": None}


# ---------------------------------------------------------------------------
# 工具注册表：复用 origin_mcp_server，再合并本插件模板工具
# ---------------------------------------------------------------------------
def _build_registry():
    """复用 origin_mcp_server 的工具注册表 + 合并模板工具。

    模板工具用 @_synchronized 装饰会丢失 inspect.signature，因此这里直接给出
    手写 schema（与 origin_template 公开函数入参一致）。
    """
    import inspect
    import origin_mcp_server as mserver
    import origin_template as otmpl

    registry = mserver._build_tool_registry()
    tool_fns = {name: getattr(mserver, name)
                for name in list(registry)
                if callable(getattr(mserver, name, None))}

    template_tools = {
        "origin_template_save": {
            "description": (
                "把当前图页存为可复用用户模板（样式快照方案）。\n"
                "由于本机 Origin 2026 的 LabTalk save -i 无法生成 .otpu、COM 也无 "
                "SaveTemplate，故采用 JSON 样式快照（颜色/线宽/线型/符号/轴/图例/"
                "页面尺寸/图层几何），套用风格 100% 可用。\n"
                "参数：graph 图页短名；template_name 模板名；category 可选分类；"
                "overwrite 同名是否覆盖（默认否）。"),
            "schema": {
                "type": "object",
                "properties": {
                    "graph": {"type": "string"},
                    "template_name": {"type": "string"},
                    "category": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["graph", "template_name"],
            },
            "fn": otmpl.template_save,
        },
        "origin_template_list": {
            "description": (
                "列出可用模板：插件样式快照目录（含 category 子目录）+ Origin 用户"
                "模板目录。返回每项 name/category/source/kind/has_style_json 等。"),
            "schema": {
                "type": "object",
                "properties": {"category": {"type": "string"}},
                "required": [],
            },
            "fn": otmpl.template_list,
        },
        "origin_template_apply": {
            "description": (
                "把模板样式套用到指定图：读回 JSON 侧车，逐项应用颜色/线宽/线型/"
                "符号/轴/图例，每项返回 status（applied/applied_unverified/"
                "rejected/unsupported 等）。\n参数：graph 目标图页短名；"
                "template_name 模板名。"),
            "schema": {
                "type": "object",
                "properties": {
                    "graph": {"type": "string"},
                    "template_name": {"type": "string"},
                },
                "required": ["graph", "template_name"],
            },
            "fn": otmpl.template_apply,
        },
    }

    for name, spec in template_tools.items():
        registry[name] = {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["schema"],
        }
        tool_fns[name] = spec["fn"]
    return registry, tool_fns


# ---------------------------------------------------------------------------
# JSON-RPC 分发（与 origin_mcp_server._sync_stdio_server 等价的单请求逻辑）
# ---------------------------------------------------------------------------
def _handle_jsonrpc(req):
    """处理单条 JSON-RPC 请求，返回响应 dict；通知（id 为 None）返回 None。"""
    import origin_mcp_server as mserver

    msg_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}

    if msg_id is None:                       # 通知：无需响应
        return None

    if method == "initialize":
        client_pv = params.get("protocolVersion") if isinstance(params, dict) else None
        pv = client_pv if client_pv else mserver._PROTO_FALLBACK
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": pv,
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {"listChanged": False}},
            "serverInfo": {"name": "origin", "version": "2.0.0"},
        }}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "resources": [
                {"uri": "origin://session", "name": "Origin 会话全量快照（只读）",
                 "mimeType": "application/json"},
                {"uri": "origin://worksheets", "name": "工作簿/工作表列表（只读）",
                 "mimeType": "application/json"},
                {"uri": "origin://graphs", "name": "图页列表（只读）",
                 "mimeType": "application/json"},
                {"uri": "origin://worksheet/[Book]Sheet1",
                 "name": "单个工作表列数据（只读）", "mimeType": "application/json"},
            ]}}

    if method == "resources/read":
        uri = params.get("uri", "") if isinstance(params, dict) else ""
        try:
            body = mserver._resource_read_impl(uri)
        except Exception as e:
            body = {"ok": False, "error": str(e)}
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "contents": [{"uri": uri, "mimeType": "application/json",
                          "text": json.dumps(body, ensure_ascii=False, default=str)}]}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id,
                "result": {"tools": list(_CFG["registry"].values())}}

    if method == "tools/call":
        tname = params.get("name", "") if isinstance(params, dict) else ""
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        fn = _CFG["tool_fns"].get(tname)
        if fn is None:
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "content": [{"type": "text", "text": json.dumps(
                    {"ok": False, "error": f"unknown tool: {tname}",
                     "error_code": "UNKNOWN_TOOL"}, ensure_ascii=False)}],
                "isError": True}}
        try:
            accepted = set(_CFG["registry"][tname]["inputSchema"]["properties"].keys())
            valid = {k: v for k, v in args.items() if k in accepted}
            result = fn(**valid)
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "content": mserver._result_to_content_blocks(result),
                "isError": False}}
        except Exception as exc:
            import traceback
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "content": [{"type": "text", "text": json.dumps({
                    "ok": False, "error": str(exc), "error_code": "TOOL_EXCEPTION",
                    "traceback": traceback.format_exc()}, ensure_ascii=False)}],
                "isError": True}}

    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    # 静默日志（避免刷屏；冒烟用重定向文件读结果）
    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body_bytes, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body_bytes)

    def _auth_ok(self):
        """按 DSH_ORIGIN_HTTP_TOKEN 校验 Bearer；未设 token 则放行。"""
        token = _CFG["token"]
        if not token:
            return True
        if self.command == "GET" and self.path.split("?")[0] in ("/health",):
            return True  # /health 仅暴露连通性与工具数，免 token
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {token}"

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/health",):
            import origin_engine as _eng
            try:
                st = _eng.status()
                connected = bool(st.get("connected"))
            except Exception:
                connected = False
            body = {"ok": True, "connected": connected,
                    "tools": len(_CFG["registry"])}
            self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"))
            return
        self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/mcp", "/"):
            self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False).encode("utf-8"))
            return
        if not self._auth_ok():
            self._send(401, json.dumps(
                {"error": "unauthorized", "detail": "需要 Authorization: Bearer <token>"},
                ensure_ascii=False).encode("utf-8"))
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self._send(400, json.dumps({"error": f"bad request: {e}"}, ensure_ascii=False).encode("utf-8"))
            return
        try:
            resp = _handle_jsonrpc(req)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32603, "message": f"internal error: {e}"}}
        if resp is None:                      # 通知：无响应体
            self._send(202, b"")
            return
        self._send(200, json.dumps(resp, ensure_ascii=False, default=str).encode("utf-8"))


# ---------------------------------------------------------------------------
# 启动 / 配置打印
# ---------------------------------------------------------------------------
def _print_config():
    """打印各 MCP 客户端的 HTTP 接入配置片段（参考 origin_mcp_stdio._print_config）。"""
    py = sys.executable
    entry = os.path.join(PLUGIN_DIR, "origin_mcp_http.py").replace("\\", "/")
    port = int(os.environ.get("DSH_ORIGIN_HTTP_PORT", "8731"))
    token = os.environ.get("DSH_ORIGIN_HTTP_TOKEN")
    server = {"command": py, "args": [entry, "--port", str(port)]}
    mcp_json = {
        "url": f"http://127.0.0.1:{port}/mcp",
        "transport": "http",
    }
    if token:
        mcp_json["headers"] = {"Authorization": f"Bearer {token}"}
    configs = {
        "Claude Desktop (HTTP/SSE 需网关)": "Claude Desktop 原生只支持 stdio；"
        "HTTP 客户端（Cursor/Windsurf/Cline 的 HTTP 模式、自研 Agent、WSL 内客户端）"
        "可直接连下面 url。",
        "通用 HTTP MCP 客户端": mcp_json,
    }
    print(json.dumps({
        "ok": True,
        "server_name": "dsh-origin-http",
        "server": server,
        "endpoint": f"http://127.0.0.1:{port}/mcp",
        "health": f"http://127.0.0.1:{port}/health",
        "token_required": bool(token),
        "note": "多客户端共享同一个 Origin 实例；WSL 内可用 127.0.0.1（Windows 侧监听）"
                "或宿主机 IP 访问。",
        "clients": configs,
    }, ensure_ascii=False, indent=2))
    return 0


def run_server(port=None, host="127.0.0.1"):
    """启动 HTTP MCP 服务（阻塞，直到进程被结束）。"""
    if port is None:
        port = int(os.environ.get("DSH_ORIGIN_HTTP_PORT", "8731"))
    token = os.environ.get("DSH_ORIGIN_HTTP_TOKEN") or None

    registry, tool_fns = _build_registry()
    _CFG["registry"] = registry
    _CFG["tool_fns"] = tool_fns
    _CFG["token"] = token

    httpd = HTTPServer((host, int(port)), _Handler)
    sys.stderr.write(
        f"[origin_mcp_http] 监听 http://{host}:{port}/mcp "
        f"（token={'启用' if token else '关闭'}），tools={len(registry)}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--print-config" in argv:
        return _print_config()

    parser = argparse.ArgumentParser(description="Origin MCP HTTP transport")
    parser.add_argument("--port", type=int, default=None,
                        help="监听端口（默认 8731，或用 DSH_ORIGIN_HTTP_PORT）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（默认 127.0.0.1，仅本机）")
    args, _unknown = parser.parse_known_args(argv)

    run_server(port=args.port, host=args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
