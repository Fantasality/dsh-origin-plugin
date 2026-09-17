# -*- coding: utf-8 -*-
"""origin_e2e —— 端到端"一张图"工具（P3，2026-09-16）。

为什么存在：性能分析显示，用户感知的"慢"80% 来自 AI 多次工具调用的决策轮次
（画一张图要 6~10 次调用）。本模块把"导入/写数 → 画图 →（可选）套样式 →
（可选）verify → 导出 →（可选）一键交付"收敛为**一次调用**。

设计原则（与 origin_matrix.py 等"纯 impl 函数 + engine 转发"模块一致）：
- 每个函数第一个参数是 op（originpro 模块对象），内部所有重活都调 origin_engine
  的裸 *_impl（如 _plot_impl / _export_impl / _verify_graph_impl），不在本模块内
  重复任何 Origin COM 细节；
- **硬约束**：本函数若被 @_synchronized 的公开函数包一层后在 COM 线程内执行，
  绝不能再调 @_synchronized 的公开函数（会二次投递队列 → 105s 看门狗超时死锁），
  因此一律调裸 impl；
- 性能：尽量减少 Origin 往返——画图自带套样式（_plot_impl 内部已调
  _apply_style_impl），verify 全程只跑一次，deliver 复用已有图不重画；
- 所有返回值统一走 origin_errors 的 oerr.ok / oerr.fail 结构。
"""
from __future__ import annotations

import time as _time
import uuid as _uuid

import origin_errors as oerr

# 总校验级别枚举（把各步的 applied/verified 汇总成一个总级别）
PROOF_VERIFIED = "verified"        # 已 verify 且全部检查通过
PROOF_READBACK = "readback_only"   # 已读回但没完全确认（有 warn/unreadable 或 verify 自身报错）
PROOF_UNVERIFIED = "unverified"    # 未做 verify


# ---------------------------------------------------------------------------
# 内部小工具
# ---------------------------------------------------------------------------
def _now_ms():
    return int(round(_time.perf_counter() * 1000))


def _resolve_op(op):
    """拿到可靠的 op（COM 线程内有效的 originpro 句柄）。

    约定：调用方应在专用 COM 线程里跑本模块函数（见 smoke/e2e_verify.py 的
    run_e2e）。若传入的 op 为空，则从 engine._origin_app 取——它正是 COM 线程
    连接后设置好的权威句柄。绝不直接用主线程随便 import 的 originpro。
    """
    import origin_engine as _eng
    if op is not None:
        return op
    ok, _ = _eng._connect_impl()
    if not ok:
        return None
    return _eng._origin_app


def _style_mode_for_intent(intent, style_mode):
    """intent 隐含排版风格：journal/presentation 自动带对应 style_mode（显式传入优先）。"""
    intent = (intent or "auto").lower()
    if intent == "journal" and (style_mode or "default") == "default":
        return "journal"
    if intent == "presentation" and (style_mode or "default") == "default":
        return "presentation"
    return style_mode or "default"


def _width_for_intent(intent, width):
    """intent 隐含目标媒介尺寸（像素）；auto 用调用方给的 width。"""
    intent = (intent or "auto").lower()
    if intent == "quick":
        return 800                       # 低分辨率快速预览
    if intent == "journal":
        return 900                       # 期刊单栏（~3.5inch @ 300dpi）
    if intent == "presentation":
        return 1920                      # 演示大屏
    return width                         # auto：尊重调用方参数（默认 1200）


def _min_font_for_style(style_mode):
    """verify 时校验轴标题字号下限：与 plot_style 的 preset 对齐。"""
    sm = (style_mode or "default").lower()
    if sm == "journal":
        return 8.0
    if sm == "presentation":
        return 16.0
    return None


