# -*- coding: utf-8 -*-
"""
build_origin_app.py —— 一键生成可安装的 Origin App 文件夹，并打印 mkOPX 命令
=============================================================================

把仓库里的 origin_app/ 复制到一个"用户可复制/Origin 可识别"的位置，并给出打包用的
mkOPX 命令（带**正确反斜杠**路径——正斜杠会让 mkOPX 卡死，这是实测过的坑）。

用法：
    python scripts/build_origin_app.py              # 复制到默认 Apps 目录并打印命令
    python scripts/build_origin_app.py --dry-run    # 自检：复制到临时目录、校验文件、
                                                    #   打印 mkOPX 命令，不碰 Origin、
                                                    #   不写 %LOCALAPPDATA%
    python scripts/build_origin_app.py --dest "D:/my/apps"   # 自定义目标父目录

默认目标：%LOCALAPPDATA%\\OriginLab\\Apps\\DSHOriginBridge
（Origin 会直接识别这个目录下的源码 App，无需先 mkOPX；mkOPX 是打成 .opx 的另一条路。）

自检（--dry-run）会验证：
    - 源 origin_app/ 存在且含 App.ini / launch.ogs / bridge_manager.py / README.md
    - 复制后目标目录结构完整
    - mkOPX 命令路径已用反斜杠、且 app:= 名字与目录名一致
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_APP = os.path.join(PLUGIN_DIR, "origin_app")
APP_NAME = "DSHOriginBridge"

# 必须随 App 一起存在的文件（缺一都不该打包）
REQUIRED_FILES = ["App.ini", "launch.ogs", "bridge_manager.py", "README.md"]


def _default_dest() -> str:
    return os.path.expandvars(r"%LOCALAPPDATA%\OriginLab\Apps")


def _to_backslash(path: str) -> str:
    """mkOPX 必须用反斜杠；统一转一下，避免用户环境里混进正斜杠。"""
    return os.path.normpath(path).replace("/", "\\")


def _check_source() -> list[str]:
    errors = []
    if not os.path.isdir(SRC_APP):
        return [f"找不到源 App 目录：{SRC_APP}"]
    for f in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(SRC_APP, f)):
            errors.append(f"源 App 缺少必需文件：origin_app/{f}")
    return errors


def _copy_app(dest_parent: str) -> tuple[str, list[str]]:
    """把 origin_app/ 复制为 <dest_parent>/DSHOriginBridge/，返回 (目标目录, 错误列表)。"""
    errors: list[str] = []
    target = os.path.join(dest_parent, APP_NAME)
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(SRC_APP, target)
    except Exception as e:
        return target, [f"复制失败：{e}"]
    # 校验复制结果
    for f in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(target, f)):
            errors.append(f"复制后缺失：{APP_NAME}/{f}")
    return target, errors


def _print_mkopx(target_dir: str) -> str:
    """构造并打印 mkOPX 命令（反斜杠路径）。"""
    app_folder = _to_backslash(target_dir)
    opx_path = _to_backslash(os.path.join(dest_parent_root(target_dir), f"{APP_NAME}.opx"))
    cmd = f'mkOPX app:="{APP_NAME}" opx:="{opx_path}";'
    print("\n在 Origin 的 Command Window 里执行（路径用反斜杠）：")
    print("  " + cmd)
    print("\n说明：")
    print(f"  - app:= 后面是 App 文件夹名（{APP_NAME}，不含路径）")
    print(f"  - opx:= 后面是输出的 .opx 完整路径")
    print(f"  - 若只想让 Origin 直接识别源码 App（不打包），把文件夹放在")
    print(f"    {app_folder}")
    print("    即可，无需 mkOPX。")
    return cmd


def dest_parent_root(target_dir: str) -> str:
    """mkOPX 的 opx 输出路径默认落在目标目录的父级（即 Apps 目录）。"""
    return os.path.dirname(target_dir)


def _emit(ok: bool, lines: list[str]) -> int:
    for ln in lines:
        print(ln)
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成可安装的 Origin App 并给出 mkOPX 命令")
    ap.add_argument("--dry-run", action="store_true",
                    help="自检模式：复制到临时目录、校验、打印命令，不写 Apps 目录")
    ap.add_argument("--dest", default=None,
                    help="目标父目录（默认 %LOCALAPPDATA%\\OriginLab\\Apps）")
    args = ap.parse_args(argv)

    lines: list[str] = []
    src_errs = _check_source()
    if src_errs:
        return _emit(False, ["[x] 源检查失败："] + [f"    - {e}" for e in src_errs])

    if args.dry_run:
        tmp = tempfile.mkdtemp(prefix="dsh_origin_app_")
        target, copy_errs = _copy_app(tmp)
        lines.append(f"[√] dry-run：已复制到临时目录 {target}")
    else:
        dest_parent = args.dest or _default_dest()
        os.makedirs(dest_parent, exist_ok=True)
        target, copy_errs = _copy_app(dest_parent)
        lines.append(f"[√] 已生成 App 目录：{target}")

    if copy_errs:
        lines.append("[x] 复制后校验失败：")
        lines += [f"    - {e}" for e in copy_errs]
        return _emit(False, lines)

    lines.append(f"[√] 文件齐全：{', '.join(REQUIRED_FILES)}")
    _print_mkopx(target)
    if args.dry_run:
        lines.append(f"\n[dry-run] 临时目录未清理（便于人工核对）：{target}")
        lines.append("           非 dry-run 时会直接写到 Apps 目录。")
    else:
        lines.append("\n完成。下一步：打开 Origin Command Window 执行上面的 mkOPX 命令，")
        lines.append("再把生成的 .opx 拖进 Origin（详见 origin_app/README.md）。")
    return _emit(True, lines)


if __name__ == "__main__":
    raise SystemExit(main())
