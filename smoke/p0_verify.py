# -*- coding: utf-8 -*-
"""P0 五项功能验证（真机）：
P0-2 LabTalk 门禁 / P0-3 release/reconnect / P0-4 fit 参数 / P0-5 manage_plots+manage_data
P0-1 看门狗：正常调用不误触发（软超时 60s 不应介入）。
"""
import sys, json
sys.path.insert(0, r"D:\workbuddyworkspace\compare\dsh-origin-plugin")
import numpy as np
import origin_engine as engine

PASS, FAIL = [], []

def check(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print(("[OK ] " if cond else "[FAIL] ") + name + (f" | {info}" if info else ""))

# ---------- P0-2 LabTalk 门禁 ----------
w = engine.write_data({"x": [1, 2, 3], "y": [1, 4, 9]})
bk = w["worksheet"].split("]")[0].lstrip("[")
r = engine.labtalk(f"delete {bk};")
check("P0-2 delete 默认拦截", (not r.get("ok"))
      and r.get("error_code") == "labtalk_blocked", r.get("error"))
r = engine.labtalk('wks.colWidth$ = "delete";')   # 字符串内 token 不该拦
check("P0-2 字符串内 delete 不误杀", r.get("ok") or r.get("error_code") != "labtalk_blocked",
      str(r.get("error_code")))
r = engine.labtalk(f"delete {bk};", confirm=True)
check("P0-2 confirm=true 放行 delete", r.get("ok") is True)
r = engine.labtalk("page.nlayers")
check("P0-2 正常命令不受影响", r.get("ok") is True, f"readback={r.get('readback')}")

# ---------- P0-3 release / reconnect ----------
r = engine.release()
check("P0-3 release ok", r.get("ok") and r.get("released") is True
      and r.get("origin_still_running") in (True, False), str(r.get("detail"))[:40])
r = engine.status()
check("P0-3 release 后自动重连（status）", r.get("ok") is True
      and r.get("connected") is True)
r = engine.reconnect()
check("P0-3 reconnect 幂等", r.get("ok") is True and r.get("connected") is True)

# ---------- P0-4 fit 参数 ----------
x = list(np.linspace(0, 10, 40))
y = [1.5 * np.exp(-((v - 5) ** 2) / (2 * 1.2 ** 2)) + 0.3 * np.random.RandomState(7).randn()
     for v in x]
w2 = engine.write_data({"x": x, "y": [round(v, 6) for v in y],
                        "err": [0.1] * len(x)})
r = engine.fit(w2["worksheet"], 0, 1, kind="Gauss",
               initial_params={"A": 1.0, "xc": 4.0},
               fixed_params={"A": True})
fo = r.get("fit_options", {})
check("P0-4 NLFit 固定参数", r.get("ok") and r["parameters"].get("A") == 1.0
      and r["parameters"].get("e_A") == 0.0,
      f"A={r['parameters'].get('A')} e_A={r['parameters'].get('e_A')}")
check("P0-4 fit_options 回传", fo.get("fixed_params_applied", {}).get("A") is True)
r = engine.fit(w2["worksheet"], 0, 1, kind="linear", fixed_params={"slope": 0.0})
check("P0-4 linear 固定斜率", r.get("ok")
      and abs(r["parameters"].get("slope", 9) - 0.0) < 1e-9,
      f"slope={r['parameters'].get('slope')}")
r = engine.fit(w2["worksheet"], 0, 1, kind="Gauss", weight_col="err")
check("P0-4 NLFit 加权列", r.get("ok")
      and r.get("fit_options", {}).get("weight_col") not in (None, "None"),
      f"weight={r.get('fit_options', {}).get('weight_col')}")
r = engine.fit(w2["worksheet"], 0, 1, kind="linear", weight_col="err")
check("P0-4 linear 加权明确拒绝", (not r.get("ok"))
      and r.get("error_code") == "invalid_request", r.get("error", "")[:50])

# ---------- P0-5 manage_plots / manage_data ----------
gp = engine.plot(w2["worksheet"], y_columns=["y"], x_column="x", plot_type="line")
g = gp["graph"]
r = engine.list_graphs()
check("P0-5 图已建", gp.get("ok"), g)
r = engine.manage_plots(g, "remove", plot_index=0)
check("P0-5 remove 曲线", r.get("ok") and r.get("plots_after") == 0,
      f"after={r.get('plots_after')}")
# change_data：重新画一条再换源
gp2 = engine.plot(w2["worksheet"], y_columns=["y"], x_column="x", plot_type="line")
g2 = gp2["graph"]
w3 = engine.write_data({"x2": x, "y2": [v * -1 for v in y]})
r = engine.manage_plots(g2, "change_data", plot_index=0,
                        data_worksheet=w3["worksheet"], x_col="x2", y_col="y2")
check("P0-5 change_data 换源", r.get("ok"), str(r.get("detail"))[:50])
rv = engine.verify_graph(g2)
check("P0-5 change_data 后 verify", rv.get("passed") is True,
      f"fail={len(rv.get('failures', []))}")
# sort
ws = engine.write_data({"k": [3.0, 1.0, 2.0], "v": [30.0, 10.0, 20.0]})
r = engine.manage_data(ws["worksheet"], "sort", col="k", dec=False)
rows = engine.read_worksheet(ws["worksheet"], columns=["k", "v"])
check("P0-5 sort 升序", r.get("ok") and rows.get("columns", {}).get("k", [9])[0] == 1.0,
      str(rows.get("columns", {}).get("k", [])[:3]))
r = engine.manage_data(ws["worksheet"], "sort", col="k", dec=True)
rows = engine.read_worksheet(ws["worksheet"], columns=["k"])
check("P0-5 sort 降序", r.get("ok") and rows.get("columns", {}).get("k", [9])[0] == 3.0)
# transpose
wt = engine.write_data({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
r = engine.manage_data(wt["worksheet"], "transpose")
check("P0-5 transpose 新表 3 行 2 列", r.get("ok") and r.get("rows") == 2
      and r.get("cols") == 3, f"rows={r.get('rows')} cols={r.get('cols')}")

print(f"\nP0 VERIFY: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
engine.shutdown()
sys.exit(1 if FAIL else 0)
