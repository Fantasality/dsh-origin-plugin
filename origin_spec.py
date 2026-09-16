# -*- coding: utf-8 -*-
"""origin_spec —— FigureSpec 声明式图协议（P1-1，2026-09-16）。

设计来源：deliuou/originplot-skill 的 FigureSpec + editaplot 的 render-plan
冻结思想，精简为四节声明式 YAML，与本项目 plan/确认流完全兼容：

    spec_version: 1
    data:        # 内联完整数据（可重放）或 data_ref 文件路径
      columns: {x: [...], y1: [...]}
      data_ref: 可选，CSV/XLSX 路径（导入后画）
    plot:        # kind/x_column/y_columns/yerr/graph_name/title
    style:       # style_mode/family
    export:      # fmt/file_path/width

关键性质：
- **spec 先行 -> 用户确认 -> 再执行**（复用 plan 的 questions/needs_confirmation
  与 check_stale 机制，不发明第二套确认流）；
- **可 diff 可重放**：plan_id 是内容哈希，同 spec 落盘重导入得到同一 plan_id；
- 老调用（plan_id 直通）不受影响。

依赖：pyyaml（venv 已装 6.0.3；缺失时给出明确安装提示）。
"""
from __future__ import annotations

import origin_errors as oerr

SPEC_VERSION = 1
_KINDS = ("line", "scatter", "line_symbol", "column", "histogram", "box", "bar")


def _yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        return None


def spec_from_plan(plan: dict) -> dict:
    """把 plan 缓存结构转为声明式 spec（四节）。"""
    p = plan.get("params", {}) or {}
    return {
        "spec_version": SPEC_VERSION,
        "source": "dsh-origin-plugin",
        "created_at": plan.get("created_at"),
        "plan_hash": plan.get("plan_hash"),
        "data": {"columns": (plan.get("data") or {}).get("columns", {}),
                 "data_ref": None},
        "plot": {"kind": p.get("plot_type", "line"),
                 "x_column": p.get("x_column"),
                 "y_columns": p.get("y_columns"),
                 "yerr_column": p.get("yerr_column"),
                 "graph_name": p.get("graph_name"),
                 "title": p.get("title")},
        "style": {"style_mode": p.get("style_mode", "default"),
                  "family": p.get("family")},
        "export": {"fmt": p.get("fmt", "png"),
                   "file_path": p.get("file_path"),
                   "width": 1200},
    }


def spec_validate(spec: dict):
    """结构校验。返回 None（通过）或 fail 结构。"""
    if not isinstance(spec, dict):
        return oerr.fail("invalid_request", "spec 必须是 dict/YAML 映射")
    if int(spec.get("spec_version") or 0) > SPEC_VERSION:
        return oerr.fail(
            "invalid_request",
            f"spec_version {spec.get('spec_version')} 高于本引擎支持的 {SPEC_VERSION}",
            supported=SPEC_VERSION)
    data = spec.get("data") or {}
    cols = data.get("columns")
    if not cols and not data.get("data_ref"):
        return oerr.fail("invalid_request",
                         "spec.data 需要 columns（内联数据）或 data_ref（文件路径）")
    if cols and not isinstance(cols, dict):
        return oerr.fail("invalid_request", "spec.data.columns 必须是 {列名: [..]}")
    plot = spec.get("plot") or {}
    kind = plot.get("kind", "line")
    if kind not in _KINDS:
        return oerr.fail("invalid_request", f"spec.plot.kind 必须是 {_KINDS} 之一",
                         received=kind, valid=list(_KINDS))
    return None


def spec_to_yaml(spec: dict, path: str):
    """spec 落盘为 YAML（可 diff / 可重放 / 可版本化）。"""
    y = _yaml()
    if y is None:
        return oerr.fail(
            "invalid_request",
            "缺少 pyyaml：请在引擎 Python 环境执行 pip install pyyaml")
    try:
        with open(path, "w", encoding="utf-8") as f:
            y.safe_dump(spec, f, allow_unicode=True, sort_keys=False)
        return oerr.ok(path=path, spec_version=SPEC_VERSION,
                       detail=f"FigureSpec 已落盘 -> {path}")
    except Exception as e:
        return oerr.from_exception(e)


def spec_from_yaml(path: str):
    """读取 YAML spec。"""
    y = _yaml()
    if y is None:
        return None, oerr.fail(
            "invalid_request",
            "缺少 pyyaml：请在引擎 Python 环境执行 pip install pyyaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return y.safe_load(f), None
    except Exception as e:
        return None, oerr.from_exception(e)


def spec_import(spec: dict):
    """spec -> build_plan 重建计划（进确认流缓存，返回 plan_id/questions）。

    data_ref 场景：import 返回 hint，由调用方先 origin_load_file 后用
    origin_plot_plan 走文件路径（spec_import 不做 IO，保持离线秒回）。
    """
    bad = spec_validate(spec)
    if bad is not None:
        return bad
    data = spec.get("data") or {}
    cols = data.get("columns")
    if not cols:
        return oerr.fail(
            "invalid_request",
            "spec.data 只有 data_ref：请先 origin_load_file(data_ref) 导入，"
            "再以文件返回的列重建 plan（或把 columns 内联进 spec）",
            data_ref=data.get("data_ref"))
    plot = spec.get("plot") or {}
    style = spec.get("style") or {}
    export = spec.get("export") or {}
    import origin_plan as oplan
    plan = oplan.build_plan(
        cols,
        plot_type=plot.get("kind", "line"),
        style_mode=style.get("style_mode", "default"),
        family=style.get("family"),
        x_column=plot.get("x_column"),
        y_columns=plot.get("y_columns"),
        yerr_column=plot.get("yerr_column"),
        title=plot.get("title"),
        fmt=export.get("fmt", "png"),
        graph_name=plot.get("graph_name"),
        file_path=export.get("file_path"))
    if not plan.get("ok"):
        return plan
    return {"ok": True, "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "needs_confirmation": plan["needs_confirmation"],
            "questions": plan["questions"],
            "roles": plan["roles"],
            "detail": ("FigureSpec 已导入为计划 " + plan["plan_id"]
                       + ("；有待确认问题需先经用户确认" if plan["needs_confirmation"]
                          else "；可直接 origin_execute_plan"))}
