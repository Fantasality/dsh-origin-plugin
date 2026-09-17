# -*- coding: utf-8 -*-
"""
e2e_verify —— origin_e2e 端到端工具冒烟（6 项，真机 Origin）
================================================================
覆盖：
  ① figure_impl 用内联数据画 line 图并导出 PNG（断言文件存在>0、steps 齐全、
    total_ms 有值）
  ② figure_impl 用本地 CSV（脚本自生成临时 CSV）
  ③ intent="journal"
  ④ figure_impl(verify=True) 返回 proof_level
  ⑤ warmup_impl
  ⑥ pages_gc_impl(dry_run=True)

运行：
  D:/workbuddyworkspace/compare/_chem_venv/Scripts/python.exe -X utf8 smoke/e2e_verify.py
  （建议把 stdout/stderr 重定向到文件再读，串行跑更稳，Origin 共用一个 COM 实例）

结尾打印 E2E-VERIFY OK 或 E2E-VERIFY FAIL 并列出失败项。
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import time

# 把项目根目录加入 sys.path（本脚本位于 smoke/ 下）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import origin_engine as engine          # 仅用其公共 connect + _run_on_com_thread 通道
import origin_e2e as e2e

OUT_DIR = os.path.join(ROOT, "smoke", "_e2e_out")
os.makedirs(OUT_DIR, exist_ok=True)

RESULTS = []   # (name, ok, note)


def run_e2e(fn, **kw):
    """把 e2e 裸 impl 投递到专用 COM 线程执行（与公共 @_synchronized 同一通道），
    避免在主线程直接碰 COM 接口（线程亲和性 + 死锁规避）。

    op 一律传 None：figure_impl 等会内部取 engine._origin_app（COM 线程内权威句柄）。
    """
    # 确保专用 COM 线程已启动并连上 Origin（公共 connect 会启动线程+连接）
    try:
        engine.connect()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"connect 失败: {e}"}
    return engine._run_on_com_thread(fn, None, **kw)


def check(name, cond, note=""):
    RESULTS.append((name, bool(cond), note))
    mark = "OK " if cond else "ATTN"
    print(f"[{mark}] {name}  {note}", flush=True)


def gen_csv(path, n=12):
    """生成一个简单 CSV（x, y, y2），供"本地文件导入"用例使用。"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["x", "y", "y2"])
        for i in range(n):
            x = i * 0.5
            w.writerow([x, round(2 * x + 1, 3), round(x * x, 3)])


