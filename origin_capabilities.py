# -*- coding: utf-8 -*-
"""
origin_capabilities —— 从本机 Origin 安装提取"真实"绘图能力表
================================================================

动机（吸收竞品 Yike-Ye/OriginLab-MCP 的做法 #1）
------------------------------------------------
画图插件的"支持哪些图型"不应从文档手抄，而应从 Origin 安装目录里真实存在的
`oPlotIDs.h` 提取。该头文件是 Origin C 里所有 plot type 的权威 ID 定义
（`#define IDM_PLOT_XXX <编号>`）。手抄会随时间与版本脱节——这正是本项目要解决的
"文档/实现脱节"问题：引擎 `origin_engine.py` 顶部有一份硬编码图表型表
（`PLOT_TYPES` / `MATRIX_PLOT_TYPES` / `PLOT_TYPES_CN`），把它和 oPlotIDs.h 对比，
能直接暴露哪些"声称支持"其实对不上 Origin 真实 ID。

硬纪律
------
- 找不到 oPlotIDs.h 时，**绝不伪造数据**：返回 fail("invalid_request", ...) 并
  如实列出搜过的所有路径。
- 提取是纯文件解析（读 Origin 安装目录的 .h），不需要连 Origin COM，因此在
  COM 线程内直接调用 *_impl 也不会死锁（本模块不调任何 @_synchronized 包装函数）。

产物
----
- capabilities_impl(op=None, force_refresh=False)：提取能力表，带 source_path /
  extracted_at / count，并缓存到 ~/.dsh/origin_capabilities.json。
- compare_against_hardcoded_impl()：与 origin_engine 硬编码表对比，报告
  missing_in_hardcoded（Origin 有但插件没包）/ extra_in_hardcoded（插件声称但
  对不上 Origin，含 id_mismatches 致命项）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import origin_errors as oerr

# 缓存路径：~/.dsh/origin_capabilities.json
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".dsh")
_CACHE_PATH = os.path.join(_CACHE_DIR, "origin_capabilities.json")

# oPlotIDs.h 中每行 `#define IDM_PLOT_xxx 编号` 的解析正则
_RE_DEFINE = re.compile(
    r"#define\s+IDM_PLOT_([A-Za-z0-9_]+)\s+(\d+)\s*(?:///|//|$)"
)

# 插件硬编码图型名 -> oPlotIDs.h 中的规范名（用于对比；None 表示 Origin 无对应定义）
# 这是"显式别名表"，宁可写清楚也不靠模糊匹配误报。
_PLUGIN_TO_ORIGIN = {
    "line": "line",
    "scatter": "scatter",
    "line_symbol": "linesymb",
    "column": "column",
    "histogram": "histogram_type",
    "box": "box",
    "bar": "bar",
    "contour": "contour",
    "contour_fill": "heat_map",   # 注意：引擎 105 实为 heat_map，命名不同
    "3d_wire": None,              # 引擎用 106，但 Origin 无 3d_wire 定义
    "3d_surface": "3d_surface_new",
}


# ---------------------------------------------------------------------------
# 1) 定位 oPlotIDs.h（winreg + 扫盘，穷举常见位置）
# ---------------------------------------------------------------------------
def _registry_origin_path() -> Optional[str]:
    """读注册表拿 Origin 安装根目录（可能失败，返回 None）。"""
    try:
        import winreg
    except Exception:
        return None
    for hive, sub in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\OriginLab\Origin"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\OriginLab\Origin"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\OriginLab\Origin"),
    ):
        try:
            key = winreg.OpenKey(hive, sub)
            for i in range(64):
                try:
                    name, val, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                if name.lower() in ("installpath", "path", "programfiles",
                                     "originpro") and isinstance(val, str) and val:
                    return val
        except OSError:
            continue
    return None


def find_oplotids_header() -> Tuple[Optional[str], List[str]]:
    """定位 oPlotIDs.h。

    返回 (path_or_None, searched_paths)。搜不到时 path=None，searched 列出所有
    穷举过的路径，便于如实报告（不伪造）。
    """
    searched: List[str] = []
    candidates: List[str] = []

    reg = _registry_origin_path()
    if reg:
        candidates.append(os.path.join(reg, "OriginC", "System", "oPlotIDs.h"))
        candidates.append(os.path.join(reg, "oPlotIDs.h"))

    # 常见安装根
    for root in ("C:/Program Files/OriginLab",
                 "C:/Program Files (x86)/OriginLab",
                 "D:/Program Files/OriginLab",
                 "E:/Program Files/OriginLab"):
        if os.path.isdir(root):
            for entry in sorted(os.listdir(root)):
                base = os.path.join(root, entry)
                if os.path.isdir(base):
                    candidates.append(os.path.join(base, "OriginC", "System", "oPlotIDs.h"))
                    candidates.append(os.path.join(base, "oPlotIDs.h"))
                    candidates.append(os.path.join(base, "Samples", "oPlotIDs.h"))

    # 全盘兜底：仅当上述都没命中时才走（耗时），避免每次都扫整盘
    fallback_roots = [f"{d}:/" for d in "CDEFG"]
    for fr in fallback_roots:
        if os.path.isdir(fr):
            candidates.append(os.path.join(fr, "oPlotIDs.h"))  # 占位，触发下方 walk 仅当未命中

    for c in candidates:
        searched.append(c)
        if os.path.isfile(c):
            return c, searched

    # 仍未命中：做一次带超时的定向 walk（仅扫 Program Files 类目录，不扫全盘）
    for root in ("C:/Program Files/OriginLab",
                 "C:/Program Files (x86)/OriginLab",
                 "D:/Program Files/OriginLab"):
        if not os.path.isdir(root):
            continue
        for dp, _dn, fn in os.walk(root):
            if "oPlotIDs.h" in fn:
                p = os.path.join(dp, "oPlotIDs.h")
                searched.append(p)
                return p, searched
    return None, searched


# ---------------------------------------------------------------------------
# 2) 解析头文件
# ---------------------------------------------------------------------------
def parse_header(path: str) -> Dict[str, int]:
    """解析 oPlotIDs.h，返回 {规范名(小写去 IDM_PLOT_ 前缀): id}。"""
    table: Dict[str, int] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _RE_DEFINE.search(line)
            if not m:
                continue
            name = m.group(1).lower()
            pid = int(m.group(2))
            # 跳过显然非 plot type 的边界定义（如 _O_PLOT_IDS_H 未匹配；BEGIN/END 行无编号）
            if name in ("begin", "end", "last"):
                continue
            table[name] = pid
    return table


# ---------------------------------------------------------------------------
# 3) 能力提取（缓存）
# ---------------------------------------------------------------------------
def _load_cache() -> Optional[Dict[str, Any]]:
    try:
        if os.path.isfile(_CACHE_PATH):
            with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        return None
    return None


def _save_cache(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass  # 缓存失败不影响返回（只影响下次加速）


def capabilities_impl(op: Any = None, force_refresh: bool = False) -> Dict[str, Any]:
    """从本机 Origin 安装提取真实绘图能力表。

    op 形参保留（API 对称/可在 COM 线程内安全调用），本函数纯文件解析，不使用 op。
    force_refresh=True 时忽略缓存重新提取。

    返回（成功）：
        {ok, source_path, extracted_at, count, items:{name:id,...}, cached:bool}
    返回（失败，找不到头文件）：
        fail("invalid_request", ...) 含 searched_paths，绝不伪造。
    """
    cached = None if force_refresh else _load_cache()
    if cached and cached.get("source_path") and os.path.isfile(cached["source_path"]):
        cached["cached"] = True
        return oerr.ok(**cached)

    path, searched = find_oplotids_header()
    if not path:
        return oerr.fail(
            "invalid_request",
            "在本机未找到 Origin 的 oPlotIDs.h，无法提取真实能力表（已如实拒绝，不伪造）。",
            searched_paths=searched,
            hint="请确认 Origin 已安装；或设置环境变量 DSH_ORIGIN_HEADER 指向 oPlotIDs.h 绝对路径。",
        )

    try:
        table = parse_header(path)
    except Exception as e:
        return oerr.fail("invalid_request",
                         f"找到 oPlotIDs.h 但解析失败: {e}", source_path=path,
                         searched_paths=searched)

    if not table:
        return oerr.fail("invalid_request",
                         "oPlotIDs.h 解析为空，可能文件格式变化。", source_path=path,
                         searched_paths=searched)

    data = {
        "ok": True,
        "source_path": path,
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(table),
        "items": table,
        "cached": False,
    }
    _save_cache(data)
    return data


# 允许用环境变量覆盖头文件位置（罕见情况：装在非标准目录）
def _header_from_env() -> Optional[str]:
    p = os.environ.get("DSH_ORIGIN_HEADER")
    return p if (p and os.path.isfile(p)) else None


def capabilities_impl_env_first(op: Any = None, force_refresh: bool = False) -> Dict[str, Any]:
    """与 capabilities_impl 相同，但优先读 DSH_ORIGIN_HEADER 环境变量。"""
    env = _header_from_env()
    if env and not force_refresh:
        cached = _load_cache()
        if cached and cached.get("source_path") == env:
            cached["cached"] = True
            return oerr.ok(**cached)
        try:
            table = parse_header(env)
        except Exception as e:
            return oerr.fail("invalid_request", f"DSH_ORIGIN_HEADER 指向文件解析失败: {e}",
                             source_path=env)
        data = {"ok": True, "source_path": env,
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "count": len(table), "items": table, "cached": False}
        _save_cache(data)
        return data
    return capabilities_impl(op=op, force_refresh=force_refresh)


# ---------------------------------------------------------------------------
# 4) 与硬编码表对比（暴露文档/实现脱节）
# ---------------------------------------------------------------------------
def _hardcoded_claims() -> Dict[str, Any]:
    """惰性 import origin_engine，取顶部硬编码图表型表（CI 离线 import 安全）。

    返回：{plugin_types:[...], matrix_ids:{name:id}, cn_names:{name:中文}}
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import origin_engine as eng  # noqa: F401  (仅取常量)
    plugin_types = set(eng.PLOT_TYPES_CN.keys()) | set(eng.MATRIX_PLOT_TYPES.keys())
    return {
        "plugin_types": sorted(plugin_types),
        "matrix_ids": dict(eng.MATRIX_PLOT_TYPES),
        "cn_names": dict(eng.PLOT_TYPES_CN),
    }


