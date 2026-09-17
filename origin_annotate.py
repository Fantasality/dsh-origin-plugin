# -*- coding: utf-8 -*-
"""批量文本标注 / 布局信息 / 谱图模拟 / 找峰（阶段 D：标注盲试治理，2026-09-17）。

背景（甲烷 NMR 案例复盘，150+ 轮 / 30 分钟）：
- AI 放两行注释用了 15 次 add_text——因为工具从不返回文本宽度与坐标映射，
  AI 只能"估算像素 → 渲染 → 看图 → 再调"，一段推理里反复试错。
- 多个 LabTalk 通道静默失败（grand()/data()/col()[LName]$ 等），每次踩坑
  2-4 轮返工——这些坑已在 COMPATIBILITY，但"文档级"不如"引擎级"门禁。

本模块提供三件事，把"盲试"变"明算"：
1. layout_info：一次返回坐标映射（数据↔像素）、轴范围、页尺寸、现有文本对象清单
2. annotate：一次批量加 N 个文本（统一样式、可选左对齐），返回逐项落位结果
3. simulate / find_peaks：纯 numpy 的谱图模拟与找峰（模拟数据强制带 simulated 标记）

所有对 Origin 的写操作走专用 COM 线程（engine 转发），模块内只复用裸 impl。
"""
import math

import origin_errors as oerr


# ---------------------------------------------------------------------------
# 1) 布局信息：把"AI 心算像素"变成"引擎直说"
# ---------------------------------------------------------------------------
def layout_info_impl(op, po, graph, width_px=1100):
    """返回画一张图布局所需的全部几何信息。

    - 轴范围（x.from/x.to/y.from/y.to，COM 作用域通道，可靠）
    - 数据坐标 → 导出像素 的线性映射（含反向轴；导出宽度假定 width_px）
    - 页面尺寸（cm 与 dots，page.width/height 单位 600dpi dots）
    - 现有文本对象清单（Text1..TextN：text/x/y/fsize，逐个读，读不到即停）
    - 图例位置（left/top，dots）

    AI 拿到 mapping 后可精确算落位，不再需要"渲染-看图-再调"循环。
    """
    import origin_edit as oedit

    short, err = oedit.ensure_active_graph(op, po, graph)
    if err:
        return err
    gp, _ = oedit._safe(op.find_graph, short)
    if gp is None:
        return oerr.fail("graph_not_found", f"找不到图页 {graph!r}", graph=short)
    gl, _ = oedit._safe(gp.__getitem__, 0)
    if gl is None:
        return oerr.fail("graph_not_found", f"图页 {short!r} 无图层", graph=short)

    lt_float = oedit.lt_float
    lt_str = oedit.lt_str if hasattr(oedit, "lt_str") else None

    axis = {}
    for prop in ("x.from", "x.to", "y.from", "y.to",
                 "x.type", "y.type"):
        try:
            v = gl.get_float(prop) if prop.endswith(("from", "to")) \
                else gl.get_int(prop)
        except Exception:
            v = None
        axis[prop] = v
    x_from, x_to = axis.get("x.from"), axis.get("x.to")
    y_from, y_to = axis.get("y.from"), axis.get("y.to")
    if x_from is None or x_to is None or y_from is None or y_to is None:
        return oerr.fail("origin_operation_error",
                         "轴范围读取失败（x.from/y.from 等不可用）", axis=axis,
                         graph=short)

    page = {}
    for prop in ("page.width", "page.height"):
        page[prop] = oedit.lt_float(po, prop)
    pw_dots = page.get("page.width")
    ph_dots = page.get("page.height")
    if not pw_dots or not ph_dots:
        return oerr.fail("origin_operation_error",
                         "page.width/height 读取失败", page=page, graph=short)
    page_cm = {"width_cm": round(pw_dots / 600 * 2.54, 3),
               "height_cm": round(ph_dots / 600 * 2.54, 3)}

    # 像素映射：x_px = width_px * (x - x_from) / (x_to - x_from)
    #          y_px = height_px * (y_to - y) / (y_to - y_from)
    # 反向轴（如 NMR 的 δ 反转、FTIR 的波数反转）自动成立：x_to < x_from 时
    # 分母为负，映射方向随之翻转，AI 不需要自己判断。
    h_px = int(width_px * ph_dots / pw_dots)
    x_formula = "x_px = width_px * (x - {xf}) / ({xt} - {xf})".format(
        xf=x_from, xt=x_to)
    y_formula = "y_px = {h} * ({yt} - y) / ({yt} - {yf})".format(
        h=h_px, yt=y_to, yf=y_from)
    mapping = {
        "width_px": int(width_px),
        "height_px": h_px,
        "x_data_to_px": x_formula,
        "y_data_to_px": y_formula,
        "reversed_x": bool(x_to < x_from),
    }

    # 现有文本对象：Text1..TextN 逐个读，第一个读不到文本的就停（上限 60）。
    # 统一走 oedit.lt_str/lt_float（内部 LT_execute 赋值再读，项目验证过的通道）。
    texts = []
    for i in range(1, 61):
        name = f"Text{i}"
        txt = oedit.lt_str(po, f"{name}.text$")
        if not txt:
            break
        texts.append({"name": name, "text": txt,
                      "x": oedit.lt_float(po, f"{name}.x"),
                      "y": oedit.lt_float(po, f"{name}.y"),
                      "fsize": oedit.lt_float(po, f"{name}.fsize")})

    legend = {}
    for prop in ("legend.left", "legend.top"):
        try:
            legend[prop] = lt_float(po, prop)
        except Exception:
            legend[prop] = None

    return oerr.ok(
        graph=short, page_cm=page_cm, page_dots={"width": pw_dots,
                                                 "height": ph_dots},
        axis={"x": {"from": x_from, "to": x_to},
              "y": {"from": y_from, "to": y_to}},
        mapping=mapping, texts=texts, legend=legend,
        note=("x_px/y_px 公式已按当前轴范围实例化；反向轴（reversed_x=true）映射"
              "方向已自动翻转。texts 为图内现有文本对象（TextN 命名，中心锚点）。"))


