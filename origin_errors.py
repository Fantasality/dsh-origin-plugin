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
- next_actions 给出模型可以直接照着做的恢复步骤，减少瞎试；
- RECOVERY_MAP 是"错误码 -> 恢复动作"映射表（policy 重试策略 + diagnose 诊断动作），
  fail() 自动把对应项内嵌到失败返回的 recovery 字段；recovery_info() 可单独查询。
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
    "origin_busy_user_session": (
        True,
        ["关闭当前 Origin 窗口后重试（isolated 模式不劫持已打开的 Origin）",
         "或在启动器环境改回 ORIGIN_SESSION=attach（默认）以复用已开实例"],
    ),
    "file_unsupported_format": (
        True,
        ["改用 CSV/TXT/XLSX 格式", "旧版 .xls 请先在 Excel/Origin 中另存为 XLSX"],
    ),
    "file_read_error": (
        True,
        ["确认文件存在且未损坏、未被其他程序锁定",
         "XLSX 需要 openpyxl：在插件 venv 中 pip install openpyxl 后重试"],
    ),
    "plan_not_found": (
        True,
        ["plan_id 已失效（服务器重启或缓存淘汰），重新调用 origin_plot_plan 生成",
         "确认 plan_id 来自本次会话的 origin_plot_plan 返回值"],
    ),
    "plan_stale": (
        True,
        ["数据或列映射在计划生成后发生了变化：重新 origin_plot_plan 生成新计划",
         "确认新计划的 plan_hash 后再 origin_plot_execute"],
    ),
    "template_unavailable": (
        True,
        ["查看 detail 中缺失的模板/图层能力说明",
         "改用基础 origin_plot 或其他 template_id（见 origin_status 的 features.templates）"],
    ),
    "delivery_error": (
        True,
        ["确认目标目录可写（OneDrive/网盘同步目录可能锁定文件）",
         "换一个 output_dir 后重试"],
    ),
    "window_activation_failed": (
        True,
        ["LabTalk 只在活动窗口内解析：先 origin_list_pages 复核图页短名",
         "用 origin_manage_pages(action='activate', pages=[<短名>]) 显式激活后重试",
         "若图页已隐藏/关闭，用 action='show' 或重新出图"],
    ),
    "layer_not_found": (
        True,
        ["用 origin_inspect_graph 查看图层数与索引（0 起始）",
         "确认 layer 参数后重试"],
    ),
    # --- v2.3.0 实测新增（column_formula / fit / 数据链路健壮性） ---
    "formula_no_effect": (
        True,
        ["复核源列确有数据、目标列引用正确后重试",
         "检查公式函数名：LabTalk 以 10 为底对数是 log() 而不是 log10()（log10 会静默无效）",
         "引擎已自动做 range 变量绑定与函数名纠正，仍失败说明公式本身无法解析"],
    ),
    "no_data_to_fit": (
        True,
        ["用 origin_read_worksheet 查看拟合列：x/y 全空无法拟合",
         "先写入或修复数据（origin_write_data / origin_import_file）再拟合"],
    ),
    "insufficient_data": (
        True,
        ["线性拟合至少需要 3 个有效点", "补足数据点或改用 origin_stats 做描述统计"],
    ),
    "column_empty": (
        True,
        ["目标列长度为 0：先写入数据或用 origin_column_formula 计算派生列"],
    ),
    "column_all_nan": (
        True,
        ["列有长度但全为空值：常见原因是上游 column_formula 未生效或源列含文本",
         "用 origin_read_worksheet 复核上游数据，修复后重试"],
    ),
    # --- v2.3.0 项目保存策略（不自动写 .opju 的受控提示） ---
    "manual_save_required": (
        False,
        ["请在 Origin 窗口按 Ctrl+S 手动保存当前项目",
         "如需恢复脚本自动保存，取消环境变量 DSH_ORIGIN_NO_AUTO_SAVE 后重试"],
    ),
}

VALID_CODES = frozenset(CODE_META)

# ---------------------------------------------------------------------------
# 错误码 -> 恢复动作映射表（#4）
# ---------------------------------------------------------------------------
# 每个错误码对应一段结构化恢复策略，fail() 会把精简版内嵌到失败返回里：
#   policy     重试策略：
#                fix_args_then_retry      修正参数/输入后重试
#                retry_same_args          原样重试（瞬时故障，引擎已有自愈）
#                restart_origin_then_retry 先清理 Origin 进程再重试
#                replan_then_retry        数据/映射已变，重新生成计划后重试
#                no_retry                 不要重试（需人工介入或改策略开关）
#   diagnose   恢复前建议执行的诊断动作（工具调用或系统命令）
RECOVERY_POLICY_FIX = "fix_args_then_retry"
RECOVERY_POLICY_RETRY = "retry_same_args"
RECOVERY_POLICY_RESTART = "restart_origin_then_retry"
RECOVERY_POLICY_REPLAN = "replan_then_retry"
RECOVERY_POLICY_NO = "no_retry"