def main():
    t_total0 = time.time()

    # 公共样例数据
    xs = [i * 0.5 for i in range(20)]
    y1 = [round(2 * x + 1 + 0.3 * (x % 3), 3) for x in xs]
    y2 = [round(0.5 * x * x, 3) for x in xs]
    inline_cols = {"x": xs, "y1": y1, "y2": y2}

    # ① 内联 line 图 + 导出 PNG
    try:
        r1 = run_e2e(e2e.figure_impl, columns=inline_cols, plot_type="line",
                     fmt="png", file_path=os.path.join(OUT_DIR, "e2e_line.png"))
        f1 = r1.get("file")
        ok1 = (r1.get("ok") and f1 and os.path.exists(f1) and os.path.getsize(f1) > 0
               and isinstance(r1.get("steps"), list) and len(r1.get("steps")) > 0
               and isinstance((r1.get("timings") or {}).get("total_ms"), int)
               and (r1.get("timings") or {}).get("total_ms", 0) > 0)
        check("① inline line PNG", ok1,
              f"file={os.path.basename(f1) if f1 else None} "
              f"steps={len(r1.get('steps') or [])} "
              f"total_ms={(r1.get('timings') or {}).get('total_ms')} "
              f"graph={r1.get('graph')}")
    except Exception as e:  # noqa: BLE001
        check("① inline line PNG", False, f"异常: {type(e).__name__}: {e}")

    # ② 本地 CSV 导入 + 画图导出
    try:
        csv_path = os.path.join(OUT_DIR, "e2e_sample.csv")
        gen_csv(csv_path, n=14)
        r2 = run_e2e(e2e.figure_impl, data_source=csv_path,
                     fmt="png", file_path=os.path.join(OUT_DIR, "e2e_from_csv.png"))
        f2 = r2.get("file")
        ok2 = (r2.get("ok") and f2 and os.path.exists(f2) and os.path.getsize(f2) > 0
               and r2.get("source_kind") == "file")
        check("② from CSV", ok2,
              f"file={os.path.basename(f2) if f2 else None} "
              f"source_kind={r2.get('source_kind')} graph={r2.get('graph')}")
    except Exception as e:  # noqa: BLE001
        check("② from CSV", False, f"异常: {type(e).__name__}: {e}")

    # ③ intent="journal"
    try:
        r3 = run_e2e(e2e.figure_impl, columns={"x": xs, "y1": y1}, intent="journal",
                     fmt="png", file_path=os.path.join(OUT_DIR, "e2e_journal.png"))
        f3 = r3.get("file")
        ok3 = (r3.get("ok") and f3 and os.path.exists(f3) and os.path.getsize(f3) > 0
               and r3.get("style_mode") == "journal")
        check("③ intent=journal", ok3,
              f"file={os.path.basename(f3) if f3 else None} "
              f"style_mode={r3.get('style_mode')} "
              f"total_ms={(r3.get('timings') or {}).get('total_ms')}")
    except Exception as e:  # noqa: BLE001
        check("③ intent=journal", False, f"异常: {type(e).__name__}: {e}")

    # ④ verify=True 返回 proof_level
    try:
        r4 = run_e2e(e2e.figure_impl, columns=inline_cols, verify=True,
                     fmt="png", file_path=os.path.join(OUT_DIR, "e2e_verified.png"))
        pl = r4.get("proof_level")
        ok4 = (r4.get("ok") and pl in ("verified", "readback_only", "unverified")
               and isinstance(r4.get("verify"), dict))
        check("④ verify proof_level", ok4,
              f"proof_level={pl} verify_passed={(r4.get('verify') or {}).get('passed')} "
              f"file={os.path.basename(r4.get('file') or '')}")
    except Exception as e:  # noqa: BLE001
        check("④ verify proof_level", False, f"异常: {type(e).__name__}: {e}")

    # ⑤ warmup_impl
    try:
        r5 = run_e2e(e2e.warmup_impl, start_origin=True)
        ok5 = (r5.get("ok") and isinstance(r5.get("warmup_ms"), int)
               and r5.get("connected") is True)
        check("⑤ warmup", ok5,
              f"warmup_ms={r5.get('warmup_ms')} "
              f"connected={r5.get('connected')} "
              f"origin_running_before={r5.get('origin_running_before')}")
    except Exception as e:  # noqa: BLE001
        check("⑤ warmup", False, f"异常: {type(e).__name__}: {e}")

    # ⑥ pages_gc_impl(dry_run=True)
    try:
        r6 = run_e2e(e2e.pages_gc_impl, threshold=200, dry_run=True)
        ok6 = (r6.get("ok") and isinstance(r6.get("pages_count"), int)
               and isinstance(r6.get("over_threshold"), bool))
        check("⑥ pages_gc dry_run", ok6,
              f"pages_count={r6.get('pages_count')} "
              f"over_threshold={r6.get('over_threshold')} "
              f"suggestion={'有' if r6.get('suggestion') else '无'}")
    except Exception as e:  # noqa: BLE001
        check("⑥ pages_gc dry_run", False, f"异常: {type(e).__name__}: {e}")

    # 汇总
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("\n" + "=" * 60)
    print(f"E2E 冒烟结果：{passed}/{total} 通过")
    failed = [(n, note) for n, ok, note in RESULTS if not ok]
    if failed:
        print("失败项：")
        for n, note in failed:
            print(f"  - {n}: {note}")
        print("E2E-VERIFY FAIL")
    else:
        print("E2E-VERIFY OK")
    print(f"总耗时 {round(time.time() - t_total0, 1)}s", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