# ---------------------------------------------------------------------------
# 2) 批量标注：一次调用加 N 个文本（消灭 add_text 逐个试错）
# ---------------------------------------------------------------------------
def annotate_impl(op, po, graph, items, style=None):
    """批量添加文本标注。

    Args:
        items: [{"text": str, "x": float, "y": float,
                 "size"?: float(默认 7), "color"?: int(Origin 色号,默认黑=1),
                 "bold"?: bool}]，x/y 为轴数据坐标（中心锚点）。
        style: 全局默认 {"size"?: 7, "just_left"?: bool}；just_left 尝试把
               text.just 设为 0（左对齐），不支持时在返回里标注 just_applied=False。
    Returns:
        oerr.ok + results（逐项：text/object_name/applied/just_applied）。
        object_name 是创建后的 LabTalk 对象名（Text1..TextN 顺序分配），
        AI 可用 origin_labtalk 对其做后续微调（如 Text3.x = -1.9）。

    设计说明：逐个 add_label 后**立即**用通用对象 `text` 设置属性——
    LabTalk 的 `text` 恒指向最近创建的标注（ori-test 案例验证的通道），
    批处理时这个"先建后设"的顺序是安全的。
    """
    import origin_edit as oedit

    if not items or not isinstance(items, (list, tuple)):
        return oerr.fail("invalid_request", "items 必须是非空数组")
    style = style or {}
    default_size = float(style.get("size", 7.0))
    just_left = bool(style.get("just_left", False))

    short, err = oedit.ensure_active_graph(op, po, graph)
    if err:
        return err
    gp, _ = oedit._safe(op.find_graph, short)
    gl, _ = oedit._safe(gp.__getitem__, 0)

    results = []
    for idx, item in enumerate(items):
        text = str(item.get("text", "")).strip()
        x, y = item.get("x"), item.get("y")
        if not text or x is None or y is None:
            results.append({"index": idx, "applied": False,
                            "detail": "缺 text/x/y"})
            continue
        size = float(item.get("size", default_size) or default_size)
        color = item.get("color", 1)
        bold = bool(item.get("bold", False))

        lb, e = oedit._safe(lambda: gl.add_label(text, float(x), float(y)))
        if lb is None:
            e2 = oedit.lt_exec(po, f'label -p {float(x)} {float(y)} "{text}";')
            if e2 is not None:
                results.append({"index": idx, "text": text, "applied": False,
                                "detail": f"add_label 与 LabTalk 均失败: {e or e2}"})
                continue

        applied, just_applied = [], []
        # 逐属性设置并读回（统一走 oedit.lt_float/lt_exec：LT_execute 赋值 + 读回，
        # `text` 通用对象恒指向最近创建的标注）
        for prop, val in (("fsize", size), ("color", color),
                          ("bold", 1 if bold else 0)):
            back = oedit.lt_float(po, f"text.{prop}")
            oedit.lt_exec(po, f"text.{prop} = {val};")
            back2 = oedit.lt_float(po, f"text.{prop}")
            status = "unverified"
            if back2 is not None:
                status = ("applied" if abs(float(back2) - float(val)) < 1e-6
                          else "readback_only")
            applied.append({"prop": prop, "set": val, "readback": back2,
                            "status": status})
        if just_left:
            oedit.lt_exec(po, "text.just = 0;")
            back = oedit.lt_float(po, "text.just")
            just_applied = back is not None
        obj_name = oedit.lt_str(po, "text.name$")

        results.append({"index": idx, "text": text, "x": float(x), "y": float(y),
                        "size": size, "applied": True,
                        "object_name": obj_name,
                        "props": applied,
                        "just_applied": (just_applied if just_left else None)})

    n_ok = sum(1 for r in results if r.get("applied"))
    lvl = "readback_only"           # 标注无独立读回通道，保守定级
    if all(r.get("applied") for r in results):
        lvl = "readback_only"
    return oerr.ok(graph=short, n_items=len(items), n_applied=n_ok,
                   results=results, proof_level=lvl,
                   note=("对象名 TextN 可用 origin_labtalk 后续微调"
                         "（如 Text3.x = -1.9）；位置为中心锚点（数据坐标）。"))


