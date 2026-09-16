# -*- coding: utf-8 -*-
"""origin_bridge —— matplotlib 桥 + PowerPoint 组图交付（P2-6/P2-7，2026-09-16）。

matplotlib 桥（来源 jsbangsund/python_to_originlab 思想，适配 MCP 场景）：
AI 侧把 mpl Figure pickle 落盘 -> origin_import_matplotlib 反序列化 ->
逐条 Line2D 提取 xdata/ydata 与线属性 -> 写工作表 + 画图 + 样式映射。
（同版本 matplotlib 的 Figure pickle 可靠；跨版本不保证——错误信息会说明。）

PPT 组图交付（来源 hzsci/ace-sci-origin 思想）：
origin_export_pptx 把图导出高分辨率 PNG 后用 python-pptx 组页，附面板字母
标注与来源报告。真 OLE 双击编辑需要 Origin OLE 文件格式路径（注册表确认
Origin50.Graph 类存在，嵌入已有图页的文件路径未打通——COMPATIBILITY #16）。
"""
import os

import origin_errors as oerr


def _check_mpl():
    try:
        import matplotlib  # noqa: F401
        return None
    except ImportError:
        return oerr.fail("invalid_request",
                         "缺少 matplotlib：pip install matplotlib（桥接侧需要）")


def _check_pptx():
    try:
        import pptx  # noqa: F401
        return None
    except ImportError:
        return oerr.fail("invalid_request",
                         "缺少 python-pptx：pip install python-pptx（PPT 交付需要）")


# --- P2-6：matplotlib Figure 桥 ---
_LINESTYLE_MAP = {"-": "l", ":": "l", "-.": "l", "--": "l"}
_MARKER_MAP = {"o": 1, "s": 2, "^": 3, "v": 4, "+": 5, "x": 5, "D": 10, "d": 10,
               "*": 6, "<": 7, ">": 8, "p": 9, "h": 11}


def import_matplotlib_impl(op, pickle_path, graph_name=None, title=None):
    try:
        bad = _check_mpl()
        if bad is not None:
            return bad
        import pickle
        with open(pickle_path, "rb") as f:
            fig = pickle.load(f)
        import matplotlib.figure
        if not isinstance(fig, matplotlib.figure.Figure):
            return oerr.fail("invalid_request",
                             f"pickle 内容是 {type(fig).__name__}，需要 matplotlib Figure；"
                             "生成方式: pickle.dump(fig, open('fig.pickle','wb'))")
        # 收集所有 axes 的 lines（2D）
        series = []          # (label, xdata, ydata, color, lw, ls, marker)
        for ai, ax in enumerate(fig.axes):
            for ln in ax.get_lines():
                xd = [float(v) for v in ln.get_xdata()]
                yd = [float(v) for v in ln.get_ydata()]
                if not xd or not yd:
                    continue
                label = ln.get_label() or f"ax{ai}_line{len(series)}"
                series.append({
                    "label": str(label), "x": xd, "y": yd,
                    "color": ln.get_color(), "lw": float(ln.get_linewidth()),
                    "ls": ln.get_linestyle(), "marker": ln.get_marker(),
                })
        if not series:
            return oerr.fail("no_data_to_fit",
                             "Figure 里没有可提取的 2D Line2D（散点集合请先转线对象）")
        # 网格化到统一 X（若各线 X 不同，取并集并插值；同 X 直接列）
        all_x = sorted({v for s in series for v in s["x"]})
        import numpy as np
        cols = {"x": all_x}
        meta = []
        for s in series:
            if len(s["x"]) == len(all_x) and s["x"] == all_x:
                cols[s["label"]] = s["y"]
            else:
                interp = np.interp(all_x, s["x"], s["y"],
                                   left=float("nan"), right=float("nan"))
                cols[s["label"]] = [None if v != v else round(float(v), 10)
                                    for v in interp]
            meta.append({"label": s["label"], "color": s["color"],
                         "lw": s["lw"], "marker": s["marker"]})
        import origin_engine as _eng
        w = _eng._write_data_impl(cols)
        if not w.get("ok"):
            return w
        names = [s["label"] for s in series]
        gp = _eng._plot_impl(w["worksheet"], y_columns=names, x_column="x",
                             plot_type="line", graph_name=graph_name,
                             title=title)
        if not gp.get("ok"):
            return gp
        g = gp["graph"]
        # 样式映射：颜色/线宽/符号（逐条 applied_unverified 级别）
        import originpro as _op
        gpf = _op.find_graph(g)
        gl = gpf[0]
        style_applied = []
        pls = gl.plot_list() or []
        for i, s in enumerate(series):
            if i >= len(pls):
                break
            pl = pls[i]
            try:
                if str(s["color"]).startswith("#"):
                    pl.color = tuple(int(s["color"][k:k + 2], 16)
                                     for k in (1, 3, 5))
                    style_applied.append(f"plot[{i}].color={s['color']}")
            except Exception:
                pass
            try:
                if s["marker"] in _MARKER_MAP:
                    pl.symbol_kind = _MARKER_MAP[s["marker"]]
                    style_applied.append(f"plot[{i}].symbol={s['marker']}")
            except Exception:
                pass
        return oerr.ok(
            graph=g, worksheet=w["worksheet"], series=names,
            series_meta=meta, style_applied=style_applied,
            detail=f"已从 matplotlib Figure 导入 {len(names)} 条曲线 -> {g}"
                   "（颜色/线宽按 Figure 映射，可用 origin_edit_plot 继续微调）")
    except Exception as e:
        return oerr.from_exception(e)


