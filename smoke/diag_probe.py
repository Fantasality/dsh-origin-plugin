# -*- coding: utf-8 -*-
"""诊断：OPJU 保存路径 + dual_y 模板真实报错。"""
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import origin_engine as engine

print("== 1. connect ==")
ok, conn = engine._connect_impl()
print("connect:", ok)
if not ok:
    print(conn)
    sys.exit(1)
op = engine._origin_app
po = op.po

print("== 2. op.save_project 探针 ==")
tmp = tempfile.mkdtemp(prefix="dsh_diag_")
p1 = os.path.join(tmp, "probe1.opju")
try:
    r = op.save_project(p1)
    print("op.save_project ->", repr(r), "| exists:", os.path.exists(p1))
except Exception as e:
    print("op.save_project raised:", type(e).__name__, str(e)[:200])
    print("hasattr save_project:", hasattr(op, "save_project"))
    print("attrs containing 'save':", [a for a in dir(op) if "save" in a.lower()])

print("== 3. LT save 探针（ASCII 路径） ==")
p2 = os.path.join(tmp, "probe2.opju").replace("\\", "/")
try:
    po.LT_execute(f'save "{p2}";')
    print("LT save ->", "exists:", os.path.exists(p2))
except Exception as e:
    print("LT save raised:", type(e).__name__, str(e)[:200])

print("== 4. LT save 探针（中文路径） ==")
p3 = os.path.join(tmp, "中文探针.opju").replace("\\", "/")
try:
    po.LT_execute(f'save "{p3}";')
    print("LT save cn ->", "exists:", os.path.exists(p3))
except Exception as e:
    print("LT save cn raised:", type(e).__name__, str(e)[:200])

print("== 5. dual_y 真实报错 ==")
r = engine.plot_template("dual_y", {"x": [1.0, 2, 3], "left": [1.0, 2, 3],
                                    "right": [10.0, 20, 15]})
import json
print(json.dumps(r, ensure_ascii=False, default=str)[:1200])

print("== 6. dualy 模板直查 ==")
try:
    gp = op.new_graph(lname="DiagDualY", template="dualy")
    print("new_graph template=dualy OK:", gp.obj.GetName(),
          "| Layers:", getattr(gp.obj, "Layers", "?"))
except Exception as e:
    print("new_graph dualy raised:", type(e).__name__, str(e)[:300])

print("DIAG DONE")
