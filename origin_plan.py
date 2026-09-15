# -*- coding: utf-8 -*-
"""
origin_plan —— 绘图计划/确认流（纯离线，不连 Origin，可离线单测）
==================================================================

把「数据 → 图」的隐式约定显式化，形成轻量确认闭环：

1. ``build_plan``（由 origin_plot_plan 暴露）输入 columns 与绘图意图，产出：
   - 逐列画像：dtype（numeric/text/mixed/empty）、缺失数、范围、单调性；
   - 角色建议：X 候选（名称语义 + 单调性评分）/ Y 序列 / 误差棒候选 / 标签列；
   - 图形元素清单：图型、排版预设、调色板（含使用约束）、轴标题建议、图例策略；
   - 待确认问题：混合列、高缺失（>20%）、多个 X 候选、无单调 X；
   - plan_id（内容哈希）并缓存于服务端（LRU，容量 32）。
2. ``get_plan``（由 origin_execute_plan 使用）仅凭 plan_id 取回计划执行，
   模型无需回传大体积数据；服务器重启/缓存淘汰后返回 plan_not_found。

设计取舍：刻意不做 EditaPlot 式的重型 hash 冻结机制 —— MCP 会话内模型有
记忆，轻确认（plan + questions + 服务端缓存）已能覆盖"不确定列先问"的核心
诉求，同时保持秒级响应。

科学边界（与 SKILL.md 约定一致，随计划返回）：
不虚构/不补数据；不确定用途的列先询问；派生列必须标注 derived；
不做未经要求的统计推断。
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict

import origin_errors as oerr
import plot_style as pst

PLAN_CACHE: "OrderedDict[str, dict]" = OrderedDict()
PLAN_CACHE_MAX = 32

_X_NAME_HINTS = ("x", "time", "temp", "t_", "two_theta", "2theta", "wavelength",
                 "energy", "wave_number", "frequency", "date", "age", "dose",
                 "concentration", "velocity")
_YERR_HINTS = ("err", "error", "sd", "std", "dev", "unc", "sigma", "_se")

SCIENCE_BOUNDARIES = [
    "不虚构/不补造数据：源数据缺失时报告缺口，等待用户决定",
    "不确定用途的列先询问，不自动画成新曲线",
    "派生列（拟合/平滑/归一化等产物）必须标注 derived 来源",
    "不做未经要求的统计推断；统计结论需用户确认后才写进图",
]


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _column_meta(name, vals):
    nums = [v for v in vals if _is_num(v)]
    texts = sum(1 for v in vals if isinstance(v, str))
    missing = sum(1 for v in vals if v is None)
    if nums and texts:
        dtype = "mixed"
    elif nums:
        dtype = "numeric"
    elif texts:
        dtype = "text"
    else:
        dtype = "empty"
    meta = {"name": name, "dtype": dtype, "n": len(vals), "missing": missing}
    if nums:
        meta["min"] = float(min(nums))
        meta["max"] = float(max(nums))
        meta["mean"] = float(sum(nums) / len(nums))
        meta["monotonic_increasing"] = all(b > a for a, b in zip(nums, nums[1:]))
        meta["monotonic_nondecreasing"] = all(b >= a for a, b in zip(nums, nums[1:]))
    return meta


def _plan_hash(columns: dict, params: dict) -> str:
    payload = json.dumps(
        {"columns": {k: [(round(v, 10) if _is_num(v) else v) for v in vals]
                     for k, vals in columns.items()},
         "params": params},
        ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _valid_plot_types():
    """与引擎的合法 plot_type 保持同步（惰性导入避免离线环境拉起 COM）。"""
    try:
        import origin_engine as _e
        return list(_e.PLOT_TYPES) + ["histogram", "box", "bar"]
    except Exception:
        return ["line", "scatter", "line_symbol", "column", "histogram", "box", "bar"]


def build_plan(columns, plot_type="line", style_mode="default", family=None,
               x_column=None, y_columns=None, yerr_column=None, title=None,
               fmt="png", graph_name=None, file_path=None):
    """构建绘图计划（不连 Origin，秒回）。失败返回统一错误结构。"""
    if not isinstance(columns, dict) or not columns:
        return oerr.fail("invalid_request",
                         "columns 必须是非空 dict {列名: [值...]}",
                         received_type=type(columns).__name__)
    clean = {}
    for k, v in columns.items():
        if not isinstance(v, (list, tuple)):
            return oerr.fail("invalid_request", f"列 {k!r} 的值必须是列表",
                             column=str(k))
        clean[str(k)] = [x for x in v]
    n = max(len(v) for v in clean.values())
    for k, v in clean.items():
        if len(v) < n:
            v.extend([None] * (n - len(v)))

    valid_types = _valid_plot_types()
    if plot_type not in valid_types:
        return oerr.fail("invalid_request", f"plot_type 必须是 {valid_types} 之一",
                         valid=valid_types, received=plot_type)
    if yerr_column is not None and str(yerr_column) not in clean:
        return oerr.fail("column_not_found", f"误差棒列不存在: {yerr_column}",
                         column=str(yerr_column))
    for c in (y_columns or []):
        if str(c) not in clean:
            return oerr.fail("column_not_found", f"Y 列不存在: {c}", column=str(c))

    metas = [_column_meta(k, v) for k, v in clean.items()]
    numeric_names = [m["name"] for m in metas if m["dtype"] in ("numeric", "mixed")]
    text_names = [m["name"] for m in metas if m["dtype"] == "text"]
    if not numeric_names:
        return oerr.fail("empty_data", "没有可绘图的数值列（全部为文本/空）",
                         columns=[m["name"] for m in metas])

    # X 候选：名称语义 + 单调性评分
    x_cands = []
    for m in metas:
        if m["dtype"] != "numeric":
            continue
        nm = m["name"].lower()
        if m.get("monotonic_increasing") or m.get("monotonic_nondecreasing"):
            score = 0
            if any(h in nm for h in _X_NAME_HINTS):
                score += 2
            if m.get("monotonic_increasing"):
                score += 1
            x_cands.append((score, m["name"]))
    x_cands.sort(reverse=True)

    if x_column:
        suggested_x = str(x_column)
    elif x_cands:
        suggested_x = x_cands[0][1]
    else:
        suggested_x = None

    yerr_cands = [nm for nm in numeric_names
                  if any(h in nm.lower() for h in _YERR_HINTS)]
    y_suggested = [nm for nm in numeric_names
                   if nm != suggested_x and nm not in yerr_cands]
    if not y_suggested and len(numeric_names) > 1:
        y_suggested = [nm for nm in numeric_names if nm != suggested_x]
    y_final = [str(c) for c in (y_columns or y_suggested)]
    if not y_final:
        return oerr.fail("empty_data", "没有可用的 Y 列",
                         numeric_columns=numeric_names)

    # 待确认问题（不确定列先问，不自动猜）
    questions = []
    for m in metas:
        if m["dtype"] == "mixed":
            questions.append({
                "type": "mixed_column", "column": m["name"],
                "message": f"列 {m['name']} 同时含数值与文本，确认用途（数值序列/标签/误差棒）"})
        if m["dtype"] in ("numeric", "mixed") and m["n"] and m["missing"] / m["n"] > 0.2:
            questions.append({
                "type": "high_missing", "column": m["name"],
                "message": f"列 {m['name']} 缺失 {m['missing']}/{m['n']}（>20%），"
                           "确认缺失原因（未测/不计入/需删点）"})
    if len(x_cands) > 1 and not x_column:
        questions.append({
            "type": "multiple_x_candidates",
            "message": "存在多个单调数值列，确认用哪列作 X",
            "candidates": [nm for _, nm in x_cands[:4]]})
    if suggested_x is None:
        questions.append({
            "type": "no_x_column",
            "message": "未找到单调数值列作 X，执行时将生成行号列作为 X，确认是否合适"})

    row_counts = [m["n"] for m in metas]
    style = pst.full_style_plan(plot_type, y_final, max(row_counts) if row_counts else 0,
                                style_mode=style_mode, family=family)
    x_title = pst.infer_axis_title([suggested_x])["title"] if suggested_x else ""
    yerr_pick = str(yerr_column) if yerr_column else (yerr_cands[0] if yerr_cands else None)
    elements = {
        "plot_type": plot_type,
        "series": y_final,
        "yerr": yerr_pick,
        "style_mode": style["style_mode"],
        "palette": style["palette"],
        "axis_titles": {"x": x_title,
                        "y": (style["axis_titles"].get("y") or {}).get("title", "")},
        "legend": "多序列显示图例" if len(y_final) > 1 else "单序列隐藏图例",
        "target_width_mm": style["preset"].get("target_width_mm"),
    }
    params = {"plot_type": plot_type, "style_mode": style_mode or "default",
              "family": family, "title": title, "fmt": fmt or "png",
              "graph_name": graph_name, "file_path": file_path}
    plan_id = _plan_hash(clean, params)
    plan = {
        "ok": True,
        "plan_id": plan_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "columns_meta": metas,
        "roles": {"x": suggested_x, "y": y_final, "yerr": yerr_pick,
                  "labels": text_names},
        "elements": elements,
        "questions": questions,
        "needs_confirmation": bool(questions),
        "science_boundaries": SCIENCE_BOUNDARIES,
        "data": {"columns": clean, "n_rows": n},
        "params": params,
    }
    PLAN_CACHE[plan_id] = plan
    while len(PLAN_CACHE) > PLAN_CACHE_MAX:
        PLAN_CACHE.popitem(last=False)
    return plan


def get_plan(plan_id):
    """按 plan_id 取回计划（LRU 触碰）；不存在返回 None。"""
    key = str(plan_id or "")
    p = PLAN_CACHE.get(key)
    if p is not None:
        PLAN_CACHE.move_to_end(key)
    return p


def cache_size():
    return len(PLAN_CACHE)
