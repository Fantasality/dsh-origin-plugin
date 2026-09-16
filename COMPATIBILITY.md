# COMPATIBILITY — 兼容性矩阵与已知失败场景（v2.3.0）

> 本文件从 README 拆出（#21），集中维护"在什么环境能用/什么场景实测会失败/失败时怎么办"。
> 所有"实测"条目均来自真机探针（Windows 11 + Origin 2026/2026b），非文档推断。

## 1. 接入方式矩阵

| 接入方式 | 依赖 | 入口 | 适用 |
|---|---|---|---|
| **DSH 插件（推荐）** | DSH Desktop + 插件 venv（mcp/originpro/pywin32/numpy/openpyxl） | cordis.patch.yml 的 mcp-origin 加载器 → `origin_mcp_server.py` | DSH 内一键安装，COM 线程 + 跨进程锁全量启用 |
| **通用 MCP 客户端** | 仅官方 MCP SDK（`pip install "mcp>=1.0"`，实测 2.2.0）+ requirements.txt | `python origin_mcp_stdio.py` | Kimi Code / Cursor / Claude Desktop / WorkBuddy / Cline / Windsurf |
| **npx 启动** | Node（仅做启动器）+ Python 环境 | `npx -y dsh-origin-plugin`（bin: dsh-origin-mcp） | 不想手写 JSON 配置的用户 |
| **自动写入配置** | Python 3.10+ | `python install.py [--list/--yes/--remove/--client]` | 检测已装客户端并合并写入 mcpServers（自动备份） |

客户端配置片段（`python origin_mcp_stdio.py --print-config` 可打印全量）：

```json
{"mcpServers": {"dsh-origin": {
  "command": "<python 绝对路径>",
  "args": ["<插件目录>/origin_mcp_stdio.py"]
}}}
```

## 2. Origin 版本兼容

| 项 | 状态 | 说明 |
|---|---|---|
| Origin 2026 / 2026b（@V=10.3+） | ✅ 实测主力 | 全部 50 工具 |
| 双 Y 模板名 | ⚠️ 按名探测 | 2026 实测 `doubley`/`righty` 可用，`dualy` 不存在；`origin_status.features.templates` 为准 |
| Origin 2022–2025 | 🟡 大概率可用 | 核心 plot/edit/export 通道相同；新特性（能力握手 features）以 `origin_status` 返回为准 |
| Origin 简版/学生版 | 🟡 部分工具受限 | NLFit 全函数/3D 可能缺；`unsupported_origin_feature` 会明确拒绝 |
| 未安装 Origin | ❌ 不可用 | 插件通过 COM 驱动本机 Origin，不含绘图内核 |

## 3. Python / OS 兼容

| 项 | 状态 |
|---|---|
| Windows 10/11 + COM | ✅ 实测 |
| Python 3.10–3.13 | ✅ 实测 3.13 |
| macOS / Linux | ❌ COM/originpro 不可用 |
| Origin 以管理员运行、脚本非管理员 | ⚠️ COM 权限不匹配会连不上 → 两侧同权限 |

## 4. 已知失败场景（实测；遇到时按"处置"列做）

