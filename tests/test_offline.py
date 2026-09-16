# -*- coding: utf-8 -*-
"""离线单测（P2-3）：不连 Origin，任何机器可跑（CI 友好）。

覆盖：错误码表一致性 / LabTalk 门禁 tokenizer / 路径白名单 / FigureSpec
校验与闭环 / plan 构建与 PLAN_STALE / 工具目录完整性。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_errors as oerr          # noqa: E402
import origin_plan as oplan          # noqa: E402
import origin_spec as ospec          # noqa: E402


# ---------------------------------------------------------------------------
# 错误码表
# ---------------------------------------------------------------------------
def test_recovery_map_covers_all_codes():
    for code in oerr.CODE_META:
        assert code in oerr.RECOVERY_MAP, f"{code} 缺恢复映射"


def test_p0_new_codes_present():
    for code in ("com_blocked_by_dialog", "labtalk_blocked", "path_not_allowed"):
        assert code in oerr.CODE_META, f"缺少 {code}"


# ---------------------------------------------------------------------------
# LabTalk 门禁（P0-2）
# ---------------------------------------------------------------------------
def test_gate_blocks_destructive():
    for script in ("delete Book1;", "exit", "doc -s;", "win -c Book1;",
                   "kill dataset1;"):
        bad = oerr.__dict__.get("_labtalk_gate") or \
            getattr(__import__("origin_engine"), "_labtalk_gate")
        r = bad(script)
        assert r is not None, f"{script!r} 应被拦截"


def test_gate_allows_normal_and_strings():
    gate = getattr(__import__("origin_engine"), "_labtalk_gate")
    for script in ("page.nlayers", 'wks.colWidth$ = "delete";',
                   "layer.x.from = 0;", "win -a Book1;"):
        assert gate(script) is None, f"{script!r} 不应被拦截"


# ---------------------------------------------------------------------------
# 路径白名单（P2-8）
# ---------------------------------------------------------------------------
def test_path_guard_default_open(monkeypatch):
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    eng = __import__("origin_engine")
    assert eng._path_guard(r"D:\any\where.png") is None


def test_path_guard_blocks_outside(monkeypatch):
    monkeypatch.setenv("DSH_ORIGIN_ALLOWED_ROOTS", r"D:\allowed;D:\also")
    eng = __import__("origin_engine")
    r = eng._path_guard(r"C:\Windows\evil.png")
    assert r is not None and r["error_code"] == "path_not_allowed"
    assert eng._path_guard(r"D:\allowed\ok.png") is None


# ---------------------------------------------------------------------------
# FigureSpec（P1-1）
# ---------------------------------------------------------------------------
def test_spec_roundtrip_plan_id_stable():
    plan = oplan.build_plan({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]},
                            plot_type="line")
    spec = ospec.spec_from_plan(plan)
    r = ospec.spec_import(spec)
    assert r.get("ok") and r["plan_id"] == plan["plan_id"]


def test_spec_validate_rejects_bad_kind():
    r = ospec.spec_import({"spec_version": 1,
                           "data": {"columns": {"x": [1]}},
                           "plot": {"kind": "hologram"}})
    assert not r.get("ok")


# ---------------------------------------------------------------------------
# plan / PLAN_STALE
# ---------------------------------------------------------------------------
def test_plan_stale_on_data_change():
    p1 = oplan.build_plan({"x": [1, 2], "y": [1.0, 2.0]}, plot_type="line")
    oplan.build_plan({"x": [1, 2], "y": [9.0, 9.0]}, plot_type="line")  # 同签名更新
    p1c = oplan.get_plan(p1["plan_id"])
    assert oplan.check_stale(p1c) is not None       # 有更新计划 => stale
    assert oplan.check_stale(p1c, force=True) is None


def test_plan_cache_bounded():
    assert oplan.cache_size() <= oplan.PLAN_CACHE_MAX


# ---------------------------------------------------------------------------
# 工具目录
# ---------------------------------------------------------------------------
def test_tool_catalog_structure():
    import origin_mcp_server as oms
    registry = oms._build_tool_registry()
    names = list(registry)
    assert len(names) >= 59, f"注册工具 {len(names)} < 59"
    assert len(set(names)) == len(names), "注册表重名"
    # 目录条目与注册表一一对应（目录有的必须能注册）
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "origin_mcp_server.py"),
        encoding="utf-8").read()
    import re
    blocks = re.findall(r'"name":\s*"(origin_[a-z0-9_]+)"', src)
    for n in set(blocks):
        assert n in names, f"目录条目 {n} 未注册"
