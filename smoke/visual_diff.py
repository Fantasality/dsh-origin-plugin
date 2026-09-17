# -*- coding: utf-8 -*-
"""visual_diff —— 冒烟产物视觉基准对比（P1-5，2026-09-16）。

思想来源：editaplot 的 50 张验证图资产。冒烟测试的数值断言会漏外观回归
（实证：图例 "\\n" 换行渲染、单系列图例暴露内部列名），本工具补上图像层：

- dHash 感知哈希（9x8 灰度差分，64 bit）对布局/配色/形状敏感、对噪声不敏感；
- 基准入库：`python smoke/visual_diff.py --capture <产物目录>` —— 复制白名单
  图片到 visual_baseline/ 并记录 sha256 + dhash 到 manifest.json；
- 回归检查：`python smoke/visual_diff.py --check <产物目录>` —— 对同名产物
  重算 dhash，与基准汉明距离 > 阈值（默认 14/64）即报差异；
  新增/缺失产物也报（防基准悄悄失效）。

退出码：0=一致，1=有差异（CI 可直接用）。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

from PIL import Image

BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "visual_baseline")
MANIFEST = os.path.join(BASELINE_DIR, "manifest.json")
# 白名单：案例名 -> 产物文件名（视觉敏感的代表性图；其它产物变化不算）
WATCHLIST = {
    # 化学 v2 案例（15 个场景基线）
    "d01_raman": "d01_raman.png",
    "d05_tafel": "d05_tafel.png",
    "d06_eis": "d06_eis.png",
    "d07_cv": "d07_cv.png",
    "d08_gcd": "d08_gcd.png",
    "d09_ts3d": "d09_ts3d.png",
    "d10_ticks": "d10_ticks.png",
    "d11_mask": "d11_mask.png",
    "d13_hist": "d13_hist.png",
    "c23_multipanel": "c23.png",
    "c21_dualy": "c21.png",
    "c25_forest": "c25.png",
    # 四种调用方式 × 大学物理化学案例（2026-09-17 新增）
    "wayA_app_boyle": "A_boyle.png",
    "wayB_script_boyle": "B_boyle.png",
    "wayB_script_beer": "B_beer.png",
    "wayB_script_titration": "B_titration.png",
    "wayB_script_arrhenius": "B_arrhenius.png",
    "wayC_mcp_boyle": "C_boyle.png",
    "wayC_mcp_beer": "C_beer.png",
    "wayC_mcp_titration": "C_titration.png",
    "wayC_mcp_arrhenius": "C_arrhenius.png",
    "wayD_pkg_arrhenius": "D_arrhenius.png",
}
THRESHOLD = 14  # 汉明距离阈值（64 bit dHash；实测同图重绘距离 0-6）


def dhash(path):
    img = Image.open(path).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(img.getdata())
    rows = [px[i * 9:(i + 1) * 9] for i in range(8)]
    bits = []
    for r in rows:
        bits += [1 if r[c + 1] > r[c] else 0 for c in range(8)]
    return "".join(str(b) for b in bits)


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def capture(src_dir):
    os.makedirs(BASELINE_DIR, exist_ok=True)
    manifest = {"threshold": THRESHOLD, "items": {}}
    for case, fname in WATCHLIST.items():
        src = os.path.join(src_dir, fname)
        if not os.path.isfile(src):
            print(f"[SKIP] {case}: {fname} 不在 {src_dir}")
            continue
        dst = os.path.join(BASELINE_DIR, fname)
        shutil.copy2(src, dst)
        manifest["items"][case] = {"file": fname, "sha256": sha256(dst),
                                   "dhash": dhash(dst)}
        print(f"[OK ] {case} <- {fname} ({os.path.getsize(dst)}B)")
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"baseline -> {MANIFEST} ({len(manifest['items'])} items)")
    return 0 if manifest["items"] else 1


def check(src_dir):
    if not os.path.isfile(MANIFEST):
        print("基准不存在：先跑 --capture")
        return 1
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    th = int(manifest.get("threshold", THRESHOLD))
    problems = []
    for case, item in manifest["items"].items():
        cur = os.path.join(src_dir, item["file"])
        if not os.path.isfile(cur):
            problems.append(f"{case}: 产物缺失 {item['file']}")
            continue
        dist = hamming(item["dhash"], dhash(cur))
        if dist > th:
            problems.append(f"{case}: 视觉差异 汉明距离 {dist} > {th} "
                            f"({item['file']})")
        else:
            print(f"[OK ] {case} dHash 距离 {dist}")
    for case, fname in WATCHLIST.items():
        if case not in manifest["items"]:
            problems.append(f"{case}: 基准未入库（capture 时缺失 {fname}）")
    for p in problems:
        print("[DIFF]", p)
    print(f"\n视觉基准检查: {len(manifest['items']) - len([p for p in problems if '视觉差异' in p or '产物缺失' in p])} 一致, "
          f"{len(problems)} 项需关注")
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", metavar="DIR", help="从产物目录建立基准")
    ap.add_argument("--check", metavar="DIR", help="对比产物目录与基准")
    a = ap.parse_args()
    if a.capture:
        return capture(a.capture)
    if a.check:
        return check(a.check)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
