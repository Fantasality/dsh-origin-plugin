# AGENTS.md — 给 AI 助手的项目说明

> 你在一个叫 **dsh-origin-plugin** 的项目里。它的作用：让 AI 通过 MCP 驱动本机
> **OriginLab Origin** 画图、分析、交付。Windows + 正版 Origin（2021+，2026 实测）。
>
> **你（AI）请先读完下面 3 节再动手。** 这个项目的图纸和纪律都在文件里，不在你的记忆里。

---

## 0. 如果你是被用户叫来"装这个插件"的

读 [`skills/install-dsh-origin/SKILL.md`](skills/install-dsh-origin/SKILL.md)，按它一步步做。
**注意它有两部分**：①配 MCP（给你工具）②装 Skill（给你纪律）——**第 2 部分不要跳**。

---

## 1. 必须遵守的纪律（违反必出错，全部实测过）

1. **写后必读返回**：所有编辑类工具逐项返回 `status`（applied / readback_only / unverified / rejected），
   必须逐项看；`rejected` 必须重新想办法，不能当成功。
2. **改图前必 inspect**：曲线索引、图层号、页面短名以 `origin_inspect_graph` / `origin_list_pages` 读到的为准，**不要猜**。
3. **不虚构数据**：列用途有歧义 → 先问用户；不补造缺失值；派生列必须标注。
4. **优先快路径**：说一句就画图的场景，用 **`origin_figure`**（一次调用完成 写数→画图→验证→导出，约 1 秒）；
   只有需要细改 / 发表级精修 / 多组对比时，才走完整多步流程。
5. **不要绕道裸 LabTalk**：细粒度修改用 `origin_edit_*` / `origin_manage_pages`。
   裸 LabTalk 只在目标图页是活动窗口时可靠，否则**静默失败**（返回 NaN 或写到别的窗口）。
6. **读回不是证据**：返回值里的 `proof_level` 三级——
   `verified`（写入且读回一致）才能当证据；`readback_only` 需目视确认；`unverified` 必须调 `origin_view_graph` 看图。
7. **引用原样传递**：`origin_write_data` / `origin_load_file` 返回 `worksheet`，`origin_plot*` 返回 `graph`，后续直接传返回值。

---

## 2. 项目地图（要读的文件）

| 文件 | 什么时候读 |
|---|---|
| [`skills/origin-plotting/SKILL.md`](skills/origin-plotting/SKILL.md) | **画图/改图前必读**——主循环图 + 七步流程 + 铁律 |
| [`COMPATIBILITY.md`](COMPATIBILITY.md) | **动手前扫一眼**——15 条实测失败场景与替代方案（3D 轴标题、heatmap 静默失败、图例坐标陷阱…） |
| [`skills/origin-stats/SKILL.md`](skills/origin-stats/SKILL.md) | 做统计检验（t/ANOVA/PCA/生存分析）时 |
| [`skills/origin-scripting/SKILL.md`](skills/origin-scripting/SKILL.md) | 用户想拿脚本自己在 Origin 里跑（不需要 MCP）时 |
| [`skills/install-dsh-origin/SKILL.md`](skills/install-dsh-origin/SKILL.md) | 安装/排障时 |
| [`QUICKSTART.md`](QUICKSTART.md) | 用户问"怎么用/怎么装"时（小白语言） |
| [`README.md`](README.md) | 需要全部 70 个工具与能力清单时 |
| [`docs/`](docs/) | FigureSpec 声明式协议等细节 |

---

## 3. 工具面速览（70 个工具 / 34 错误码）

- **快路径**：`origin_figure`（端到端一张图）、`origin_warmup`、`origin_pages_gc`
- **环境**：`origin_status`、`origin_diagnose`、`origin_capabilities`、`origin_catalog`、`origin_help`
- **数据**：`origin_write_data`、`origin_load_file`、`origin_matrix_*`、`origin_import_matplotlib`
- **画图**：`origin_plot`、`origin_plot_file`、`origin_plot_template`、`origin_plot_plan` + `origin_execute_plan`
- **改图**：`origin_edit_plot/axis/legend/page`、`origin_manage_pages`、`origin_manage_plots`、`origin_manage_data`
- **分析**：`origin_fit`、`origin_analysis`、`origin_stats`(t/ANOVA/PCA/survival)
- **交付**：`origin_export`、`origin_export_delivery`、`origin_export_pptx`、`origin_save_project`、`origin_template_save/apply`
- **可信**：`origin_verify_graph`、`origin_view_graph`、`origin_proof`（读回分级）
- **声明式**：`origin_spec_export/import`（FigureSpec YAML，可 diff、可重放）

完整清单与参数以 `origin_catalog` / `origin_help` 的**运行时返回**为准（文档与实现同步维护）。

---

## 4. 用户是新手时的沟通要求

- 用户说中文就用中文回答；给文件路径时给**可直接点开的绝对路径**。
- 不要说"我帮你配置好了"——除非你真的验证过（`origin_status` 返回 `connected: true`）。
- 遇到本项目**确实不支持**的功能（见 COMPATIBILITY.md），**如实说不支持**并给替代方案，
  不要硬试、不要假装成功。
