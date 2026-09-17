---
name: origin-scripting
description: Origin 零安装脚本模式（v1.0.0）——教 AI 生成可直接粘贴进 Origin Script Window / Python Console 的 Python(originpro) 或 LabTalk 脚本，零依赖、零安装、不需要 MCP。用户拿去手动跑。
whenToUse: 用户没有配 MCP / 只想要一段能抄的脚本 / 想在 Origin 内手动跑 / 不想装 Python 环境也不想改 mcp.json 时；或用户说"给我段 Origin 脚本""贴进 Origin 怎么画""不用 AI 自动画，我自己跑"时
---

# Origin 脚本模式（零安装、零依赖、不碰 MCP）

## 什么时候用这个 skill（先判断，再生成）
- 用户**没有 MCP 客户端**、不会配 Python 环境、不想改 `mcp.json` → 直接给脚本让他手动跑。
- 用户只想要**一段可复制的脚本**（贴进 Origin 自己执行），而不是让 AI 自动画图。
- 用户想在 **Origin 内手动微调**前，先有现成脚本打底。
- 任何"给我段 Origin 脚本 / 贴进 Origin 怎么画 / 我自己跑"的表述 → 本 skill，不走 origin-plotting 的工具流。

## 铁律：生成脚本的硬规则（每条都对应本项目实测过的返工，违反必出错）
1. **内嵌 Python 一律 `import originpro as op`**：Origin 自带内嵌 Python + originpro，这是最稳通道；别用系统 python、别让用户 `pip install`。脚本开头固定：
   ```python
   import originpro as op
   op.set_show(True)   # 让 Origin 窗口可见（调试时）
   ```
2. **LabTalk 裸表达式只在"目标图页恰为活动窗口"时可靠** → 脚本里先 `activate()` 再操作，或优先用 **COM 作用域对象**（originpro 的 `wks`/`gp`/`gl` 对象自带作用域，不依赖哪个窗口活动）。需要 LabTalk 时务必先 `win -a <短名>` 或 `gp.activate()`，否则写进别的窗口/静默返回 NaN。
3. **已知静默失败通道（写进去像成功、实际没生效，最坑）**：
   - **3D 图（GL 层）的轴标题写不进去**——别给 3D 图设轴标题，留空或改用 2D；强行写会静默无效。
   - **矩阵 heatmap 的 `plotm` 全变体静默失败**——矩阵画热力图优先走 Origin 官方模板或先转工作表再画，别裸调 `plotm`。
4. **通道纪律（写后必须读回，NaN 比较恒为 False）**：
   - 设完轴标题/范围/样式，**读回**确认落点（`gl.xlabel` 再打印），不要假设写成功。
   - `NaN` 比较永远 False：`if x == float('nan')` 永远不成立；判空用 `import math; math.isnan(x)` 或 `x != x`。
   - LabTalk 读回值是 `NaN` → 该通道在当前图/版本无效，**别重试同一通道**，换 originpro COM 对象。

## 生成脚本的通用骨架（复制后只改"←改这里"标注行）
```python
# 作用：<一句话说明这张图干什么>
# 需要你改的：下面标了「←改这里」的行（数据 / 列名 / 轴标题 / 路径）
import originpro as op
op.set_show(True)

wks = op.new_sheet("w", "MyData")          # ←改这里：工作表短名
x = [20, 25, 30, 35]                        # ←改这里：X 数据
y = [95, 101, 112, 118]                     # ←改这里：Y 数据
wks.from_list(0, x, lname="temperature_C", axis="X")   # axis="X" 把第0列设为X
wks.from_list(1, y, lname="pressure_kPa", axis="Y")

gp = op.new_graph(lname="MyPlot")           # ←改这里：图页短名
gl = gp[0]                                  # 取第 0 个图层
gl.add_plot(wks, 1, 0, type="l")            # 1=Y列索引, 0=X列索引, type="l"折线
gl.rescale()

# 轴标题（语义化：物理量+单位）——写后读回验证
gl.set_x_label("Temperature (\u00b0C)")      # ←改这里
gl.set_y_label("Pressure (kPa)")            # ←改这里
print("x_label =", gl.xlabel)               # 读回确认（通道纪律）
```

## 模板（直接复制，只改标注行）

### 1. 折线图（line）
```python
# 作用：画一条折线图。改：数据、列名、轴标题、图名。
import originpro as op
op.set_show(True)
wks = op.new_sheet("w", "LineData")         # ←改这里
x = [0, 1, 2, 3, 4]                          # ←改这里
y = [0, 1, 4, 9, 16]                         # ←改这里 y=x^2
wks.from_list(0, x, lname="x", axis="X")
wks.from_list(1, y, lname="y_x2", axis="Y")
gp = op.new_graph(lname="LinePlot")          # ←改这里
gl = gp[0]
gl.add_plot(wks, 1, 0, type="l")             # type="l" 折线
gl.set_x_label("x"); gl.set_y_label("y = x\u00b2")   # ←改这里
gl.rescale()
```

