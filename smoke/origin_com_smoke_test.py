# -*- coding: utf-8 -*-
"""
最小 Origin COM 自动化冒烟测试（步骤 1, v4 —— 推荐链路）
链路: originpro (comtypes COM) -> 连接已运行/自动启动 Origin -> 新建 worksheet
      -> 写入 x=1..10, y=x^2 -> 折线图 add_plot(type='l') -> 导出 PNG (+SVG)

运行: <venv python> -X utf8 origin_com_smoke_test.py
"""
import os
import sys
import traceback


def main():
    out_dir = os.path.join(os.path.expanduser("~"), "dsch_origin_plugin", "output")
    os.makedirs(out_dir, exist_ok=True)

    import originpro as op

    print("[1/7] 连接 Origin (originpro/OriginExt COM) ...")
    # originpro 首次访问即通过 OriginExt.ApplicationSI 连接（已运行则复用）
    print(f"      Origin 路径: {op.path()}")

    print("[2/7] 新建 worksheet ...")
    wks = op.new_sheet('w', 'COMTest')
    if not wks:
        raise RuntimeError("op.new_sheet 返回空")
    print(f"      sheet: {wks}")

    print("[3/7] 写入 x=1..10, y=x^2 ...")
    x = list(range(1, 11))
    y = [v * v for v in x]
    wks.from_list(0, x, lname='X')
    wks.from_list(1, y, lname='Y')
    rx = wks.to_list(0)
    ry = wks.to_list(1)
    print(f"      readback x[:3]={rx[:3]} y[:3]={ry[:3]}")
    if rx[:3] != [1.0, 2.0, 3.0] or ry[:3] != [1.0, 4.0, 9.0]:
        raise RuntimeError(f"数据回读校验失败: {rx[:3]}, {ry[:3]}")

    print("[4/7] 创建折线图 ...")
    gp = op.new_graph('COMPlot')
    gl = gp[0]
    p = gl.add_plot(wks, 1, 0, type='l')  # Y=col1(索引1), X=col0(索引0), line
    if p is None:
        raise RuntimeError("add_plot 失败")
    gl.rescale()
    print(f"      plot added: {p}")

    print("[5/7] 导出 PNG ...")
    png_abs = os.path.join(out_dir, "comtest.png")
    res = gp.save_fig(png_abs, width=600, replace=True)
    print(f"      save_fig -> {res}")
    if not (res and os.path.exists(res)):
        raise RuntimeError("PNG 导出失败")

    print("[6/7] 导出 SVG ...")
    svg_abs = os.path.join(out_dir, "comtest.svg")
    res2 = gp.save_fig(svg_abs, replace=True)
    print(f"      save_fig -> {res2}")
    if not (res2 and os.path.exists(res2)):
        raise RuntimeError("SVG 导出失败")

    print("[7/7] 汇总 ...")
    sizes = {os.path.basename(res): os.path.getsize(res),
             os.path.basename(res2): os.path.getsize(res2)}
    print(f"RESULT: OK  files={sizes}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("RESULT: FAIL")
        traceback.print_exc()
        sys.exit(1)
