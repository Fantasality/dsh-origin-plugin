# DSH Origin Plugin v2 · 设计蓝图与真机探测记录

本文档是 v2（工具 16 → 28）的设计/验证依据。分四部分：
① 设计蓝图（模块职责 / 数据流 / 线程模型 / clean-room 边界）
② 真机探测矩阵（Plot Type ID / 模板 / originpro API，全部在本机 OriginPro 2026b 验证）
③ 官方文档依据（canonical URL + 探测补充说明）
④ 验证矩阵（自测 + 视觉模型复核）

---

## 1. 设计蓝图

### 1.1 模块职责

```
origin_mcp_server.py   MCP 层：注册式 TOOL_CATALOG（单一事实源）、28 工具、自测入口
        │  调 origin_engine（@_synchronized 公共 API）
        ▼
origin_engine.py       引擎：连接/写数/画图/导出/样式应用/统计批/错误升级
        │  所有 COM 操作投递到唯一工作线程（_com_queue / _com_thread_loop）
        ▼
originpro (OriginExt → comtypes) → Origin64.exe（单实例 COM 自动化服务器）
```

新增独立模块（clean-room，仅依赖 numpy）：
- `origin_errors.py` —— 稳定错误码枚举 + recoverable + next_actions，
  `_synchronized` 边界自动把历史 `{ok:false, error}` 升级为结构化错误；
- `plot_style.py` —— OKLab 色差 / CVD 色盲模拟 / 白底对比度 / style_mode 预设 /
  语义轴标题推断 / 可读性计划；
- `origin_analysis.py` —— 纯 numpy 的 t 检验(Welch)/ANOVA/PCA/Kaplan-Meier。

### 1.2 关键设计决策

| 决策 | 取舍 |
|---|---|
| 专用 COM 线程串行化 | Origin 单实例 COM + 指针线程亲和 → 全部操作 funnel 到一条线程（天然并发安全，8/8 实测） |
| `graph_name` 幂等命名 | 传同名时清旧重画，图名稳定；不传则 Graph+序号（兼容旧行为） |
| 样式"仅多序列/显式时改色" | 单序列保持 Origin 默认，避免模板自定义面目全非 |
| box/bar 走官方模板而非 plotxy | 真机验证 plotxy 204/215 在 2026b 会渲成面积图/不出图 |
| 3D 散点 plotxy 310 + 页名差检测 | `wks.activate()` 前置 + `_page_names()` 差分判断是否真出了新页；无输出则优雅降级 |
| 轴标题用 `GLayer.axis('x'/'y').title` | 真机验证可靠；LabTalk `layer.y.title.text$` 在本机不可靠（会把标题设错页） |
| 预览闭环 `origin_view_graph` | 返回 mcp ImageContent（PNG base64），模型不落盘即可视检 |
| `origin_catalog` 文档即实现 | 工具清单只有一份 TOOL_CATALOG；catalog/help/description 均由此生成 |

### 1.3 clean-room 边界（借鉴 vs 原创）

设计方向上借鉴了 Ge-Shun/origin-mcp 的"参数化工具入口 / 默认排版规则与无障碍
调色板 / 语义轴标题推断 / 稳定 error_code / 幂等命名 / 预览闭环 / 分层 profile"
理念，但**全部实现为独立产物**：

- 未复制其任何源码、文档或 `official_docs.generated.json`；
- OKLab/CVD/对比度度量、Lanczos-γ + 连分数不完全 β 的 t/F 分布、SVD-PCA、
  KM 表均为本仓库自研（只依赖 numpy）；
- 轴标题/模板/plotxy 行为全部经本机真机探针确认后才写入代码；
- README/CHANGELOG/SKILL 均为新撰说明。

---

## 2. 真机探测矩阵（OriginPro 2026b，后台实例实测）

### 2.1 `op.new_graph(template=...)` —— 验证可用的官方模板

| 模板 | 结果 | 备注 |
|---|---|---|
| line / scatter / column / bar | ✓ | 柱状/条形 |
| box | ✓ | 箱线图（add_plot + type='?', '#'=行号作 X） |
| area / stack / doubleY | ✓ | 面积 / 堆叠 / 双 Y |
| pie / ternary / bubble / candlestick | ✓ | 饼图 / 三元 / 气泡 / K 线 |
| contour / heatmap | ✓ | 等高线 / 热图 |
| 3DBars / 3DVector / GLparafunc | ✓ | 3D 柱 / 3D 矢量 / 3D 曲面（matrix） |
| 3D scatter | Δ | plotxy 310 预检无页、真机**修复后可用**（activate+页名差）；见 2.2 |

