# -*- coding: utf-8 -*-
"""smoke/http_template_verify —— 端到端冒烟（HTTP transport + 模板资产）
=========================================================================

串行运行、真机连 Origin（单进程共享一个 COM 实例）：
  1) HTTP：起服务（线程）→ GET /health → POST /mcp 发 initialize / tools/list /
     tools/call(origin_status)；若 DSH_ORIGIN_HTTP_TOKEN 已设（本脚本默认设一个
     测试 token）则额外验证 401。
  2) 模板：建一张图 → template_save → template_list → template_apply 到另一张图
     → 断言逐项 status。

结尾打印 `HTTP-TEMPLATE-VERIFY OK` 或列出失败项。输出可直接重定向到文件再读。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

# 本机环境设了 HTTP_PROXY（指向本地代理），会把手 localhost 请求也路由到代理导致
# 超时。冒烟脚本自己作为客户端时需直连 127.0.0.1，故安装一个空 ProxyHandler 的 opener
# 绕过代理（仅影响本脚本内的 urllib 客户端，不改系统环境，不影响真实 MCP 客户端）。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

PORT = int(os.environ.get("DSH_ORIGIN_HTTP_PORT", "8731"))
# 默认启用一个测试 token，用于验证 401；如外部已设则用外部的
TOKEN = os.environ.get("DSH_ORIGIN_HTTP_TOKEN") or "smoke-test-token-8731"
os.environ["DSH_ORIGIN_HTTP_TOKEN"] = TOKEN

_failures = []


def _fail(msg):
    _failures.append(msg)
    print(f"[FAIL] {msg}", flush=True)


def _ok(msg):
    print(f"[OK]   {msg}", flush=True)


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
def _http_post_mcp(method, params=None, with_token=True):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/mcp", data=body, method="POST",
        headers={"Content-Type": "application/json"})
    if TOKEN and with_token:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _http_health():
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/health")
    try:
        # 短超时：冷连接 Origin 可能要几秒，留给多次重试，避免一次性耗尽预算
        resp = urllib.request.urlopen(req, timeout=8)
        return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")
    except Exception as e:  # noqa
        return None, str(e)


def _wait_health(timeout=45):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, body = _http_health()
        if st == 200 and isinstance(body, dict) and body.get("ok"):
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# 1) HTTP transport 验证
# ---------------------------------------------------------------------------
def verify_http():
    print("=== 1) HTTP transport ===", flush=True)
    # health
    if not _wait_health():
        _fail("GET /health 在超时内未就绪")
        return
    st, body = _http_health()
    if st != 200 or not isinstance(body, dict) or not body.get("ok"):
        _fail(f"GET /health 异常: {st} {body}")
    else:
        _ok(f"GET /health -> connected={body.get('connected')} tools={body.get('tools')}")
        if not isinstance(body.get("tools"), int) or body["tools"] <= 0:
            _fail("health.tools 非正整数")

    # initialize
    st, body = _http_post_mcp("initialize", {"protocolVersion": "2024-11-05"})
    if st != 200 or "result" not in body or "serverInfo" not in body["result"]:
        _fail(f"initialize 异常: {st} {body}")
    else:
        _ok(f"initialize -> protocolVersion={body['result'].get('protocolVersion')}")

    # tools/list
    st, body = _http_post_mcp("tools/list")
    tools = (body.get("result", {}) or {}).get("tools") if st == 200 else None
    if not isinstance(tools, list) or len(tools) == 0:
        _fail(f"tools/list 异常: {st} {body}")
    else:
        names = {t["name"] for t in tools}
        _ok(f"tools/list -> {len(tools)} 个工具")
        for need in ("origin_status", "origin_template_save",
                     "origin_template_list", "origin_template_apply"):
            if need not in names:
                _fail(f"tools/list 缺少 {need}")

    # tools/call origin_status
    st, body = _http_post_mcp("tools/call",
                              {"name": "origin_status", "arguments": {}})
    if st != 200 or "result" not in body:
        _fail(f"tools/call origin_status 异常: {st} {body}")
    else:
        res = body["result"]
        if res.get("isError"):
            _fail(f"tools/call origin_status isError=true: {res}")
        else:
            _ok("tools/call origin_status -> 成功")
            # 解析 content[0].text 是否含 ok 字段
            try:
                txt = res["content"][0]["text"]
                j = json.loads(txt)
                if j.get("ok"):
                    _ok("origin_status 返回 connected=true（Origin 已连接）")
                else:
                    _fail(f"origin_status 返回 ok=false: {j.get('error')}")
            except Exception as e:  # noqa
                _fail(f"origin_status content 解析失败: {e}")

    # 401 鉴权
    st, body = _http_post_mcp("tools/list", with_token=False)
    if st != 401:
        _fail(f"无 token 请求应返回 401，实际 {st}")
    else:
        _ok("无 token 请求 -> 401（鉴权生效）")
    # 带 token 的重复确认能通过
    st2, _ = _http_post_mcp("tools/list", with_token=True)
    if st2 != 200:
        _fail(f"带 token 请求应 200，实际 {st2}")
    else:
        _ok("带 token 请求 -> 200")


# ---------------------------------------------------------------------------
# 2) 模板资产验证
# ---------------------------------------------------------------------------
def verify_template():
    print("=== 2) 模板资产（save/list/apply） ===", flush=True)
    import origin_engine as _eng
    import origin_template as otmpl

    # 建数据 + 两张图
    w = _eng.write_data({"x": [1, 2, 3, 4, 5], "y": [2, 4, 1, 5, 3]})
    if not (isinstance(w, dict) and w.get("ok")):
        _fail(f"write_data 失败: {w}")
        return
    ws = w["worksheet"]

    g1 = _eng.plot(ws, plot_type="line")
    if not (isinstance(g1, dict) and g1.get("ok")):
        _fail(f"plot(g1) 失败: {g1}")
        return
    g1name = g1["graph"]

    # 给 g1 一个可识别样式（颜色/线宽/轴标题/图例）
    _eng.edit_plot(g1name, [{"plot": 0, "color": "#D55E00", "line_width_pt": 3.0}])
    _eng.edit_axis(g1name, axis="x", title="Energy (eV)")
    _eng.edit_axis(g1name, axis="y", title="Intensity (a.u.)")
    _eng.edit_legend(g1name, {"font_size_pt": 12})

    # save
    sv = otmpl.template_save(g1name, "smoke_tmpl", category="smoke")
    if not (isinstance(sv, dict) and sv.get("ok")):
        _fail(f"template_save 失败: {sv}")
        return
    _ok(f"template_save -> {sv.get('path')} （style_snapshot={sv.get('style_snapshot')}）")

    # list
    ls = otmpl.template_list(category="smoke")
    if not (isinstance(ls, dict) and ls.get("ok")):
        _fail(f"template_list 失败: {ls}")
        return
    names = {t["name"] for t in ls.get("templates", [])}
    if "smoke_tmpl" not in names:
        _fail(f"template_list 未包含 smoke_tmpl（实际 {names}）")
    else:
        _ok(f"template_list -> 找到 smoke_tmpl 等 {ls.get('count')} 个模板")

    # 第二张图（默认样式）
    g2 = _eng.plot(ws, plot_type="line")
    if not (isinstance(g2, dict) and g2.get("ok")):
        _fail(f"plot(g2) 失败: {g2}")
        return
    g2name = g2["graph"]

    # apply
    ap = otmpl.template_apply(g2name, "smoke_tmpl")
    if not (isinstance(ap, dict) and ap.get("ok")):
        _fail(f"template_apply 失败: {ap}")
        return
    counts = ap.get("status_counts", {})
    n_total = ap.get("n_total", 0)
    if n_total == 0:
        _fail("template_apply 未产生任何改动项")
        return
    _ok(f"template_apply -> {n_total} 项改动，状态分布 {counts}")
    if counts.get("applied", 0) == 0 and counts.get("applied_unverified", 0) == 0:
        _fail(f"template_apply 没有任何 applied/applied_unverified 项: {counts}")
    else:
        _ok("template_apply 至少部分项 applied/applied_unverified（逐项 status 正常）")

    # 清理测试模板 JSON（保留 .opju 无所谓）
    try:
        import os as _os
        p = sv.get("path")
        if p and _os.path.exists(p):
            _os.remove(p)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    # 预热 Origin COM 连接（主线程先连，HTTP 服务的首次 status 即命中已连接状态）
    try:
        import origin_engine as _eng
        _eng.status()
    except Exception:
        pass

    # 在后台线程起 HTTP 服务（与冒烟主线程共享同一引擎 COM 实例）
    def _serve():
        import origin_mcp_http as httpmod
        httpmod.run_server(port=PORT, host="127.0.0.1")

    srv = threading.Thread(target=_serve, daemon=True)
    srv.start()
    time.sleep(1.0)

    try:
        verify_http()
    except Exception as e:  # noqa
        _fail(f"verify_http 抛异常: {e}")

    try:
        verify_template()
    except Exception as e:  # noqa
        _fail(f"verify_template 抛异常: {e}")

    print("\n" + "=" * 60, flush=True)
    if _failures:
        print("HTTP-TEMPLATE-VERIFY FAILED:", flush=True)
        for f in _failures:
            print(f"  - {f}", flush=True)
        return 1
    print("HTTP-TEMPLATE-VERIFY OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