# ---------------------------------------------------------------------------
# 1) 端到端一张图
# ---------------------------------------------------------------------------
def figure_impl(op, columns=None, data_source=None, intent="auto", plot_type=None,
                x_column=None, y_columns=None, style_mode="default", family=None,
                fmt="png", file_path=None, output_dir=None, width=1200, graph_name=None,
                title=None, verify=True, deliver=False, source_path=None):
    """一次调用画出一张图：导入/写数 → 画图 →（可选）套样式 →（可选）verify
    → 导出 →（可选）一键交付。

    Args:
        op: originpro 模块对象（通常走专用 COM 线程被调用；为空则内部取 engine 句柄）
        columns: 内联数据，dict{列名: 列表} 或二维列表（与 origin_write_data 同语义）
        data_source: 本地 CSV/XLSX 路径（优先于 columns）—内部调 _load_file_impl 导入
        intent: "auto"（按数据形状自动选图型/角色）/"journal"（期刊单栏）/
                "presentation"/"quick"（800px 低分辨率快速预览）
        plot_type: 显式图型（line/scatter/line_symbol/column/histogram/box/bar…），
                   留空时由 auto 逻辑按列数推断
        x_column / y_columns: 列映射（覆盖自动推断）
        style_mode / family: 排版风格（intent 会隐含 journal/presentation）
        fmt / file_path / output_dir / width: 导出参数（width 会被 intent 覆盖）
        graph_name / title: 图页命名与标题
        verify: 是否跑一次确定性反读验证（默认 True）
        deliver: 是否一键交付（复用已有图，不重画）
        source_path: 交付时源文件路径（决定交付目录位置）

    Returns:
        oerr.ok，含 graph / file / steps（每步耗时 ms）/ timings.total_ms /
        proof_level（verified/readback_only/unverified 汇总）/ 各步详情。
    """
    import origin_engine as _eng

    # 计时与步骤采集：每步都记 ok + 耗时 ms，最后汇总 total_ms
    steps = []
    wall0 = _now_ms()

    def _step(name, fn):
        t0 = _now_ms()
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            ms = _now_ms() - t0
            steps.append({"step": name, "ok": False, "ms": ms,
                          "error": f"{type(e).__name__}: {e}"})
            return None
        ms = _now_ms() - t0
        steps.append({"step": name, "ok": bool(r and r.get("ok")), "ms": ms})
        return r

    # --- 0. 连接（确保 COM 线程内 op 有效）---
    ok_conn, conn = _eng._connect_impl()
    if not ok_conn:
        return conn
    op = _resolve_op(op)
    if op is None:
        return oerr.fail("connection_error", "无法取得 Origin COM 句柄")

    intent = (intent or "auto").lower()
    style_mode = _style_mode_for_intent(intent, style_mode)
    width = _width_for_intent(intent, width)

    # --- 1. 数据：导入文件 或 写内联数据 ---
    r_data = _step("data", lambda: (
        _eng._load_file_impl(data_source)
        if data_source else _eng._write_data_impl(columns)
    ))
    if r_data is None or not r_data.get("ok"):
        return oerr.fail(
            "empty_data" if (not columns and not data_source) else "origin_operation_error",
            "数据准备失败：" + (str((r_data or {}).get("error", "未知")) if r_data else "异常"),
            steps=steps, timings={"total_ms": _now_ms() - wall0})
    worksheet = r_data.get("worksheet")
    n_cols = len(r_data.get("columns") or [])
    source_kind = "file" if data_source else "inline"

    # --- 2. 推断图型（auto 时按列数：单列散点，多列折线）---
    if not plot_type:
        plot_type = "scatter" if n_cols <= 1 else "line"

    # --- 3. 画图 + 套样式（_plot_impl 内部已调 _apply_style_impl，合并成一次往返）---
    r_plot = _step("plot", lambda: _eng._plot_impl(
        worksheet, y_columns=y_columns, x_column=x_column, plot_type=plot_type,
        graph_name=graph_name, title=title, style_mode=style_mode, family=family))
    if r_plot is None or not r_plot.get("ok"):
        return oerr.fail(
            "origin_operation_error",
            "画图失败：" + (str((r_plot or {}).get("error", "未知")) if r_plot else "异常"),
            steps=steps, timings={"total_ms": _now_ms() - wall0})
    graph = r_plot.get("graph")
    style_info = r_plot.get("style") or {}
    style_applied = bool(style_info.get("applied")) if isinstance(style_info, dict) else bool(style_info)
    plotted_y = r_plot.get("y_columns") or []
    n_series = len(plotted_y)

    # --- 4. 验证（全程只跑一次，读回后即停，不重复 activate/inspect）---
    proof_level = PROOF_UNVERIFIED
    r_verify = None
    if verify:
        min_font = _min_font_for_style(style_mode)
        r_verify = _step("verify", lambda: _eng._verify_graph_impl(
            graph, expected_series=(n_series if n_series else None),
            min_font_pt=min_font))
        if r_verify is not None and r_verify.get("ok"):
            proof_level = (PROOF_VERIFIED if r_verify.get("passed")
                           else PROOF_READBACK)
        elif r_verify is not None:
            # verify 自身报错（读不回来）→ 视为未能读回
            proof_level = PROOF_UNVERIFIED

    # --- 5. 导出 ---
    r_export = _step("export", lambda: _eng._export_impl(
        graph, file_path=file_path, fmt=fmt, width=width, output_dir=output_dir))
    if r_export is None or not r_export.get("ok"):
        return oerr.fail(
            "export_error",
            "导出失败：" + (str((r_export or {}).get("error", "未知")) if r_export else "异常"),
            graph=graph, steps=steps, proof_level=proof_level,
            timings={"total_ms": _now_ms() - wall0})

    # --- 6. 交付（复用已有图，不重画；deliver=False 时跳过）---
    delivery = None
    if deliver:
        r_deliver = _step("deliver", lambda: _eng._export_delivery_impl(
            graph, source_path=source_path, output_dir=output_dir, width=width))
        if r_deliver is not None and r_deliver.get("ok"):
            delivery = {
                "delivery_dir": r_deliver.get("delivery_dir"),
                "files": r_deliver.get("files"),
                "opju": r_deliver.get("opju"),
                "all_ok": r_deliver.get("all_ok"),
            }

    total_ms = _now_ms() - wall0
    return oerr.ok(
        graph=graph, graph_short=graph, plot_type=plot_type,
        worksheet=worksheet, source_kind=source_kind,
        file=r_export.get("file"), size=r_export.get("size"),
        format=fmt, channel=r_export.get("channel"),
        style_mode=style_mode, style_applied=style_applied,
        style=style_info,
        verify=({"ok": bool(r_verify and r_verify.get("ok")),
                 "passed": (r_verify or {}).get("passed"),
                 "n_fail": (r_verify or {}).get("n_fail"),
                 "checks": [(c.get("name"), c.get("status"))
                            for c in ((r_verify or {}).get("checks") or [])]}
                if verify else None),
        proof_level=proof_level,
        delivery=delivery,
        steps=steps,
        timings={"total_ms": total_ms,
                 "steps_ms": {s["step"]: s["ms"] for s in steps}},
        detail=(f"端到端出图 {graph}（{plot_type}，{n_series} 条曲线，"
                f"intent={intent}）：导出 {r_export.get('file')}；"
                f"proof_level={proof_level}；总耗时 {total_ms}ms"))