def compare_against_hardcoded_impl() -> Dict[str, Any]:
    """把真实能力表与 origin_engine 硬编码表对比。

    返回结构化差异：
      - origin_type_count        : oPlotIDs.h 中真实 plot type 数
      - plugin_type_count        : 插件声称支持的图型数
      - missing_in_hardcoded     : Origin 有但插件没包（能力缺口，数量大属正常）
      - extra_in_hardcoded       : 插件声称但对不上 Origin 的项（重点：id_mismatches）
      - id_mismatches            : 插件声称的 ID 与 Origin 真实 ID 不一致的致命项
      - name_mismatches          : ID 一致但规范名不同（命名脱节）
      - unresolved               : 插件声称但 Origin 根本无对应定义
    """
    caps = capabilities_impl_env_first()
    if not caps.get("ok"):
        return caps  # 找不到头文件则如实返回（不伪造对比）
    origin = caps["items"]  # {name: id}
    claims = _hardcoded_claims()
    plugin_types = claims["plugin_types"]
    matrix_ids = claims["matrix_ids"]

    origin_names = set(origin.keys())
    missing = sorted(origin_names - set(_PLUGIN_TO_ORIGIN.keys()))

    id_mismatches = []
    name_mismatches = []
    unresolved = []
    covered_origin = set()
    for pt in plugin_types:
        origin_name = _PLUGIN_TO_ORIGIN.get(pt)
        if origin_name is None:
            # 插件声称，但 Origin 无对应定义（如 3d_wire）
            claimed_id = matrix_ids.get(pt)
            unresolved.append({"plugin_type": pt, "claimed_id": claimed_id,
                               "note": "Origin oPlotIDs.h 无此规范名"})
            continue
        covered_origin.add(origin_name)
        real_id = origin.get(origin_name)
        claimed_id = matrix_ids.get(pt)  # 2D 类型可能为 None（用 LabTalk 字母码）
        if claimed_id is not None and real_id is not None and claimed_id != real_id:
            id_mismatches.append({
                "plugin_type": pt, "plugin_claims_id": claimed_id,
                "origin_name": origin_name, "origin_real_id": real_id,
                "note": f"引擎声称 ID {claimed_id}，但 Origin 真实 {origin_name}={real_id}",
            })
        elif claimed_id is not None and real_id is not None and claimed_id == real_id \
                and origin_name != pt:
            name_mismatches.append({
                "plugin_type": pt, "origin_name": origin_name, "id": real_id,
                "note": f"ID 一致({real_id})但命名不同：插件'{pt}' vs Origin'{origin_name}'",
            })

    extra = {
        "id_mismatches": id_mismatches,
        "name_mismatches": name_mismatches,
        "unresolved": unresolved,
    }
    return oerr.ok(
        source_path=caps["source_path"],
        origin_type_count=len(origin),
        plugin_type_count=len(plugin_types),
        missing_in_hardcoded=missing,
        missing_count=len(missing),
        extra_in_hardcoded=extra,
        extra_count=len(id_mismatches) + len(name_mismatches) + len(unresolved),
        cn_names=claims["cn_names"],
        matrix_ids=matrix_ids,
    )


if __name__ == "__main__":
    import pprint
    print("== capabilities ==")
    pprint.pprint(capabilities_impl_env_first())
    print("\n== compare against hardcoded ==")
    pprint.pprint(compare_against_hardcoded_impl())