RECOVERY_MAP: Dict[str, Dict[str, Any]] = {
    "connection_error": {
        "policy": RECOVERY_POLICY_RESTART,
        "diagnose": ["origin_diagnose",
                     "taskkill /F /IM Origin64.exe 后等 2 秒再重连"
                     "（或设置 DSH_ORIGIN_AUTOKILL=1 让引擎连接前自动清理）"],
    },
    "worksheet_not_found": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_status", "origin_list_project"],
    },
    "graph_not_found": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_list_graphs"],
    },
    "column_not_found": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_read_worksheet"],
    },
    "invalid_request": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_help"],
    },
    "empty_data": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_write_data 或 origin_import_file"],
    },
    "no_such_column_ref": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_read_worksheet"],
    },
    "unsupported_origin_feature": {
        "policy": RECOVERY_POLICY_NO,
        "diagnose": ["origin_status 查看 features 能力清单"],
    },
    "origin_operation_error": {
        "policy": RECOVERY_POLICY_RESTART,
        "diagnose": ["origin_status", "origin_diagnose"],
    },
    "export_error": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_diagnose（含导出目录权限检查）",
                     "查看返回里的 attempts 字段确认三级导出通道各自失败原因"],
    },
    "file_error": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["确认文件路径存在且未被锁定"],
    },
    "invalid_column_designation": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_read_worksheet"],
    },
    "origin_busy_user_session": {
        "policy": RECOVERY_POLICY_RESTART,
        "diagnose": ["tasklist /FI \"IMAGENAME eq Origin64.exe\"",
                     "taskkill /F /IM Origin64.exe 后等 2 秒再重连"
                     "（或设置 DSH_ORIGIN_AUTOKILL=1 自动清理）"],
    },
    "file_unsupported_format": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["改用 CSV/TXT/XLSX"],
    },
    "file_read_error": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["确认文件未损坏/未被占用", "安装 openpyxl（XLSX 需要）"],
    },
    "plan_not_found": {
        "policy": RECOVERY_POLICY_REPLAN,
        "diagnose": ["origin_plot_plan 重新生成计划"],
    },
    "plan_stale": {
        "policy": RECOVERY_POLICY_REPLAN,
        "diagnose": ["数据或列映射已变化：重新 origin_plot_plan 并确认后 execute"],
    },
    "template_unavailable": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_status 查看 features.templates"],
    },
    "delivery_error": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["确认交付目录可写（网盘同步目录可能锁文件）"],
    },
    "window_activation_failed": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_list_pages", "origin_manage_pages(activate)"],
    },
    "layer_not_found": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_inspect_graph"],
    },
    "formula_no_effect": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_read_worksheet 复核源列/目标列",
                     "检查函数名：LabTalk 底 10 对数是 log() 不是 log10()"],
    },
    "no_data_to_fit": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_read_worksheet 查看 x/y 列数据"],
    },
    "insufficient_data": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["确认有效点数（线性拟合需 ≥3）"],
    },
    "column_empty": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_read_worksheet 复核列长度"],
    },
    "column_all_nan": {
        "policy": RECOVERY_POLICY_FIX,
        "diagnose": ["origin_read_worksheet 复核上游 column_formula / 数据写入"],
    },
    "manual_save_required": {
        "policy": RECOVERY_POLICY_NO,
        "diagnose": ["在 Origin 窗口按 Ctrl+S 手动保存",
                     "或设置 DSH_ORIGIN_NO_AUTO_SAVE=0 恢复自动保存"],
    },
}

_DEFAULT_RECOVERY = {
    "policy": RECOVERY_POLICY_RESTART,
    "diagnose": ["origin_status", "origin_diagnose"],
}


def recovery_info(code: str) -> Dict[str, Any]:
    """返回错误码的恢复动作映射项；未知码退回通用恢复策略。"""
    return RECOVERY_MAP.get(code, _DEFAULT_RECOVERY)


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
    """构造失败返回。error 缺省时给出该码的一段默认说明。

    自动内嵌该错误码的恢复动作（recovery.policy / recovery.diagnose），
    调用方可用 **extra 里的同名键覆盖。
    """
    recoverable, default_actions = code_info(code)
    payload = {
        "ok": False,
        "error_code": code,
        "error": error or f"{code}",
        "recoverable": recoverable,
        "next_actions": list(next_actions) if next_actions is not None else list(default_actions),
        "recovery": recovery_info(code),
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
