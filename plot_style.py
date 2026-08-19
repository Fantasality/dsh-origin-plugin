# -*- coding: utf-8 -*-
"""
plot_style —— 默认排版规则 + 无障碍调色板 + 语义轴标题（clean-room 设计）
======================================================================

本模块只负责"设计"，不碰 Origin：给定图型/数据特征，产出一套可解释的
样式建议（readability plan、调色板、轴标题、多序列区分），由 origin_engine
应用成 Origin 属性。核心目标：让模型默认产出的图"自动接近排版规范"，
同时每一步都给出 reason 方便排查。

包含：
1. 颜色科学：sRGB→XYZ→OKLab、感知色差、WCAG 对比度、色盲(CVD)模拟与
   无障碍评分（均为公开科学公式的独立实现）；
2. 内置调色板库 + 按序列数自动选择；
3. 字段语义 -> 轴标题推断（temperature_C -> Temperature (°C)）；
4. readability 默认规则（图例/刻度旋转/科学计数/零基线/密集散点降透明度）；
5. 输出 style_mode 预设（default / journal / presentation）与多序列区分策略。
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# 1. 颜色科学（标准公式的独立实现）
# ---------------------------------------------------------------------------
def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _srgb_to_xyz(rgb: Sequence[float]) -> List[float]:
    r, g, b = (_srgb_to_linear(x) for x in rgb)
    return [
        0.4124 * r + 0.3576 * g + 0.1805 * b,
        0.2126 * r + 0.7152 * g + 0.0722 * b,
        0.0193 * r + 0.1192 * g + 0.9505 * b,
    ]


def _xyz_to_oklab(xyz: Sequence[float]) -> List[float]:
    x, y, z = xyz
    l_ = (0.4122214708 * x + 0.5363325363 * y + 0.0514459929 * z) ** (1 / 3)
    m = (0.2119034982 * x + 0.6806995451 * y + 0.1073969566 * z) ** (1 / 3)
    s = (0.0883024619 * x + 0.2817188376 * y + 0.6299787005 * z) ** (1 / 3)
    return [0.2104542553 * l_ + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l_ - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l_ + 0.7827717662 * m - 0.8086757660 * s]


def hex_to_rgb(hexstr: str) -> List[int]:
    h = hexstr.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"bad hex {hexstr!r}")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def _luminance(rgb: Sequence[float]) -> float:
    r, g, b = (_srgb_to_linear(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_white(rgb: Sequence[float]) -> float:
    """与白底的 WCAG 对比度。"""
    lum = _luminance(rgb)
    return (1.0 + 0.05) / (lum + 0.05)


def oklab(rgb: Sequence[float]) -> List[float]:
    return _xyz_to_oklab(_srgb_to_xyz(rgb))


def oklab_distance(c1: Sequence[float], c2: Sequence[float]) -> float:
    a, b = oklab(c1), oklab(c2)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# 色盲模拟（Vienot-Brettel-Mollon 标准矩阵；科学常数）
_CVD_MATRICES = {
    "protanopia": [[0.567, 0.433, 0.0], [0.558, 0.442, 0.0], [0.0, 0.242, 0.758]],
    "deuteranopia": [[0.625, 0.375, 0.0], [0.7, 0.3, 0.0], [0.0, 0.3, 0.7]],
    "tritanopia": [[0.95, 0.05, 0.0], [0.0, 0.433, 0.567], [0.0, 0.475, 0.525]],
}


def _cvd_sim(rgb: Sequence[float], kind: str) -> List[float]:
    xyz = _srgb_to_xyz(rgb)
    m = _CVD_MATRICES[kind]
    return [m[i][0] * xyz[0] + m[i][1] * xyz[1] + m[i][2] * xyz[2] for i in range(3)]


# ---------------------------------------------------------------------------
# 2. 内置调色板库（自建，注重 CVD 区分；颜色本身不受版权保护）
# ---------------------------------------------------------------------------
PALETTES: Dict[str, Dict] = {
    "ocean": {
        "colors": ["#0072B2", "#D55E00", "#009E73", "#56B4E9", "#CC79A7", "#E69F00"],
        "note": "CVD-safe 设计（蓝/橙/绿/天蓝/品红/黄；前两色高对比）",
    },
    "nightfall": {
        "colors": ["#001F5B", "#D1495B", "#EDAE49", "#58A4B0", "#8FB339", "#8E44AD"],
        "note": "深蓝基调，冷热对比鲜明",
    },
    "duo_warm": {
        "colors": ["#B2182B", "#EF8A62", "#FDDBC7", "#67A9CF", "#2166AC", "#F4A582"],
        "note": "冷暖双极（适合温度/极性数据）",
    },
    "forest": {
        "colors": ["#1B9E77", "#D95F02", "#7570B3", "#E7298A", "#66A61E", "#E6AB02"],
        "note": "调色板 2 风格（高区分、色感良好）",
    },
    "grey_tone": {
        "colors": ["#404040", "#808080", "#C8C8C8", "#2E4057", "#7D8CA3", "#A9B7C6"],
        "note": "低彩度，适合灰度打印场景",
    },
}
DEFAULT_PALETTE = "ocean"


def palette_metrics(name: str) -> Dict:
    """计算一组颜色的无障碍指标（白底对比度 / 感知色差 / CVD 区分度）。"""
    hexes = PALETTES[name]["colors"]
    rgbs = [hex_to_rgb(h) for h in hexes]
    contrasts = [contrast_white(r) for r in rgbs]
    pair_dists = [oklab_distance(a, b) for i, a in enumerate(rgbs) for b in rgbs[i + 1:]]
    cvd_min = {}
    for kind in _CVD_MATRICES:
        sims = [_cvd_sim(r, kind) for r in rgbs]
        cvd_min[kind] = min(
            (math.sqrt(sum((x - y) ** 2 for x, y in zip(_cvd_sim(r1, kind), _cvd_sim(r2, kind))))
             for i, r1 in enumerate(rgbs) for r2 in rgbs[i + 1:])
            if len(rgbs) > 1 else 0.0)
    return {
        "name": name,
        "note": PALETTES[name]["note"],
        "colors": hexes,
        "min_contrast_white": round(min(contrasts), 2),
        "min_oklab_distance": round(min(pair_dists), 3) if pair_dists else 0.0,
        "min_cvd_distance": {k: round(v, 3) for k, v in cvd_min.items()},
    }


_CACHED_METRICS: Dict[str, Dict] = {}


def get_palette_metrics(name: str) -> Dict:
    if name not in _CACHED_METRICS:
        _CACHED_METRICS[name] = palette_metrics(name)
    return _CACHED_METRICS[name]


def choose_palette(series_count: int, family: Optional[str] = None) -> Dict:
    """按序列数挑选区分度最佳且通过对比度门槛的调色板。

    返回: {name, colors, reason, metrics}。
    """
    candidates = list(PALETTES.keys())
    if family == "low_saturation":
        candidates = ["grey_tone"]
    elif family == "paired":
        candidates = ["forest", "duo_warm"]
    usable = [n for n in candidates if len(PALETTES[n]["colors"]) >= series_count]
    pool = usable or candidates
    # 评分：优先最大感知色差，其次白底对比度
    def score(n):
        m = get_palette_metrics(n)
        return (m["min_oklab_distance"], m["min_contrast_white"])

    best = max(pool, key=score)
    m = get_palette_metrics(best)
    return {
        "name": best,
        "colors": PALETTES[best]["colors"][:series_count],
        "reason": (
            f"选 {best}（{len(PALETTES[best]['colors'])} 色）："
            f"感知色差 {m['min_oklab_distance']}，白底对比度 {m['min_contrast_white']}"),
        "metrics": m,
    }


# ---------------------------------------------------------------------------
# 3. 字段语义 -> 轴标题（独立规则表 + 启发式回退）
# ---------------------------------------------------------------------------
_UNIT_MAP = [
    ("degree_c", "°C"), ("deg_c", "°C"), ("degc", "°C"), ("celsius", "°C"), ("_c$", "°C"),
    ("_k$", "K"), ("_kelvin", "K"),
    ("_s$", "s"), ("_sec", "s"), ("_second", "s"), ("_ms", "ms"), ("_us", "µs"), ("_ns", "ns"),
    ("_min", "min"), ("_hr", "h"), ("_h$", "h"), ("_day", "day"),
    ("_mm", "mm"), ("_um", "µm"), ("_nm", "nm"), ("_cm", "cm"), ("_km", "km"), ("_m$", "m") if False else ("_meter", "m"), ("_m$", "m"),
    ("_kg", "kg"), ("_g$", "g"), ("_mg", "mg"), ("_ug", "µg"), ("_ng", "ng"),
    ("_l$", "L"), ("_ml", "mL"), ("_ul", "µL"),
    ("_mol_l", "mol/L"), ("_mmol_l", "mmol/L"), ("_umol_l", "µmol/L"), ("_mg_dl", "mg/dL"),
    ("_nm_l", "nmol/L"), ("_m_s", "m/s"), ("_mm_s", "mm/s"),
    ("_v$", "V"), ("_mv", "mV"), ("_a$", "A"), ("_ma$", "mA"),
    ("_hz", "Hz"), ("_khz", "kHz"), ("_mhz", "MHz"), ("_rpm", "rpm"),
    ("_w$", "W"), ("_mw", "mW"), ("_kpa", "kPa"), ("_pa$", "Pa"), ("_mpa", "MPa"),
    ("_j$", "J"), ("_n$", "N"), ("_m_j", "mJ"),
    ("_pct", "%"), ("_percent", "%"),
]

_SPECIAL_TITLE = {
    "temperature": ("Temperature", "°C"),
    "pressure": ("Pressure", "kPa"),
    "time": ("Time", "s"),
    "duration": ("Duration", "ms"),
    "voltage": ("Voltage", "V"),
    "current": ("Current", "A"),
    "frequency": ("Frequency", "Hz"),
    "frequency_spectrum": ("Frequency", "Hz"),
    "dose": ("Dose", "µM"),
    "concentration": ("Concentration", "µM"),
    "wavelength": ("Wavelength", "nm"),
    "absorbance": ("Absorbance", "a.u."),
    "intensity": ("Intensity", "a.u."),
    "signal": ("Signal", "a.u."),
    "response": ("Response", "a.u."),
    "count": ("Counts", ""),
    "probability": ("Probability", ""),
    "velocity": ("Velocity", "m/s"),
    "acceleration": ("Acceleration", "m/s²"),
}

_TRAILING_NOISE = ("_mean", "_avg", "_average", "_std", "_sd", "_se", "_raw", "_norm")


def _strip_trailing_noise(name: str) -> str:
    name = name.lower()
    for suf in sorted(_TRAILING_NOISE, key=len, reverse=True):
        if name.endswith(suf):
            name = name[: -len(suf)].rstrip("_")
    return name


def infer_axis_title(column_names: Sequence[str]) -> Dict:
    """从列名集合推断语义轴标题。

    返回: {title, unit, base, used_names, reason}。同名含义列只取一个作代表。
    """
    names = [str(c) for c in column_names if str(c)]
    if not names:
        return {"title": "", "unit": "", "base": "", "reason": "无列名"}

    # 1) 语义主干（去掉单位/噪音后缀后最长的共同词）
    cleaned = [_strip_trailing_noise(n) for n in names]
    base_candidates = []
    for c in cleaned:
        base = re.sub(r"_[^_]*$", "", c).replace("_", " ").strip() or c.replace("_", " ").strip()
        base_candidates.append(base)
    # 取出现次数最多且最短(避免过长拼接)的主干
    from collections import Counter
    counter = Counter(b for b in base_candidates if b)
    base = (counter or {"": 0}).most_common(1)[0][0] if counter else ""

    # 2) 单位：优先出现在任一列名里的映射后缀
    unit = ""
    for name in names:
        n = name.lower()
        for token, u in _UNIT_MAP:
            if n.endswith(token) and not u.startswith("_"):
                unit = u
                break
        if unit:
            break
    # 3) 特例语义（temperature_C 等）
    base_key = re.sub(r"[^a-z_]", "", _strip_trailing_noise(names[0]).replace(" ", "_"))
    for key, (title, def_unit) in _SPECIAL_TITLE.items():
        if base_key.startswith(key):
            title_out = title
            unit_out = unit or def_unit
            return {"title": f"{title} ({unit_out})" if unit_out else title,
                    "unit": unit_out, "base": title,
                    "used_names": names,
                    "reason": f"列名语义 {names[0]!r} 匹配特例 {key}"}

    # 4) 通用：主干 + 单位
    unit_part = f" ({unit})" if unit else ""
    title_out = (base or names[0].replace("_", " ")).strip().capitalize()
    # 若所有列同主干，直接用它
    if len({b for b in base_candidates if b}) == 1 and base:
        title_out = base.replace("_", " ").strip().capitalize()
    return {"title": f"{title_out}{unit_part}", "unit": unit, "base": base or title_out,
            "used_names": names, "reason": f"启发式：主干={base!r}, 单位={unit!r}"}


# ---------------------------------------------------------------------------
# 4. readability 默认规则（给出带原因的建议）
# ---------------------------------------------------------------------------
def readability_plan(plot_type: str, series_count: int, row_count: int,
                     category_count: Optional[int] = None,
                     min_magnitude: Optional[float] = None,
                     max_magnitude: Optional[float] = None) -> Dict:
    """根据数据特征产出样式建议（永远返回原因，可解释）。"""
    plan: Dict = {
        "plot_type": plot_type, "series_count": series_count, "row_count": row_count,
        "tweaks": {}, "reasons": {},
    }
    tweaks = plan["tweaks"]
    reasons = plan["reasons"]

    # 图例：单序列隐藏
    tweaks["show_legend"] = series_count > 1
    reasons["show_legend"] = "单序列自动隐藏图例" if series_count <= 1 else "多序列保留图例"

    # 分类轴拥挤 -> 旋转刻度
    if category_count is not None and category_count > 8:
        tweaks["rotate_category_ticks"] = 45
        reasons["rotate_category_ticks"] = f"{category_count} 个分类标签拥挤，旋转 45°"

    # 科学计数法：极端量级
    if min_magnitude is not None and max_magnitude is not None:
        use_sci = abs(min_magnitude) < 1e-3 or abs(max_magnitude) > 1e5
        tweaks["use_scientific_notation"] = use_sci
        reasons["use_scientific_notation"] = (
            f"数据量级 [{min_magnitude:.3g}, {max_magnitude:.3g}] "
            + ("启用科学计数法" if use_sci else "保持常规小数"))

    # 非负柱/条形/面积：零基线
    if plot_type in ("bar", "column", "stack_bar", "column_stack", "area", "stack_area", "histogram"):
        tweaks["zero_baseline"] = True
        reasons["zero_baseline"] = "柱/条形/面积图自动强制零基线"

    # 密集散点：减小符号 + 增透明，避免糊成一团
    if plot_type in ("scatter", "line_symbol", "line") and row_count and row_count > 500:
        tweaks["marker_downscale"] = True
        tweaks["marker_transparency"] = 35
        reasons["marker_downscale"] = f"{row_count} 点密集，减小符号并加 35% 透明"

    # 网格：主力水平网格，隐藏垂直/次要网格
    tweaks["grid"] = "light_horizontal_major"
    reasons["grid"] = "Cartesian 图开浅色水平主网格便于读数，隐藏垂直/次要网格"

    return plan


# ---------------------------------------------------------------------------
# 5. style_mode 预设 + 多序列区分（Origin 兼容数值）
# ---------------------------------------------------------------------------
def style_mode_presets(style_mode: str = "default") -> Dict:
    """输出风格预设：字号/线宽/刻度/几何。取值是自己的设计（标准期刊惯例）。"""
    mode = (style_mode or "default").lower()
    if mode == "journal":
        return {
            "label": "journal", "min_font_pt": 8.0, "line_width": 1.5,
            "tick_length": 6.0, "target_width_mm": 89, "target_width_double_mm": 183,
            "note": "单栏 89mm / 双栏 183mm，适合投稿尺寸",
        }
    if mode == "presentation":
        return {
            "label": "presentation", "min_font_pt": 16.0, "line_width": 2.5,
            "tick_length": 5.0, "target_width_mm": 254,
            "note": "投影/演示：更大字号更粗线条",
        }
    return {
        "label": "default", "min_font_pt": 11.0, "line_width": 1.2,
        "tick_length": 4.0, "target_width_mm": 160,
        "note": "常规交互默认",
    }


_LINE_STYLES = [1, 2, 3, 4, 5, 6]       # 1=实线 2=虚线 3=点线 4=点划线 ...（Origin 数值）
_SYMBOL_SHAPES = [2, 3, 5, 17, 6, 7, 8, 9]  # 圆/方/上三角/菱形/下三角/左三角/右三角/叉（Origin 数值）


def series_distinction(plot_type: str, series_count: int) -> Dict:
    """按图型给多序列分配线型/符号循环（数值为 Origin 属性取值）。"""
    if plot_type in ("line", "area", "stack_area", "histogram"):
        lines = [(_LINE_STYLES[i % len(_LINE_STYLES)], 1) for i in range(series_count)]
        return {"kind": "line_style_cycle", "assignments": lines,
                "reason": "线图：循环线型区分序列（含色盲可读）"}
    if plot_type in ("scatter", "line_symbol", "bubble"):
        shapes = [_SYMBOL_SHAPES[i % len(_SYMBOL_SHAPES)] for i in range(series_count)]
        return {"kind": "symbol_shape_cycle", "assignments": shapes,
                "reason": "散点图：循环符号形状区分序列（含色盲可读）"}
    return {"kind": "color_only", "assignments": None,
            "reason": "柱/条形等不强调符号，以颜色区分（必要时补充线型）"}


# ---------------------------------------------------------------------------
# 便于调试：一次算出组合建议
# ---------------------------------------------------------------------------
def full_style_plan(plot_type: str, columns: Sequence[str], row_count: int,
                    category_count: Optional[int] = None,
                    min_magnitude: Optional[float] = None,
                    max_magnitude: Optional[float] = None,
                    style_mode: str = "default", family: Optional[str] = None) -> Dict:
    """style_mode + 调色板 + 轴标题 + readability 组合建议（供画图工具用）。"""
    import numpy as np   # 仅此处需要
    n_series = len(columns)
    palette = choose_palette(max(1, n_series or 1), family=family)
    axis = infer_axis_title(columns)
    plan = readability_plan(plot_type, n_series, row_count, category_count,
                            min_magnitude, max_magnitude)
    preset = style_mode_presets(style_mode)
    return {
        "style_mode": preset["label"],
        "palette": palette,
        "axis_titles": {"x": None, "y": axis},
        "readability": plan,
        "series_distinction": series_distinction(plot_type, n_series),
        "preset": preset,
        "applied": [],
    }