### 2. 散点图（scatter）
```python
# 作用：画散点图。改：数据、列名、轴标题、图名。
import originpro as op
op.set_show(True)
wks = op.new_sheet("w", "ScatData")          # ←改这里
x = [1.2, 2.3, 3.1, 4.0, 5.5]                # ←改这里
y = [2.1, 3.9, 3.2, 5.1, 6.8]                # ←改这里
wks.from_list(0, x, lname="conc_mM", axis="X")
wks.from_list(1, y, lname="signal", axis="Y")
gp = op.new_graph(lname="ScatPlot")          # ←改这里
gl = gp[0]
gl.add_plot(wks, 1, 0, type="s")             # type="s" 散点
gl.set_x_label("Concentration (mM)"); gl.set_y_label("Signal (a.u.)")  # ←改这里
gl.rescale()
```

### 3. 多序列带图例（multi-series + legend）
```python
# 作用：多条曲线 + 图例。改：数据列、列名、轴标题、图名。
import originpro as op
op.set_show(True)
wks = op.new_sheet("w", "MultiData")         # ←改这里
wks.from_list(0, [20, 25, 30, 35], lname="T_C", axis="X")
wks.from_list(1, [95, 101, 112, 118], lname="sample_A")   # ←改这里
wks.from_list(2, [90, 98, 105, 111],  lname="sample_B")   # ←改这里
wks.from_list(3, [88, 93, 99, 104],   lname="sample_C")   # ←改这里
gp = op.new_graph(lname="MultiPlot")         # ←改这里
gl = gp[0]
for col in (1, 2, 3):                        # 把 1/2/3 列都画成 Y（X 用第0列）
    gl.add_plot(wks, col, 0, type="l")
gl.set_x_label("Temperature (\u00b0C)"); gl.set_y_label("Response (kPa)")  # ←改这里
gl.rescale()
# 图例：originpro 默认会按列名生成；读回确认图例文本
print("legend =", gl.legend.label)           # 通道纪律：写后读回
```

### 4. 导出 PNG（export）
```python
# 作用：把当前/指定图导出为 PNG。改：图短名、输出路径、宽度。
import originpro as op
op.set_show(True)
gp = op.find_graph("MultiPlot")              # ←改这里：要导出的图短名
if gp is None:
    raise SystemExit("找不到图 MultiPlot，先画出来再导出")
gl = gp[0]
out = r"D:\OriginOut\fig.png"                # ←改这里：输出路径（用原始字符串 r""）
gp.save_fig(out, type="png", width=1200, dpi=300)   # width 像素，dpi 另设
print("saved ->", out)                        # 导出后确认文件存在
import os; print("exists =", os.path.isfile(out))   # 通道纪律：别信"没报错"
```

### 5. 线性拟合（LabTalk 正确作用域 + 读回）
```python
# 作用：对工作表 (X,Y) 做线性拟合，输出斜率/截距。
# 注意：LabTalk fitlr 只在目标工作表为活动窗口时可靠 → 先 activate()。
import originpro as op
op.set_show(True)
wks = op.find_sheet("w", "ScatData")         # ←改这里：数据所在工作表短名
if wks is None:
    raise SystemExit("找不到工作表 ScatData")
wks.activate()                               # 通道纪律：先激活再调 LabTalk
# LabTalk fitlr：iy:=(colX, colY)；结果写到结果日志
op.lt_exec('fitlr iy:=(1,2) rd:=(<none>);')  # 1=X列 2=Y列（←按实际列号改）
# 读回拟合结果（不要用 NaN 比较判成败；用结果日志文本）
print("fit done, 查看 Results Log 的 Linear Fit 表")
```
> 一般拟合（指数/高斯/Gauss/Lorentz…）改用 Origin 的 NLFit：先 `wks.activate()`，再
> `op.lt_exec('nlbegin iy:=(1,2) func:=Gauss; nlfit; nlend;')`，**始终先激活窗口**。

## 自检清单（把脚本交给用户前过一遍）
- [ ] 开头 `import originpro as op` + `op.set_show(True)` 都在（硬规则 1）。
- [ ] 任何 LabTalk / 裸表达式前都 `activate()` 了目标窗口（硬规则 2）。
- [ ] 没有给 3D 图设轴标题、没有裸调 `plotm` 矩阵热力图（硬规则 3 静默失败通道）。
- [ ] 设完样式/标题后**有读回**打印；没有 `x == float('nan')` 这种恒 False 比较（硬规则 4）。
- [ ] 每份脚本头部写清"在做什么 + 需要改哪几行"（脚本是给人看的，不是给机器盲跑的）。
- [ ] 路径用原始字符串 `r"..."` 或双反斜杠，避免 `\n` `\t` 被转义。

## 备注
- 本 skill 与 origin-plotting（MCP 自动画图）互补：**没 MCP / 只要脚本 → 本 skill**；
  有 MCP 客户端、想让 AI 自动画图改图 → 用 origin-plotting。
- 不要把本 skill 写成"工具手册"：给的是**人能复制、能改、能看懂的脚本**，不是 API 罗列。