# ---------------------------------------------------------------------------
# 3) 谱图模拟与找峰（纯 numpy，不连 Origin）
# ---------------------------------------------------------------------------
def _lorentzian(x, c, w, h):
    return h / (1.0 + ((x - c) / (w / 2.0)) ** 2)


def _gaussian(x, c, w, h):
    return h * math.exp(-4.0 * math.log(2.0) * ((x - c) / w) ** 2)


def simulate_impl(kind="lorentzian", centers=None, widths=None, heights=None,
                  n_points=2000, x_range=None, noise=0.01, seed=None,
                  x_label="x", y_label="y_simulated"):
    """物理模型谱图模拟（多峰 + 噪声）。

    **这不是虚构实验数据**：它是显式的物理模型前向计算（Lorentzian/Gaussian/
    pseudo-Voigt 线型），返回值强制带 simulated=True 标记，用于方法演示、
    教学与拟合验证。报告与图注中必须注明"模拟数据"。

    Args:
        kind: lorentzian | gaussian | pseudovoigt（0.5/0.5 混合）
        centers/widths/heights: 峰列表（等长数组；width=FWHM）
        n_points: 采样点数（默认 2000）
        x_range: [x_min, x_max]；缺省自动取 centers ± 5×最大 FWHM
        noise: 噪声幅度（相对最大峰高的比例，默认 0.01）
        seed: 随机种子（可复现）
    Returns:
        oerr.ok + columns（{"x": [...], "y": [...]}，可直接喂 origin_figure
        的 columns 参数）+ simulated=True + model 描述。
    """
    import random
    try:
        import numpy as np
    except Exception as e:
        return oerr.fail("origin_operation_error", f"numpy 不可用: {e}")

    if not centers or not widths or not heights \
            or not (len(centers) == len(widths) == len(heights)):
        return oerr.fail("invalid_request",
                         "centers/widths/heights 必须是等长的数组")
    kind = (kind or "lorentzian").lower()
    if kind not in ("lorentzian", "gaussian", "pseudovoigt"):
        return oerr.fail("invalid_request",
                         f"kind 仅支持 lorentzian/gaussian/pseudovoigt，收到 {kind!r}")

    xs = [float(c) for c in centers]
    ws = [float(w) for w in widths]
    hs = [float(h) for h in heights]
    lo = float(x_range[0]) if x_range else min(xs) - 5 * max(ws)
    hi = float(x_range[1]) if x_range else max(xs) + 5 * max(ws)
    n = max(50, int(n_points))
    step = (hi - lo) / (n - 1)
    x = [lo + step * i for i in range(n)]

    rng = random.Random(seed)
    np.random.seed(seed if seed is not None else None)

    def _peak(xv, c, w, h):
        if kind == "lorentzian":
            return _lorentzian(xv, c, w, h)
        if kind == "gaussian":
            return _gaussian(xv, c, w, h)
        return 0.5 * _lorentzian(xv, c, w, h) + 0.5 * _gaussian(xv, c, w, h)

    y = []
    max_h = max(hs) if hs else 1.0
    for xv in x:
        v = sum(_peak(xv, c, w, h) for c, w, h in zip(xs, ws, hs))
        noise_v = 0.0
        if noise:
            noise_v = rng.gauss(0.0, noise * max_h)
        y.append(round(v + noise_v, 8))

    return oerr.ok(
        columns={x_label: [round(v, 8) for v in x], y_label: y},
        simulated=True,
        model={"kind": kind, "centers": xs, "fwhm": ws, "heights": hs,
               "noise_rel": noise, "seed": seed, "n_points": n},
        note=("模拟数据（物理模型前向计算）：图注与报告必须注明 simulated，"
              "不得作为实验数据呈现。"))


