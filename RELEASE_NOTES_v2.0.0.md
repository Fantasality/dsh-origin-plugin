## v2.0.0 (工具 16 → 28)

排版与统计大版本。

### 排版
- `style_mode`(default/journal/presentation) + 7 组 OKLab/CVD 无障碍调色板
- 幂等 `graph_name`（同名清旧重画）
- 语义轴标题（temperature_C → "Temperature (°C)"，`GLayer.axis().title` 真机验证）
- 多序列线型/符号循环 + 密集自动降符号

### 新工具
- origin_catalog(动态目录) / origin_error_codes / origin_list_graphs / origin_list_sheets
- origin_read_worksheet / origin_view_graph(内联图片预览) / origin_apply_style
- 统计批：origin_ttest / origin_anova / origin_pca / origin_survival（纯 numpy）

### 修复
- bar 改走官方 bar 模板（plotxy 204/215 在本机 2026b 渲成面积图/不出图）
- 3D 散点（310）经 activate+页名差修复
- 稳定错误码三件套 error_code/recoverable/next_actions

### 验证
- selftest / mcp-test(28 工具+图片内容) / concurrency 8/8 / science / advanced / com_smoke 全绿
- 出图经识图模型核对（docs/DESIGN.md 有完整探测矩阵）

详见 [CHANGELOG.md](https://github.com/Fantasality/dsh-origin-plugin/blob/v2.0.0/CHANGELOG.md)
