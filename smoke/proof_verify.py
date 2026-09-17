# -*- coding: utf-8 -*-
"""
smoke/proof_verify.py —— origin_proof 三级推断离线单测（不需要 Origin，CI 友好）

覆盖：verified / readback_only / unverified / 失败 四类，以及 decisions 明细推断。
直接 `python smoke/proof_verify.py` 运行，或用 pytest 收集。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_proof as opf  # noqa: E402


def test_verified_by_readback_status_ok():
    r = {"ok": True, "readback_status": "ok", "readback": 5, "requested": 5}
    opf.annotate(r)
    assert r["proof_level"] == opf.VERIFIED, r
    assert "可作证据" in r["proof_note"]


def test_verified_by_readback_status_capital():
    r = {"ok": True, "readback_status": "OK"}
    opf.annotate(r)
    assert r["proof_level"] == opf.VERIFIED


def test_readback_only_by_unreliable_flag():
    r = {"ok": True, "unreliable": True}
    opf.annotate(r)
    assert r["proof_level"] == opf.READBACK_ONLY, r


def test_readback_only_by_applied_unverified_field():
    r = {"ok": True, "applied_unverified": {"line_width_pt": "no readback"}}
    opf.annotate(r)
    assert r["proof_level"] == opf.READBACK_ONLY, r


# 线宽典型场景：写成功了，但通道不可靠（读回 nan_unreliable / unreadable）
def test_readback_only_by_nan_unreliable_status():
    r = {"ok": True, "readback_status": "nan_unreliable", "readback": None}
    opf.annotate(r)
    assert r["proof_level"] == opf.READBACK_ONLY, r


def test_readback_only_by_unreadable_status():
    r = {"ok": True, "readback_status": "unreadable"}
    opf.annotate(r)
    assert r["proof_level"] == opf.READBACK_ONLY, r


def test_readback_only_by_readback_field_without_status():
    r = {"ok": True, "readback": "x axis"}
    opf.annotate(r)
    assert r["proof_level"] == opf.READBACK_ONLY, r


def test_unverified_when_no_channel():
    r = {"ok": True, "applied": ["x_title"]}
    opf.annotate(r)
    assert r["proof_level"] == opf.UNVERIFIED, r


def test_unverified_plain_ok():
    r = {"ok": True}
    opf.annotate(r)
    assert r["proof_level"] == opf.UNVERIFIED, r


def test_failed_is_unverified():
    r = {"ok": False, "error_code": "com_blocked_by_dialog"}
    opf.annotate(r)
    assert r["proof_level"] == opf.UNVERIFIED, r


def test_decisions_rejected_downgrades():
    r = {"ok": True, "decisions": [
        {"field": "x", "decision": "applied"},
        {"field": "y", "decision": "rejected"},
    ]}
    opf.annotate(r)
    assert r["proof_level"] == opf.UNVERIFIED, r


def test_decisions_applied_unverified_is_readback_only():
    r = {"ok": True, "decisions": [
        {"field": "line_width_pt", "decision": "applied_unverified"},
    ]}
    opf.annotate(r)
    assert r["proof_level"] == opf.READBACK_ONLY, r


def test_decisions_all_applied_verified():
    r = {"ok": True, "decisions": [
        {"field": "x_title", "decision": "applied", "readback_verified": True},
    ]}
    opf.annotate(r)
    assert r["proof_level"] == opf.VERIFIED, r


def test_annotate_mutates_in_place_and_returns():
    r = {"ok": True, "readback_status": "ok"}
    out = opf.annotate(r)
    assert out is r
    assert "proof_level" in r and "proof_note" in r


def test_annotate_rejects_non_dict():
    for bad in (None, 5, "x", []):
        try:
            opf.annotate(bad)
            raise AssertionError(f"应抛 TypeError，却通过: {bad!r}")
        except TypeError:
            pass


def test_level_rank_ordering():
    assert opf.level_rank(opf.VERIFIED) > opf.level_rank(opf.READBACK_ONLY)
    assert opf.level_rank(opf.READBACK_ONLY) > opf.level_rank(opf.UNVERIFIED)


def test_is_evidence_only_verified():
    assert opf.is_evidence(opf.VERIFIED) is True
    assert opf.is_evidence(opf.READBACK_ONLY) is False
    assert opf.is_evidence(opf.UNVERIFIED) is False


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
            passed += 1
        except Exception:
            print("FAIL", fn.__name__)
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
