# -*- coding: utf-8 -*-
"""
origin_proof —— 统一的证据等级（proof_level）语义
================================================

为什么需要它
------------
Origin 画图里，"写入"和"生效"是两回事。本插件已多次踩坑：
- 3D 图（GL 层）的轴标题写进去了，但读回为空（通道不可靠）；
- 矩阵 heatmap 的 plotm 全变体静默失败（根本没出图，却无报错）；
- 某些属性（如线宽）写入后没有可靠读回通道。

如果 AI 把"读回成功"误当"已生效"，就会在论文/报告里引用一张并不存在的图属性。
竞品 Yike-Ye/OriginLab-MCP 的教训正是：readback is a readback, never as proof。

因此所有"写后"返回都应带一个 proof_level 字段，让 AI 明确知道这条结果
到底能不能当证据用。本模块与 Origin 完全无关（纯 Python），可在 CI 离线单测。

三级定义
--------
- verified      : 写入且读回一致（写入通道可靠，且读回值 == 期望值）。可作结论证据。
- readback_only : 有读回，但通道不可靠（如线宽、GL 层轴标题）。读回成功 ≠ 真生效，
                  必须 origin_view_graph 目视确认后才能采信。
- unverified    : 没有任何读回通道（纯写入，无法自检）。绝不可当作已生效，
                  必须 origin_view_graph 目视确认。

annotate(result) 不修改调用方业务字段，只在 dict 上补 proof_level / proof_note。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# 三级常量（用字符串而非魔法值，便于 AI/客户端按值分支）
# ---------------------------------------------------------------------------
VERIFIED = "verified"
READBACK_ONLY = "readback_only"
UNVERIFIED = "unverified"

_LEVEL_RANK = {UNVERIFIED: 0, READBACK_ONLY: 1, VERIFIED: 2}

# 给 AI 看的简短说明：什么等级能当证据、什么必须目视确认。
PROOF_GUIDE = (
    "证据分级 proof_level 说明（读回不是证明：readback is a readback, never as proof）：\n"
    "• verified      —— 写入且读回一致，通道可靠，可作结论证据。\n"
    "• readback_only —— 有读回但通道不可靠（如线宽、3D GL 层轴标题）；读回成功≠真生效，"
    "必须 origin_view_graph 目视确认后才能采信。\n"
    "• unverified    —— 无任何读回通道（纯写入无法自检）；绝不可当作已生效，"
    "必须 origin_view_graph 目视确认。\n"
    "原则：只有 verified 能当证据；readback_only / unverified 都要目视复核，不能据以引用结论。"
)


# ---------------------------------------------------------------------------
# 推断核心
# ---------------------------------------------------------------------------
def _readback_status(r: Dict[str, Any]) -> Optional[str]:
    """读回状态：兼容 readback_status / 嵌套 decisions 里的 readback_verified。"""
    return r.get("readback_status")


def _infer(r: Dict[str, Any]) -> Dict[str, str]:
    """根据返回结构已有字段推断 (level, note)。

    推断优先级（越靠前越"弱/保守"，命中即返回）：
    1. ok is False            -> unverified（操作失败，无证据）
    2. unreliable / applied_unverified -> readback_only（明确声明通道不可靠）
    3. readback_status 存在：
         "ok"                  -> verified
         其它(nan_unreliable/unreadable/None) -> readback_only
    4. readback 字段存在（无状态）-> readback_only（有读回但无法确认可靠）
    5. decisions 明细存在：
         含 rejected           -> unverified（部分失败，整体不可采信）
         含 applied_unverified -> readback_only
         其余皆 applied        -> verified
    6. ok True 或 applied 存在但无读回 -> unverified
    7. 兜底                  -> unverified
    """
    if not isinstance(r, dict):
        raise TypeError("annotate 需要 dict 类型的返回结构")

    # 1) 失败即无证据
    if r.get("ok") is False:
        return {"level": UNVERIFIED,
                "note": "操作失败，未产生可采信证据（需排查 error/error_code）"}

    # 2) 显式声明不可靠
    if r.get("unreliable") or r.get("applied_unverified"):
        return {"level": READBACK_ONLY,
                "note": "返回结构显式标记通道不可靠，读回不足为凭，需目视确认"}

    # 3) readback_status
    rb_status = _readback_status(r)
    if rb_status is not None:
        if str(rb_status).lower() in ("ok", "verified", "consistent"):
            return {"level": VERIFIED,
                    "note": "读回状态 ok，写入与读回一致，可作证据"}
        return {"level": READBACK_ONLY,
                "note": f"读回存在但通道不可靠(readback_status={rb_status})，需目视确认"}

    # 4) 有 readback 字段但无状态
    if "readback" in r:
        return {"level": READBACK_ONLY,
                "note": "存在 readback 字段但无可靠状态标记，按不可靠处理，需目视确认"}

    # 5) 决策明细（style edit 等带 decisions 列表）
    decisions = r.get("decisions")
    if not isinstance(decisions, list):
        decisions = (r.get("style_plan") or {}).get("decisions")
    if isinstance(decisions, list) and decisions:
        any_rejected = False
        any_unverified = False
        any_applied = False
        for d in decisions:
            if not isinstance(d, dict):
                continue
            dec = d.get("decision")
            if dec == "rejected":
                any_rejected = True
            elif dec == "applied_unverified":
                any_unverified = True
            elif dec == "applied":
                any_applied = True
                if d.get("readback_verified"):
                    # 单个 applier 有可靠读回，升级该信息
                    return {"level": VERIFIED,
                            "note": f"字段 {d.get('field')} 写入后经读回核验一致"}
        if any_rejected:
            return {"level": UNVERIFIED,
                    "note": "存在 rejected 决策，整体不可采信，需逐项排查"}
        if any_unverified:
            return {"level": READBACK_ONLY,
                    "note": "含 applied_unverified 决策，通道不可靠，需目视确认"}
        if any_applied:
            return {"level": VERIFIED,
                    "note": "所有决策均为 applied 且已读回核验"}

    # 6) 仅声明成功/已应用但无读回
    if r.get("ok") is True or r.get("applied"):
        return {"level": UNVERIFIED,
                "note": "已应用但无读回通道，须 origin_view_graph 目视确认"}

    # 7) 兜底
    return {"level": UNVERIFIED,
            "note": "无足够字段推断，按最保守（unverified）处理，需目视确认"}


def annotate(result: Dict[str, Any]) -> Dict[str, Any]:
    """给任意 oerr 返回结构补上 proof_level / proof_note（原地修改并返回）。

    返回结构不变，仅新增两个键；若已存在 proof_level 则覆盖为本次推断值。
    非 dict 输入直接抛 TypeError，不让错误静默。
    """
    if not isinstance(result, dict):
        raise TypeError("annotate 需要 dict 类型的返回结构")
    info = _infer(result)
    result["proof_level"] = info["level"]
    result["proof_note"] = info["note"]
    return result


def level_rank(level: str) -> int:
    """等级序数：unverified<readback_only<verified（便于比较"哪个更可信"）。"""
    return _LEVEL_RANK.get(level, -1)


def is_evidence(level: str) -> bool:
    """该等级能否当作结论证据（仅 verified）。"""
    return level == VERIFIED


if __name__ == "__main__":
    # 快速自检（不需要 Origin）
    samples = [
        {"ok": True, "readback_status": "ok", "readback": 5},
        {"ok": True, "unreliable": True},
        {"ok": True, "readback_status": "nan_unreliable"},
        {"ok": False, "error_code": "com_blocked_by_dialog"},
        {"ok": True, "decisions": [{"field": "line_width_pt", "decision": "applied_unverified"}]},
        {"ok": True, "decisions": [{"field": "x", "decision": "rejected"}]},
        {"ok": True, "applied": ["x_title"]},
    ]
    for s in samples:
        annotate(s)
        print(s.get("proof_level"), "-", s.get("proof_note"))