# ---------------------------------------------------------------------------
# 2) 预热：把冷启动成本挪到用户不感知的时刻
# ---------------------------------------------------------------------------
def warmup_impl(op, start_origin=True):
    """预热 Origin：确保已连接，建一个临时工作表并立即删掉（触发一次完整 COM
    往返），把冷启动成本挪到用户不感知的时刻。

    Args:
        op: originpro 模块对象
        start_origin: False 且 Origin 未运行时，跳过预热（不主动拉起 Origin）；
                      True（默认）则确保连接（必要时拉起 Origin）。
    Returns:
        oerr.ok，含 warmup_ms（临时表创建+删除往返耗时）、connected、
        origin_running_before。
    """
    import origin_engine as _eng

    running_before = None
    try:
        running_before = _eng._origin_running()
    except Exception:
        running_before = None

    if not running_before and not start_origin:
        return oerr.ok(connected=False, warmup_ms=0, origin_running_before=False,
                       detail="start_origin=False 且 Origin 未运行，跳过预热")

    ok_conn, conn = _eng._connect_impl()
    if not ok_conn:
        return conn
    op = _resolve_op(op)
    if op is None:
        return oerr.fail("connection_error", "无法取得 Origin COM 句柄")

    # 建临时表 + 写一行 + 立即销毁：这是有意制造的一次完整 COM 往返，
    # 让后续真实调用不必再承担首连/首次对象创建的冷启动代价。
    t0 = _now_ms()
    temp = f"WARMUP_{_uuid.uuid4().hex[:8]}"
    try:
        wks = op.new_sheet("w", temp)
        if wks is not None:
            try:
                wks.from_list(0, [0.0], lname="warmup")
            except Exception:
                pass
            try:
                wks.destroy()               # 优先 COM 接口销毁
            except Exception:
                try:
                    op.po.LT_execute(f"window -c {temp};")  # 兜底 LabTalk 关窗
                except Exception:
                    pass
    except Exception:
        pass
    warmup_ms = _now_ms() - t0

    return oerr.ok(
        connected=True, warmup_ms=warmup_ms,
        origin_running_before=bool(running_before),
        connect_ms=(conn or {}).get("connect_ms"),
        detail=(f"预热完成：临时表 {temp} 创建+销毁往返 {warmup_ms}ms；"
                f"origin_running_before={bool(running_before)}"))