def find_peaks_impl(x_list, y_list, top_n=5, min_height_frac=0.05,
                    label_template="{x:.2f}", x_prefix=""):
    """找局部极大峰（纯 numpy，简单可靠），供标峰标注使用。

    Args:
        x_list/y_list: 数据列（等长）
        top_n: 最多返回几个峰（按高度排序）
        min_height_frac: 峰高阈值（相对最大值的比例）
        label_template: 标注文本模板，{x} 为峰位（如 NMR 用 "δ {x:.2f} ppm"）
        x_prefix: 标注前缀（如 "δ "）
    Returns:
        oerr.ok + peaks（[{x, y, label}]，按 x 升序）——可直接转 origin_annotate
        的 items（y 抬升 5% 作标注位）。
    """
    try:
        import numpy as np
    except Exception as e:
        return oerr.fail("origin_operation_error", f"numpy 不可用: {e}")

    if not x_list or not y_list or len(x_list) != len(y_list):
        return oerr.fail("invalid_request", "x/y 列必须等长且非空")
    arr_x = [float(v) for v in x_list]
    arr_y = [float(v) for v in y_list]
    a = np.asarray(arr_y)
    max_h = float(a.max())
    if max_h <= 0:
        return oerr.fail("invalid_request", "数据最大值非正，无法找峰")
    thr = max_h * float(min_height_frac)

    peaks = []
    for i in range(1, len(a) - 1):
        if a[i] >= a[i - 1] and a[i] >= a[i + 1] and a[i] >= thr:
            peaks.append((arr_x[i], float(a[i])))
    peaks.sort(key=lambda t: -t[1])
    peaks = peaks[:max(1, int(top_n))]
    peaks.sort(key=lambda t: t[0])

    out = []
    for px, py in peaks:
        out.append({"x": px, "y": py,
                    "label": (x_prefix + label_template.format(x=px)).strip()})

    return oerr.ok(peaks=out, n=len(out),
                   note=("y 阈值 = 最大值 × min_height_frac；返回按 x 升序。"
                         "转标注时建议 y 抬升 5%（相对量程）放在峰顶上方。"))
