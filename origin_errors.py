# -*- coding: utf-8 -*-
"""
origin_engine 的稳定错误码框架（clean-room 设计）
================================================

统一失败返回结构，让 AI 客户端能安全分支/重试：

    {
      "ok": false,
      "error_code": "worksheet_not_found",     # 稳定的机器可读错误码
      "error": "人类可读信息",
      "recoverable": true,                     # 该错误是否应安全重试
      "next_actions": ["...", "..."],          # 具体可执行的下一步行动
      ...
    }

成功返回统一用 ok() 构造 {"ok": true, ...}。

设计要点（非抄袭，独立实现）：
- 错误码是稳定的字符串常量，客户端按它分支，不解析自由文本；
- recoverable 语义：true = 修正输入后可安全重试；false = 环境/依赖问题，别盲目重试；
- next_actions 给出模型可以直接照着做的恢复步骤，减少瞎试。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

# ---------------------------------------------------------------------------
# 错误码目录：code -> (recoverable, 默认下一步行动)
# ---------------------------------------------------------------------------
CODE_META: Dict[str, tuple] = {
    "connection_error": (
        False,
        ["确认本机已安装 Origin 并已启动（或允许脚本自动启动）",
         "关闭多余的 Origin64 进程，只保留一个主实例后重试",
         "若 Origin 以管理员权限运行而脚本不是，改用相同权限重试"],
    ),
    "worksheet_not_found": (
        True,
        ["用 origin_list_project / origin_status 查看当前工作簿",
         "确认传入的工作表引用形如 [Book1]Sheet1，并重新调用"],
    ),
    "graph_not_found": (
        True,
        ["用 origin_list_graphs 查看当前图页短名",
         "确认传入的 graph 引用正确后重试"],
    ),
    "column_not_found": (
        True,
        ["先用 origin_read_worksheet 查看列名列表",
         "传入正确的列名（长名优先）或 0 起始列索引"],
    ),
    "invalid_request": (
        True,
        ["核对参数取值（枚举/范围/格式）",
         "按返回的合法取值列表重新调用"],
    ),
    "empty_data": (
        True,
        ["先写入或导入数据（origin_write_data / origin_import_file）再调用"],
    ),
    "no_such_column_ref": (
        True,
        ["核对列引用是否正确（长名/短名/索引）", "参考 origin_read_worksheet 返回的列清单"],
    ),
    "unsupported_origin_feature": (
        False,
        ["该能力需要 OriginPro 或更高版本 Origin", "改用降级方案（如参数化入口的其他 kind）"],
    ),
    "origin_operation_error": (
        False,
        ["查看 error 字段的具体异常信息",
         "若是有害的数据/参数异常，修正后重试；若是 Origin 内部错误，重启 Origin 后再试"],
    ),
    "export_error": (
        True,
        ["确认输出目录存在且有写权限", "换一个输出路径或格式后重试"],
    ),
    "file_error": (
        True,
        ["确认文件路径存在且可读", "修正路径后重试"],
    ),
    "invalid_column_designation": (
        True,
        ["核对列角色参数（X/Y/Z/忽略）取值", "参考 origin_read_worksheet 的列信息"],
    ),
}

VALID_CODES = frozenset(CODE_META)


def code_info(code: str) -> tuple:
    """返回 (recoverable, next_actions)；未知码退回通用处理。"""
    info = CODE_META.get(code)
    if info is None:
        return CODE_META["origin_operation_error"]
    return info


def ok(**fields: Any) -> dict:
    """构造成功返回：ok 置顶。"""
    return {"ok": True, **fields}


def fail(code: str, error: Optional[str] = None, *,
         next_actions: Optional[Iterable[str]] = None,
         trace: Optional[str] = None, **extra: Any) -> dict:
    """构造失败返回。error 缺省时给出该码的一段默认说明。"""
    recoverable, default_actions = code_info(code)
    payload = {
        "ok": False,
        "error_code": code,
        "error": error or f"{code}",
        "recoverable": recoverable,
        "next_actions": list(next_actions) if next_actions is not None else list(default_actions),
    }
    if trace:
        payload["trace"] = trace
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# 例外 -> 错误码 的启发式归类（不解析具体厂商文案，只按通用特征）
# ---------------------------------------------------------------------------
def _classify_exception(exc: Exception) -> str:
    msg = str(exc).lower()
    text = f"{type(exc).__name__} {msg}"
    if any(k in text for k in ("not connected", "connection", "no connection",
                               "server", "attach", "automation error")):
        return "connection_error"
    if any(k in text for k in ("worksheet", "sheet", "book1", "wks") ) and \
       any(k in text for k in ("not found", "no such", "does not exist", "failed to find")):
        return "worksheet_not_found"
    if "graph" in text and any(k in text for k in ("not found", "no such", "does not exist")):
        return "graph_not_found"
    if any(k in text for k in ("column", "col(1", "colindex")) and \
       any(k in text for k in ("not found", "invalid", "bad index", "out of range")):
        return "column_not_found"
    return "origin_operation_error"


def from_exception(exc: Exception, *, prefix: Optional[str] = None,
                   trace: Optional[str] = None) -> dict:
    """把任意异常转成统一失败返回。"""
    code = _classify_exception(exc)
    msg = str(exc) or type(exc).__name__
    if prefix:
        msg = f"{prefix}{msg}"
    return fail(code, error=msg, trace=trace)


# ---------------------------------------------------------------------------
# 小工具：把引擎的旧式 {"ok": false, "error": ...} 快速升级为统一结构
# ---------------------------------------------------------------------------
def upgrade_legacy_failure(result: dict) -> dict:
    """若传入的是旧式失败 dict，按 error 文本猜测错误码升级；已是新式则原样返回。"""
    if result.get("ok"):
        return result
    if "error_code" in result:
        return result
    code = "origin_operation_error"
    text = str(result.get("error", "")).lower()
    if any(k in text for k in ("列不存在", "column", "no such col")):
        code = "column_not_found"
    elif any(k in text for k in ("工作表不存在", "no such sheet", "workbook", "book not")):
        code = "worksheet_not_found"
    elif any(k in text for k in ("not found", "不存在", "无输出", "创建失败")):
        code = "graph_not_found" if "图" in text else "origin_operation_error"
    elif any(k in text for k in ("必须是", "参数", "取值", "收到", "invalid")):
        code = "invalid_request"
    return fail(code, error=str(result.get("error", "")),
                **{k: v for k, v in result.items() if k not in ("ok", "error")})