# ---------------------------------------------------------------------------
# 3) 页堆积治理
# ---------------------------------------------------------------------------
def pages_gc_impl(op, threshold=200, dry_run=True):
    """页堆积治理：项目页数 > threshold 时清理。

    实测：793 页时 list_pages 30.9s、单次 close 59.2s——故 dry_run 只报告，
    真正清理（closeAll）可能很慢，需耐心等待看门狗。

    Args:
        op: originpro 模块对象
        threshold: 页数阈值（默认 200）
        dry_run: True（默认）只报告；False 用 _manage_pages_impl(closeAll) 清理
    Returns:
        oerr.ok，含 pages_count / over_threshold / suggestion；
        dry_run=False 时附 removed / n_removed / pages_left 清理清单。
    """
    import origin_engine as _eng

    ok_conn, conn = _eng._connect_impl()
    if not ok_conn:
        return conn
    op = _resolve_op(op)
    if op is None:
        return oerr.fail("connection_error", "无法取得 Origin COM 句柄")

    lp = _eng._list_pages_impl()
    if not lp.get("ok"):
        return lp
    count = int(lp.get("count", 0))
    over = count > int(threshold)

    if not over:
        return oerr.ok(
            pages_count=count, over_threshold=False, suggestion=None,
            dry_run=dry_run,
            detail=f"当前 {count} 页 <= 阈值 {threshold}，无需清理")

    if dry_run:
        sugg = (f"项目页数 {count} 超过阈值 {threshold}；"
                f"调用 pages_gc_impl(dry_run=False) 清理全部页面"
                f"（实测大项目 closeAll 较慢，请耐心等待看门狗放行）")
        return oerr.ok(
            pages_count=count, over_threshold=True, dry_run=True,
            suggestion=sugg,
            detail=f"[dry_run] {sugg}")

    cl = _eng._manage_pages_impl("closeAll")
    if not cl.get("ok"):
        return cl
    return oerr.ok(
        pages_count=count, over_threshold=True, dry_run=False,
        removed=cl.get("removed"), n_removed=cl.get("n_removed"),
        pages_left=cl.get("pages_left"),
        detail=cl.get("detail"))