### 2.2 LabTalk `plotxy iy:=... plot:=<id>` —— 关键结论（曾踩坑）

| ID | 名义类型 | 真机结果（2026b） |
|---|---|---|
| 204 | Bar(原注释)/Area(实际) | ✗ **渲成面积图** → bar 弃用 plotxy |
| 206 | Box | ✓ 但统一改走 box 模板更稳 |
| 215 | Bar | ✗ 某些上下文**不出页** |
| 310 | 3D scatter | 需 `wks.activate()` + `_page_names()` 差分；否则无页 → 修后可用，仍留优雅降级 |

> 教训：**plot type ID 必须真机验证**，官方注释值与实际版行为可能不一致。

### 2.3 originpro API 实测结论（写入代码的依据）

| API | 结果 | 用法 |
|---|---|---|
| `gl.plot_list()` | 是**方法**不是属性 | `gl.plot_list()` |
| `gl.axis('x'/'y')` | 返回 `Axis` 对象，`.title` 可读写 | 轴标题可靠路径 |
| `p.color` | RGB **元组**（非 int） | `p.color=(r,g,b)` |
| `p.symbol_kind` / `p.symbol_size` | 平面属性 | 符号循环 / 降符号 |
| `wks.to_col_range(i)` | 仅 1 col、2 位置参数 | 多列用 `plotxy iy:=(...)` 索引式 |
| `po.LT_execute` | 仅 1 位置参数（脚本） | 不再传入多个参数 |
| `gp.obj.GetName()` | 页短名 | 幂等命名/列表依据 |
| `gl.add_plot(wks, col, '#', type='?')` | 模板图加层 | box/bar 用 |

---

## 3. 官方文档依据

> 本环境网络策略屏蔽 docs.originlab.com（modsearch 桥 198.18.0.88 被拦），无法直接抓取，
> 故全部行为以**本机真机探针**为最终依据；下列 canonical URL 供复核引用。

- Plot Type IDs（labtalk `plotxy plot:=` 枚举，注释与 2026b 行为有出入，以探针为准）：
  https://docs.originlab.com/labtalk/ref/plot-type-ids/
- GraphPage 模板（`op.new_graph(template=...)` 名称清单）：
  https://www.originlab.com/doc/Origin-Help/Display-Templates
- originpro `GraphLayer.axis` / `Plot` 对象参考：
  https://docs.originlab.com/pythonextapi/pythonextapi/graph/GLayer.html
- 无障碍/调色板参考（CVD-safe 配色 Common 思想，自研实现）：
  https://davidmathlogic.com/colorblind/

---

## 4. 验证矩阵（全绿）

| 项 | 命令 | 结果 |
|---|---|---|
| 引擎级全链路（含新能力） | `origin_mcp_server.py --selftest` | SELFTEST OK |
| MCP 协议级（28 工具 + 跨协议图片内容） | `origin_mcp_server.py --mcp-test` | MCP-TEST OK |
| 并发稳定 | `--concurrency-test` | 8/8，6.76s |
| 天线回归 | `smoke\advanced_test.py` / `smoke\science_test.py` | OK |
| COM 冒烟 | `smoke\origin_com_smoke_test.py` | RESULT: OK |
| 最小调用 | `demo_call.py` | DEMO OK |
| compact 精简模式 | `ORIGIN_MCP_PROFILE=compact` | 24 工具，统计批隐藏 |

视觉复核（识图模型，见 `smoke/visual_check.py` 生成的图）：
- `vis_styled3.png`：3 序列 line_symbol — X 轴=温度(°C)、Y 轴=压力(kPa)、
  蓝圈(压力) / 橙三角(信号)、图例右上 —— 调色板/符号循环/双轴语义标题全部落图；
- `vis_bar.png`：确认是**真柱状图**（修复前是面积图）；
- `vis_dense.png`：600 点散点，符号适中未糊成线。

---

*本文件随 v2.0.0 一并维护；新增探测结论请追加到第 2 节而非改写历史。*