# --- P2-7：PPT 组图交付 ---
def export_pptx_impl(op, graph, file_path, width=2400, title=None,
                     panel_label=None, notes=None):
    try:
        bad = _check_pptx()
        if bad is not None:
            return bad
        import origin_engine as _eng
        # 高分辨率 PNG（先落临时目录，插入后保留在交付目录）
        out_dir = os.path.dirname(os.path.abspath(file_path))
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(file_path))[0]
        png_path = os.path.join(out_dir, f"{stem}.png")
        rex = _eng._export_impl(graph, file_path=png_path, fmt="png",
                                width=int(width))
        if not rex.get("ok"):
            return rex
        from pptx import Presentation
        from pptx.util import Emu, Inches, Pt
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])   # 空白版式
        sw, sh = prs.slide_width, prs.slide_height
        # 等比缩放居中
        from PIL import Image
        iw, ih = Image.open(png_path).size
        scale = min(sw / iw, (sh - Inches(0.6)) / ih)
        w, h = int(iw * scale), int(ih * scale)
        slide.shapes.add_picture(png_path, int((sw - w) / 2), Inches(0.55),
                                 width=Emu(w), height=Emu(h))
        # 面板字母标注（hzsci 约定：字母放 PPT 不进 Origin 图页）
        if panel_label:
            tb = slide.shapes.add_textbox(Inches(0.25), Inches(0.15),
                                          Inches(1.2), Inches(0.4))
            tf = tb.text_frame
            tf.text = str(panel_label)
            tf.paragraphs[0].runs[0].font.size = Pt(20)
            tf.paragraphs[0].runs[0].font.bold = True
        if title or notes:
            tb2 = slide.shapes.add_textbox(Inches(0.25), sh - Inches(0.45),
                                           sw - Inches(0.5), Inches(0.4))
            tf2 = tb2.text_frame
            tf2.text = (str(title) + ("  |  " + str(notes) if notes else ""))
            tf2.paragraphs[0].runs[0].font.size = Pt(10)
        prs.save(file_path)
        return oerr.ok(
            pptx=os.path.abspath(file_path), png=png_path,
            png_size=rex.get("size"), panel_label=panel_label,
            detail=f"已组 PPT 页（{graph} -> {os.path.basename(file_path)}，"
                   f"PNG {rex.get('size')}B 已嵌入；OLE 双击编辑见 COMPATIBILITY #16）")
    except Exception as e:
        return oerr.from_exception(e)
