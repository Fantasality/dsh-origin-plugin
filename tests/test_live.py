# -*- coding: utf-8 -*-
"""真机集成测试（P2-3）：无 Origin 的机器自动 skip（leima-max 模式）。

跑法：pytest tests/ -v   （有 Origin 的 Windows 机器上执行真 COM 全链路）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("originpro", reason="需要 originpro（Origin 自动化包）")

_origin_running = False
try:
    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Origin64.exe", "/FO", "CSV"],
        capture_output=True).stdout.decode("gbk", errors="replace")
    _origin_running = 'Origin64.exe' in out
except Exception:
    pass

if not _origin_running:
    pytest.skip("未检测到运行中的 Origin64.exe（真机集成测试跳过）",
                allow_module_level=True)

import origin_engine as engine  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _connect_origin():
    ok, conn = engine.connect()
    assert ok, f"Origin 连接失败: {conn}"
    yield
    engine.shutdown()


def test_write_plot_export_roundtrip(tmp_path):
    w = engine.write_data({"x": [1, 2, 3, 4], "y": [1, 4, 9, 16]})
    assert w.get("ok")
    gp = engine.plot(w["worksheet"], y_columns=["y"], x_column="x",
                     plot_type="line")
    assert gp.get("ok"), gp
    out = os.path.join(str(tmp_path), "t.png")
    r = engine.export(gp["graph"], fmt="png", file_path=out)
    assert r.get("ok") and os.path.getsize(out) > 0


def test_labtalk_gate_live():
    r = engine.labtalk("delete SomeBook;")
    assert (not r.get("ok")) and r.get("error_code") == "labtalk_blocked"


def test_release_reconnect_live():
    r = engine.release()
    assert r.get("ok") and r.get("released")
    st = engine.status()
    assert st.get("ok") and st.get("connected")


def test_matrix_write_plot_live(tmp_path):
    z = [[float(i * j) for j in range(4)] for i in range(3)]
    w = engine.matrix_write({"z": z})
    assert w.get("ok") and w.get("writeback_consistent")
    p = engine.matrix_plot(w["matrix"], plot_type="contour")
    assert p.get("ok"), p
