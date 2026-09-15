# -*- coding: utf-8 -*-
"""
labtalk_probe4 —— 细粒度编辑第三批微探针（线宽读回 / 图例排除 / 文本对象 / 页面重命名）
=====================================================================================

只探测前两批未定的关键项，每项都做"写入→读回"闭环：
1. 线宽读回候选通道（get_float/get_int 的 w/wp/width）
2. 图例中排除单条曲线（候选键 × 图例文本读回验证）
3. 文本标注：gl.add_label 签名 + remove_label
4. 页面：gp.lname 改名 / activate / is_open / destroy / duplicate
5. 曲线隐藏：p.show 写入与读回；p.name/lname/index
6. 图例文本写入（GLabel.text = ...）
7. 轴标签数字格式：x.label.type / x.label.decPlaces + formula 字符串
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

R = {"read": {}, "write": {}, "api": {}, "errors": {}}


def rec(sec, k, v):
    R[sec][k] = v


def safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def main():
    import originpro as op
    import origin_engine as engine

    ok, _ = engine._connect_impl()
    rec("write", "connect", ok)
    if not ok:
        return
    po = op.po

    def lt_read(expr):
        v, _ = safe(engine._lt_read_float, expr)
        return v

    w = op.new_sheet("w")
    w.from_list(0, [1.0, 2, 3, 4, 5], lname="x")
    w.from_list(1, [1.0, 4, 9, 16, 25], lname="alpha")
    w.from_list(2, [2.0, 3, 5, 7, 11], lname="beta")
    gp = op.new_graph(lname="P4")
    gl = gp[0]
    gl.add_plot(w, 1, 0, type="l")
    gl.add_plot(w, 2, 0, type="y")
    gl.rescale()
    short = gp.obj.GetName()
    rec("api", "short_name", short)
    po.LT_execute(f"win -a {short};")

    # ---- 1. 线宽读回候选 ----
    p0 = gl.plot_list()[0]
    for key in ("w", "wp", "width", "linewidth", "linewidth_pt"):
        v, e = safe(p0.get_float, key)
        rec("read", f"get_float('{key}')", v if e is None else str(e)[:50])
    for key in ("w", "wp"):
        v, e = safe(p0.get_int, key)
        rec("read", f"get_int('{key}')", v if e is None else str(e)[:50])
    # 写 -wp（pt）后读回候选
    _, e = safe(p0.set_cmd, "-wp 4")
    rec("write", "set_cmd('-wp 4')", "ok" if e is None else str(e)[:60])
    for key in ("w", "wp", "linewidth"):
        v, _ = safe(p0.get_float, key)
        rec("write", f"after -wp4 get_float('{key}')", v)
    # LabTalk 侧读回候选（激活图后）
    for expr in ("plot.w", "plot.wp", "layer.plot.w", "%C.w", "%C.wp"):
        rec("read", f"LT:{expr}", lt_read(expr))

    # ---- 2. 图例中排除单条曲线 ----
    lgnd, _ = safe(gl.label, "Legend")
    txt0 = safe(getattr, lgnd, "text")[0] if lgnd is not None else None
    rec("read", "legend.text(before)", repr(txt0)[:120])
    for key in ("showinlegend", "legend", "showlegend", "legendshow", "inlegend",
                "legendexclude", "excludelegend"):
        _, e = safe(p0.set_int, key, 0)
        if e is not None:
            rec("write", f"plot.set_int('{key}',0)", f"ERR {str(e)[:50]}")
            continue
        t, _ = safe(getattr, lgnd, "text")
        changed = (t != txt0)
        rec("write", f"plot.set_int('{key}',0)", {
            "legend_changed": changed, "legend_text": repr(t)[:100]})
        if changed:
            # 恢复
            safe(p0.set_int, key, 1)
            t2, _ = safe(getattr, lgnd, "text")
            rec("write", f"plot.set_int('{key}',1) 恢复", repr(t2)[:100])
            break

    # ---- 3. 文本标注 add_label / remove_label ----
    for meth in ("add_label", "remove_label"):
        f = getattr(gl, meth, None)
        rec("api", f"gl.{meth} 签名", str(safe(inspect.signature, f)[0]) if f else "missing")
    try:
        lb = gl.add_label('P4 note', 2, 20)      # 猜测签名 (text, x, y)
        rec("api", "add_label('P4 note',2,20)", repr(lb)[:60])
    except Exception as e:
        rec("api", "add_label(text,x,y) 失败", str(e)[:120])
        try:
            lb = gl.add_label('P4 note')
            rec("api", "add_label('P4 note')", repr(lb)[:60])
        except Exception as e2:
            rec("api", "add_label(text) 失败", str(e2)[:120])
    try:
        cnt = gl.obj.GraphObjects.Count
        rec("api", "GraphObjects.Count(after add)", cnt)
    except Exception as e:
        rec("errors", "GraphObjects.Count", str(e)[:80])

    # ---- 4. 页面改名 / 激活 / 存在性 / 复制 ----
    _, e = safe(setattr, gp, "lname", "P4 Renamed")
    rec("write", "gp.lname='P4 Renamed'", {"err": str(e)[:60] if e else None,
                                           "lname": safe(getattr, gp, "lname")[0]})
    rec("api", "gp.is_open", safe(getattr, gp, "is_open")[0])
    rec("api", "gp.is_active", safe(getattr, gp, "is_active")[0])
    _, e = safe(gp.activate)
    rec("write", "gp.activate()", "ok" if e is None else str(e)[:60])
    dup, e = safe(gp.duplicate)
    rec("write", "gp.duplicate()", repr(dup)[:60] if e is None else str(e)[:80])
    if dup is not None:
        n = safe(getattr, dup, "lname")[0] or safe(lambda: dup.obj.GetName())[0]
        rec("api", "duplicate name", str(n)[:40])
        safe(dup.destroy)
        rec("write", "duplicate.destroy()", "called")

    # ---- 5. 曲线隐藏 / 名称 ----
    p1 = gl.plot_list()[1]
    rec("read", "p1.name/lname/index", [safe(getattr, p1, "name")[0],
                                        safe(getattr, p1, "lname")[0],
                                        safe(getattr, p1, "index")[0]])
    _, e = safe(setattr, p1, "show", False) if hasattr(p1, "show") else (None, "no show attr")
    if e:
        _, e2 = safe(p1.show, 0)
        rec("write", "p1.show(0)", "ok" if e2 is None else str(e2)[:60])
    else:
        rec("write", "p1.show=False", "ok")
    rec("read", "p1.show(getattr)", repr(safe(getattr, p1, "show")[0])[:40])
    try:
        v, _ = safe(p1.get_int, "show")
        rec("read", "p1.get_int('show')", v)
    except Exception:
        pass

    # ---- 6. 图例文本写入 ----
    if lgnd is not None:
        before, _ = safe(getattr, lgnd, "text")
        _, e = safe(setattr, lgnd, "text", "A\\r\\nB")
        after, _ = safe(getattr, lgnd, "text")
        rec("write", "legend.text 写入", {"err": str(e)[:60] if e else None,
                                          "before": repr(before)[:60],
                                          "after": repr(after)[:60]})
        if e is None:
            safe(setattr, lgnd, "text", before)
            rec("write", "legend.text 恢复", "ok")

    # ---- 7. 轴标签数字格式 ----
    for key, val in (("x.label.type", 1), ("x.label.decPlaces", 2)):
        _, e = safe(gl.set_int, key, val)
        v, _ = safe(gl.get_int, key)
        rec("write", f"gl.set_int('{key}',{val})",
            {"err": str(e)[:50] if e else None, "readback": v})
    s, e = safe(gl.set_str, "x.label.formula$", "#,##0.0")
    rec("write", "gl.set_str('x.label.formula$')", "ok" if e is None else str(e)[:80])

    # ---- 8. 图层单位切换后的几何精确性 ----
    po.LT_execute(f"win -a {short}; layer.unit = 1;")
    rec("read", "layer geometry % (unit=1)",
        [lt_read("layer.left"), lt_read("layer.top"),
         lt_read("layer.width"), lt_read("layer.height")])
    po.LT_execute("layer.unit = 3;")
    rec("read", "layer geometry cm (unit=3)",
        [lt_read("layer.left"), lt_read("layer.top"),
         lt_read("layer.width"), lt_read("layer.height")])
    po.LT_execute("layer.unit = 1;")

    rec("write", "done", True)


if __name__ == "__main__":
    main()
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        tempfile.gettempdir(), "labtalk_probe4_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, ensure_ascii=False, indent=1, default=str)
    print("PROBE4 REPORT ->", out)
    for sec in ("read", "write", "api", "errors"):
        print(f"\n=== {sec.upper()} ===")
        for k, v in R.get(sec, {}).items():
            print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:200]}")