| # | 场景 | 现象 | 根因 | 处置 |
|---|---|---|---|---|
| 1 | 残留 Origin64 僵尸进程（上次会话崩溃） | COM 连不上或 LT_execute 报错 | 单实例 COM 连到坏实例 | `taskkill /F /IM Origin64.exe` 等 2 秒重连；isolated 会话已默认自动清理（`DSH_ORIGIN_AUTOKILL`） |
| 2 | 用户手动开着 Origin 时自动保存 .opju | 可能覆盖/锁定用户工程 | 自动化写入与手动编辑冲突 | 设 `DSH_ORIGIN_NO_AUTO_SAVE=1` → 工具返回 `manual_save_required`，改提示 Ctrl+S |
| 3 | 渐变设色 + 同一 COM 任务内导出 | 曲线 COM 枚举永久失效（verify 报 series_count=0，但导出图正常） | originpro 通道缺陷（v2.3.0 已修复：导出拆独立 COM 任务） | 升级到 v2.3.0+；旧版避免 gradient+同任务导出 |
| 4 | LabTalk `log10()` 列公式 | 静默不赋值不报错 | LabTalk 底 10 对数是 `log()` | 引擎已自动纠正函数名并做硬失败检测（`formula_no_effect`） |
| 5 | LabTalk `[Book]Sheet!col(N)` 内联作右值 | 静默返回 NaN | 非法语法；必须先绑定 range 变量 | 引擎 `origin_column_formula` 已内置 range 绑定 |
| 6 | 非活动窗口写轴标题 | 写入静默丢失（读回 `%(?X)`） | LabTalk 只在活动窗口解析 | `origin_edit_axis` 已内置 激活→写→读回；`set_axis_title_checked` 自动反查图页并激活 |
| 7 | 拟合空列 | 返回 missing 值 `-1.23456789e-300` 冒充 slope | Origin missing 值非 NaN | 引擎前置拦截（`no_data_to_fit`/`insufficient_data`）+ 返回参数消毒 |
| 8 | 多实例 Origin64 并存 | COM 连错实例、LT_execute 异常 | 单实例服务器被多个启动器拉起 | 关多余窗口只留 1 个；`origin_diagnose` 会报进程数 |
| 9 | 网盘同步目录（OneDrive 等）交付 | 文件被锁、导出半截 | 同步进程占用 | 换本地目录；`delivery_error` 会提示 |
| 10 | 中文路径导出 | 个别通道写不出 | LabTalk 对非 ASCII 路径的历史差异 | 引擎保存走 ASCII 临时目录搬运；导出走绝对路径+文件头校验 |
| 11 | `expGraph` 静默失败 | 命令不抛异常但文件没生成 | LabTalk 静默失败特性 | 三级导出回退链 + 每级文件存在性/文件头校验（`export_error` 带 attempts 明细） |
| 12 | 3D（GL 层）轴标题写入 | `layer.xl$/yl$/zl$` 与 COM `axis().title` 全部静默失败（读回空、图上仍显占位符） | GL 图层对象模型与 2D 不同，本机 Origin 2026 + originpro 1.1.15 实证无可用通道 | 如实拒绝（不冒充支持）；3D 图请在 Origin 内手动改轴标题；`plot3d` 的 `title` 参数（图页名）仍可用 |
| 13 | 3D 图色标（colorbar） | 未实现 | GL 层色标对象通道同上 | 同上；文档提示手动添加 |
| 14 | 矩阵 heatmap（plotm） | LabTalk `plotm` 全变体（`im:=`/`ogl:=`/`mat:=`、先 `win -a` 激活）均静默失败（err=False、0 plots） | plotm 通道在本机 Origin 2026 + 外部 Python COM 下不可用（2026-09-16 探针实证） | `origin_matrix_plot` 明确拒绝 heatmap；用 `contour_fill` 近似或 Origin 内手动绘制 |
| 15 | MBook/MSheet 的 `lname` 属性 | 返回空字符串 | originpro MBook/MSheet 未实现 lname | 引擎矩阵引用统一走 `obj.GetName()`；AI 不应直接依赖矩阵对象的 lname |

## 5. 与其他 Origin 方案的差异

| 项 | 本插件 | 远程/云端 Origin 服务（如 EditaPlot 类） |
|---|---|---|
| 网络依赖 | 无（纯本机 COM） | 需外网可达；国内访问不稳定 |
| 数据隐私 | 数据不出本机 | 数据上传第三方 |
| Origin 版本 | 用你已装的正版 | 受服务端版本限制 |
| 延迟 | 秒级 | 排队+网络往返 |

## 6. 环境变量总表

| 变量 | 默认 | 作用 |
|---|---|---|
| `ORIGIN_SESSION` | attach | `isolated`=不劫持已开 Origin |
| `DSH_ORIGIN_AUTOKILL` | isolated 时开 | 连接前清残留 Origin64 并等 2 秒 |
| `DSH_ORIGIN_NO_AUTO_SAVE` | 未设 | `1`=禁止自动写 .opju（提示 Ctrl+S） |
| `ORIGIN_MCP_PROFILE` | full | `compact`=隐藏统计批 |
| `ORIGIN_IPC_LOCK` | 关 | `1`=跨进程命名互斥体 |
| `DSH_ORIGIN_PYTHON` | — | npx/启动器指定 Python 解释器路径 |
