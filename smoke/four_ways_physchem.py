# -*- coding: utf-8 -*-
"""四种调用方式 × 大学物理化学案例 冒烟（2026-09-17）。

四个案例（覆盖不同图型与拟合需求）：
  1. boyle      理想气体等温线族（多序列折线，PV=nRT）
  2. beer       朗伯-比尔标准曲线（散点 + 线性拟合，求斜率=εb）
  3. titration  强酸强碱滴定曲线（S 型 + 一阶导数找等当点）
  4. arrhenius  阿伦尼乌斯图（ln k vs 1/T，斜率求活化能 Ea）

方法 A：Origin App 按钮形态 —— 验证 bridge_manager 的 start/status/stop 与拉起进程
方法 B：零安装脚本 —— 按 skills/origin-scripting 模板生成脚本并在 Origin 引擎里执行
（方法 C MCP 客户端 / 方法 D npm 包形态 由 four_ways_client.mjs 与 npm 安装验证负责）
"""
import math
import os
import sys
import time

sys.path.insert(0, r"D:\workbuddyworkspace\compare\dsh-origin-plugin")

OUT = r"D:\workbuddyworkspace\compare\_chem_out"
os.makedirs(OUT, exist_ok=True)
PASS, FAIL = [], []


def check(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print(("[OK ] " if cond else "[FAIL] ") + name + (f" | {info}" if info else ""))


# ---------------------------------------------------------------------------
# 四个物化案例的数据
# ---------------------------------------------------------------------------
R = 8.314


def boyle_data():
    """理想气体等温线族：n=1 mol，V 从 0.01 到 0.1 m³，三个温度。"""
    vols = [round(0.01 + 0.005 * i, 5) for i in range(19)]
    cols = {"V_m3": vols}
    for T in (300, 400, 500):
        cols[f"P_{T}K_kPa"] = [round(1.0 * R * T / v / 1000.0, 4) for v in vols]
    return cols, "V_m3", [f"P_{T}K_kPa" for T in (300, 400, 500)]


def beer_data():
    """朗伯-比尔标准曲线：A = εbc，εb = 0.152 L/mmol，含空白 0.002。"""
    c = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    a = [round(0.002 + 0.152 * x + (0.0015 if i % 2 else -0.0012), 5)
         for i, x in enumerate(c)]
    return {"c_mmol_L": c, "absorbance": a}, "c_mmol_L", ["absorbance"]


def titration_data():
    """0.1 M NaOH 滴定 25 mL 0.1 M HCl：等当点 25 mL 处的 S 型 pH 曲线。"""
    v, ph = [], []
    x = 0.0
    while x <= 50.0:
        v.append(round(x, 2))
        # 用解析式生成带突跃的 pH 曲线（强酸强碱，等当点 25 mL）
        if x < 24.9:
            h = 0.1 * (25.0 - x) / (25.0 + x)
            ph.append(round(-math.log10(max(h, 1e-12)), 3))
        elif x > 25.1:
            oh = 0.1 * (x - 25.0) / (25.0 + x)
            ph.append(round(14 + math.log10(max(oh, 1e-12)), 3))
        else:
            ph.append(7.0)
        x += 0.5
    return {"V_NaOH_mL": v, "pH": ph}, "V_NaOH_mL", ["pH"]


def arrhenius_data():
    """阿伦尼乌斯：k = A exp(-Ea/RT)，Ea = 50 kJ/mol，A = 1e7。"""
    Ea, A = 50000.0, 1e7
    Ts = [300.0, 310.0, 320.0, 330.0, 340.0]
    inv_t = [round(1.0 / T, 8) for T in Ts]
    ln_k = [round(math.log(A * math.exp(-Ea / (R * T))), 5) for T in Ts]
    return {"invT_K-1": inv_t, "ln_k": ln_k}, "invT_K-1", ["ln_k"]


CASES = {
    "boyle": (boyle_data, "line", "理想气体等温线族"),
    "beer": (beer_data, "scatter", "朗伯-比尔标准曲线"),
    "titration": (titration_data, "line_symbol", "酸碱滴定曲线"),
    "arrhenius": (arrhenius_data, "scatter", "阿伦尼乌斯图"),
}

# ---------------------------------------------------------------------------
# 方法 A：Origin App（.opx 按钮）—— 验证 bridge_manager 生命周期
# ---------------------------------------------------------------------------
def method_a_app():
    print("\n=== 方法 A：Origin App（按钮形态）===")
    app_dir = os.path.join(r"D:\workbuddyworkspace\compare\dsh-origin-plugin",
                           "origin_app")
    sys.path.insert(0, app_dir)
    try:
        import bridge_manager as bm
    except Exception as e:
        check("A bridge_manager 可导入", False, str(e)[:80])
        return
    check("A bridge_manager 可导入", True)
    # start（后台拉起 stdio MCP 服务）
    r = bm.do_start()
    check("A start 拉起桥接", bool(r.get("ok")), str(r)[:150])
    time.sleep(2)
    st = bm.do_status()
    check("A status 报告运行中", bool(st.get("running")), str(st)[:150])
    # 用四个案例之一验证桥接后确实能出图（走 stdio MCP）
    cols, xc, ycs = boyle_data()
    ok_fig = _call_via_stdio(cols, xc, ycs, "line",
                             os.path.join(OUT, "A_boyle.png"))
    check("A 经桥接画出等温线族", ok_fig)
    r2 = bm.do_stop()
    check("A stop 停止桥接", bool(r2.get("ok")), str(r2)[:120])


def _call_via_stdio(cols, xc, ycs, plot_type, out_png):
    """通过 stdio JSON-RPC 调 origin_figure（模拟 MCP 客户端）。"""
    import json
    import subprocess
    py = r"D:\workbuddyworkspace\compare\_chem_venv\Scripts\python.exe"
    entry = os.path.join(r"D:\workbuddyworkspace\compare\dsh-origin-plugin",
                         "origin_mcp_stdio.py")
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "origin_figure", "arguments": {
             "columns": cols, "x_column": xc, "y_columns": ycs,
             "plot_type": plot_type, "fmt": "png", "file_path": out_png,
             "title": "physchem"}}},
    ]
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in reqs) + "\n"
    try:
        p = subprocess.run([py, "-X", "utf8", entry], input=payload,
                           capture_output=True, text=True, timeout=180,
                           cwd=r"D:\workbuddyworkspace\compare\dsh-origin-plugin")
        for line in p.stdout.splitlines():
            try:
                resp = json.loads(line)
            except Exception:
                continue
            if resp.get("id") == 2:
                txt = resp["result"]["content"][0]["text"]
                body = json.loads(txt)
                return bool(body.get("ok")) and os.path.getsize(
                    body.get("file") or "") > 0 if body.get("file") else False
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# 方法 B：零安装脚本（把生成的脚本喂给 Origin 引擎执行）
# ---------------------------------------------------------------------------
def method_b_script():
    print("\n=== 方法 B：零安装脚本（粘进 Origin 执行）===")
    import origin_engine as engine
    ok, _ = engine.connect()
    check("B 连接 Origin", ok)
    if not ok:
        return
    import originpro as op
    for name, (fn, ptype, label) in CASES.items():
        cols, xc, ycs = fn()
        try:
            wks = op.new_sheet("w", f"B_{name}")
            names = list(cols)
            for j, col in enumerate(names):
                wks.from_list(j, cols[col], lname=col)
            x_idx = names.index(xc)
            gp = op.new_graph(lname=f"B_{name}")
            gl = gp[0]
            tmap = {"line": "l", "scatter": "s", "line_symbol": "y"}
            for yc in ycs:
                gl.add_plot(wks, names.index(yc), x_idx, type=tmap.get(ptype, "l"))
            gl.rescale()
            png = os.path.join(OUT, f"B_{name}.png")
            gp.save_fig(png, width=1200)
            good = os.path.exists(png) and os.path.getsize(png) > 0
            check(f"B 脚本出图 {name}（{label}）", good,
                  f"{os.path.getsize(png) if good else 0}B")
        except Exception as e:
            check(f"B 脚本出图 {name}", False, str(e)[:100])
    engine.shutdown()


if __name__ == "__main__":
    method_a_app()
    method_b_script()
    print(f"\nFOUR-WAYS(AB): {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
    sys.exit(1 if FAIL else 0)
