---
name: install-dsh-origin
description: 帮用户把 dsh-origin-plugin（AI 驱动 OriginLab Origin 画图的 MCP 插件）装好并验证可用。当用户说"帮我装 Origin 插件""配置 MCP""让 AI 能画 Origin 图"或遇到装到旧版本/连不上 Origin 时使用。
---

# 安装 dsh-origin-plugin（给 AI 看的操作手册）

> 你是被用户请来干活的 AI。**按下面的顺序做，不要跳步，不要凭印象改路径**。
> 目标：装完后用户说一句"用 Origin 画 x/y 折线图"，你就能真的画出图并导出文件。

## 这次安装要装**两部分**（只看 MCP 就漏了一半）

| 部分 | 内容 | 漏了会怎样 |
|---|---|---|
| **① MCP 服务** | 把 `origin_mcp_stdio.py` 写进客户端配置 → 你获得 70 个 Origin 工具 | 你根本没有工具可用 |
| **② 项目 Skill**（第 4 步，**必须做**） | 让 AI 读到 `skills/origin-plotting/SKILL.md` 等文件 → 你获得"怎么用才对"的纪律：写后必读回、不虚构数据、歧义先问用户、避开静默失败通道 | **有工具但用得不对**：图不可信、反复踩坑、用户还得自己纠错 |

**两部分都做完才算装完。** 第 4 步不要跳。

---

## 第 0 步：先判断能不能装（30 秒）

依次确认，任何一条不满足就**停下来告诉用户**，不要硬装：

| 检查 | 怎么查 | 不满足怎么办 |
|---|---|---|
| Windows | `echo %OS%` 或看系统 | 不支持——Origin 只在 Windows |
| 装了 Origin | **按序快查，别一上来就全盘递归**（全盘 `-Recurse` 搜 Origin64.exe 要几分钟，实测坑）:<br>① `Get-ItemProperty HKLM:\SOFTWARE\OriginLab\* -ErrorAction SilentlyContinue`<br>② `Get-ChildItem 'C:\Program Files\OriginLab','C:\Program Files (x86)\OriginLab' -Filter Origin64.exe -Recurse -Depth 3 -EA 0`<br>③ 用户自定义盘：`Get-ChildItem 'D:\OriginLab*','D:\Origin*' -Filter Origin64.exe -Recurse -Depth 3 -EA 0`<br>④ 问用户："你的 Origin 装在哪个盘？"（最快） | 让用户先装正版 Origin（2021+，推荐 2026） |
| Python 3.10+ | `python --version`；没有就试 `py -3 --version`、`where.exe python` | 让用户装 Python 并勾 "Add to PATH" |
| npm（可选） | `npm --version`（没有也能装：走源码/zip 方式） | 可选——没有 npm 就让用户下载 release 包解压 |

**找不到就问，不要死磕**：三条快查都没结果时，直接问用户"Origin 装在哪台机器/哪个盘"，
比重试十次全盘搜索快得多。

**Origin 可以不开着**——插件会在首次调用时自动拉起它。

---

## 第 1 步：拿到插件（二选一）

**方式 A：npm 装（推荐，最快）**
```bash
npm install -g dsh-origin-plugin
# 国内用户如果慢，用镜像：
npm install -g dsh-origin-plugin --registry=https://registry.npmmirror.com
```
装完记下包位置：
```bash
npm root -g
# 通常是 C:\Users\<用户名>\AppData\Roaming\npm\node_modules\dsh-origin-plugin
```
把它记作 **`<PKG_DIR>`**。

**方式 B：Clone 源码**
```bash
git clone https://github.com/Fantasality/dsh-origin-plugin.git
cd dsh-origin-plugin
```
此时 `<PKG_DIR>` 就是这个 clone 出来的目录。

> ⚠️ **版本陷阱（很常见）**：DSH 插件市场里显示的版本可能滞后（目录源缓存导致）。
> **不要指定具体版本号**（本手册不锁版本，写死版本只会随时间过期）。
> 想拿最新版就**不写版本**：`npm install -g dsh-origin-plugin`（默认 latest）。
> 已经装了旧版就先 `npm uninstall -g dsh-origin-plugin` 再重装。
> 需要确认装到的是哪一版：`npm view dsh-origin-plugin version`。

---

## 第 2 步：确认 Python 与入口文件

插件靠 Python 连 Origin，配置里必须写**绝对路径**（写 `python` 经常指向错误环境）。

找一个**装了插件全部依赖的** Python（不只是 originpro——插件运行还需要 MCP SDK 等）：
```bash
python -c "import originpro; print('OK')"
```
- 缺依赖 → **一步装全**（推荐，别一个个装）：
  ```
  pip install -r <PKG_DIR>\requirements.txt
  ```
  至少包含 `originpro pywin32 numpy openpyxl pyyaml pillow` 与 MCP 运行时依赖。
  **注意**：同一个 Python 若被切换过版本（如客户端自带 runtime 升级），依赖会丢失，
  需要重装一次（实测坑：Codex 换 runtime 后 `mcp` 包不见了）。
- 不通就用项目自带的 venv：`python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`

记下这个 python.exe 的**绝对路径**（`where python` 可查），记作 **`<PYTHON>`**。

入口文件固定是：`<PKG_DIR>\origin_mcp_stdio.py`

---

## 第 3 步：写进用户的 AI 客户端配置

### 3.1 先自动检测（推荐，覆盖 13 种客户端）

```bash
cd <PKG_DIR>
python install.py            # 自动找客户端并写入配置（含备份）
python install.py --list     # 只看检测到哪些，不写入
```

支持：Claude Desktop / Cursor / Windsurf / Cline / VS Code(Copilot) / WorkBuddy /
Kimi Code / Gemini CLI / Trae / Zed / Continue / Codex CLI —— 结构不同的
（Continue 用数组、Zed 用嵌套对象、Codex 用 TOML）脚本会分别处理。

### 3.2 如果脚本一个都没检测到（**兜底流程，务必执行**）

**不要就此收工，也不要瞎猜配置文件位置。按顺序做：**

1. **直接问用户**（这是最可靠的一步）：
   > "你的 AI 客户端叫什么名字？（比如 Cursor / Claude Desktop / Codex / 通义灵码 /
   > 豆包 / 公司自研的 XX —— 有名字我就能找到它的配置文件；不确定的话，
   > 在客户端设置里找 'MCP' 或 '模型上下文协议' 字样）"
2. 用户答不上来时，**列出候选让用户指认**：
   > "是不是下面这些之一：**豆包**、**Cherry Studio**、Cursor、Claude Desktop、
   > VS Code、Cline、Windsurf、Kimi Code、Gemini CLI、Codex、Trae、Zed、Continue、
   > 通义灵码、腾讯元宝、Chatbox、WorkBuddy？"
3. **都没有 / 是自研客户端** → 给用户通用片段，让他粘到客户端的 MCP 设置里：
   ```json
   {"mcpServers": {"dsh-origin": {"command": "<PYTHON>", "args": ["<PKG_DIR>\\origin_mcp_stdio.py"]}}}
   ```
   并告诉他："在客户端里搜 'MCP' 或 '添加服务器'，把这段填进去。"
4. **完全找不到入口** → 让用户在客户端里直接说：
   > "请帮我把这个 MCP 服务器加进你的配置：命令是 <PYTHON>，参数是 <PKG_DIR>\origin_mcp_stdio.py，名字叫 dsh-origin"
   多数客户端能自己完成。

### 3.3 各客户端配置文件速查（兜底时用）

| 客户端 | 配置文件 | 结构 |
|---|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` | `mcpServers` |
| VS Code / Copilot | `%APPDATA%\Code\User\mcp.json` | `mcpServers` |
| Cline | `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json` | `mcpServers` |
| Windsurf | `%USERPROFILE%\.codeium\windsurf\mcp_config.json` | `mcpServers` |
| WorkBuddy | `%USERPROFILE%\.workbuddy\mcp.json` | `mcpServers` |
| Kimi Code | `%USERPROFILE%\.kimi-code\mcp.json` | `mcpServers` |
| Gemini CLI | `%USERPROFILE%\.gemini\settings.json` | `mcpServers` |
| Trae | `%USERPROFILE%\.trae\mcp.json` | `mcpServers` |
| Zed | `%APPDATA%\Zed\settings.json` | `context_servers.<名字>.command = {path, args}` |
| Continue | `%USERPROFILE%\.continue\config.json` | `experimental.modelContextProtocolServers`（数组） |
| Codex CLI | `%USERPROFILE%\.codex\config.toml` | TOML：`[mcp_servers.dsh-origin]` 段 |
| **DSH（DeepSeek Harness）** | **不走 mcp.json** | 用插件市场搜 `dsh-origin`；或读本仓库 SKILL |
| **Cherry Studio** | `%APPDATA%\cherry-studio\config.json` | `mcpServers`（元素还需 `type:"stdio"`、`isActive:true`；也可用 UI：设置 → MCP 服务器 → 添加） |

### 3.4 「UI 里添加」型客户端（配置文件位置随版本变，**不要猜路径**）

这些客户端在**设置界面**里加 MCP，直接引导用户点：

| 客户端 | 怎么做 |
|---|---|
| **豆包（Doubao）** | 见下方 3.5 专项——豆包有**两条路**，优先走"操作电脑"模式 |
| 通义灵码（阿里） | 插件设置里找 **MCP / 工具** → 添加服务器 → 填命令与参数 |
| 腾讯元宝（PC 端） | 设置里找 **扩展 / 工具 / MCP** 入口；找不到就用兜底第 4 步（让它自己配） |
| Chatbox | 设置 → **MCP** → 添加 → 类型选 stdio → 填命令与参数 |
| LobeChat / Open WebUI / Dify | 这些是**服务端/自托管**客户端，在各自的"工具/MCP 插件"配置里加；填的是同样的命令与参数 |

引导话术（通用）：
> "在设置里找 **MCP**、**工具**、**插件**、**扩展** 这几个字眼，找到『添加服务器』，
> 类型选 **stdio / 本地命令**，命令填 `<PYTHON>`，参数填 `<PKG_DIR>\origin_mcp_stdio.py`。"

### 3.5 豆包专项（用户量最大，两条路都要会讲）

**路 A：配 MCP（常规）**
1. 打开豆包 **电脑客户端** → 设置 → 找 **MCP 管理**（或"插件管理"）
2. 添加本地服务，启动命令填：
   ```json
   {"mcpServers": {"dsh-origin": {"command": "<PYTHON>", "args": ["<PKG_DIR>\\origin_mcp_stdio.py"]}}}
   ```
3. 保存后豆包会自己拉起这个本地进程（stdio，不需要公网地址与密钥）

**路 B：开"操作电脑"模式（豆包独有，且更适合小白）**
> 豆包 Windows/Mac 桌面版有 **工作任务模式 → 选择"本地电脑" → 技能栏点"操作电脑" → 授权**，
> 之后豆包可以**直接看屏幕、移鼠标、点按钮**（截图 OCR + UI 元素检测驱动，
> 不侵入应用内存），完全**不需要 MCP、API 或插件**。
>
> 对只用 Origin 画几张图的用户，这条路更快：用户说"打开 Origin，把 D 盘那个 CSV 画成折线图并导出 PNG"，
> 豆包就自己点。**代价**：慢（走人眼/人手路径）、且每步都要用户看着。
>
> 什么时候推荐哪条：**要重复做、要精确、要批量 → 配 MCP（路 A）；就画一两张、不想配置 → 操作电脑（路 B）**。

**怎么跟用户解释差别**（照抄即可）：
> "两条路你挑：①配 MCP，之后你说一句它就精确出图（1 秒），但要先配一次；
> ②开『操作电脑』模式，不用配置，但它像真人一样点鼠标，慢一些也需要你看屏幕。
> 想长期用建议 ①，只想试一次用 ②。"

写入注意：
- **路径里的反斜杠要转义成 `\\`**（JSON 语法）
- 已配了别的 server 就**合并**，不要覆盖整个文件（先备份）
- Codex 的 TOML 是**追加**一段，不要重写整个文件

---

## 第 4 步：**把项目的 Skill 也装进去**（重要，别只装 MCP！）

> ⚠️ **这一步不是可选项。** 只装 MCP = 只给了 AI 工具清单，没给它"怎么用"。
> 结果就是 AI 会用 `origin_plot` 但不知道项目纪律（写后必须读回、不虚构数据、
> 歧义要先问用户、通道静默失败要避开），画出来的图不可信、还会重复踩坑。

### 4.1 要装的两类东西

**A. 知识文件（必须让 AI 读到）** —— 都在 `<PKG_DIR>` 里：

| 文件 | 作用 | 优先级 |
|---|---|---|
| `skills/origin-plotting/SKILL.md` | 画图主流程 + 7 条铁律（写后读回、不虚构数据、改图前必 inspect…） | **必装** |
| `COMPATIBILITY.md` | 15 条实测失败场景（3D 轴标题写不进、heatmap 静默失败、图例坐标陷阱…） | **必装** |
| `skills/origin-stats/SKILL.md` | 统计检验流程与置信度说明 | 建议 |
| `skills/origin-scripting/SKILL.md` | 零安装脚本模式（生成脚本给用户自己跑） | 建议 |
| `QUICKSTART.md` / `README.md` | 用法与全部能力清单 | 可选 |
| `docs/`（FigureSpec 等） | 声明式复现协议的细节 | 可选 |

**B. 装的位置** —— 按客户端能力分三层，**从第 1 层开始试，不行往下降**：

#### 第 1 层：客户端有原生 Skill / 规则目录（最好）

把每个 `SKILL.md` 按 **`<目录>/<技能名>/SKILL.md`** 的形态复制过去：

| 客户端 | Skill 目录 |
|---|---|
| **WorkBuddy** | 用户级 `%USERPROFILE%\.workbuddy\skills\<名字>\SKILL.md`；项目级 `<项目>\.workbuddy\skills\<名字>\SKILL.md` |
| **Claude Code / Claude Desktop（Skills 功能）** | `%USERPROFILE%\.claude\skills\<名字>\SKILL.md` |
| **DSH** | 用插件市场装（自带 skills/），或让 AI 读插件安装目录下的 `skills/` |
| Cursor | 项目内 `.cursor\rules\` 放 `.mdc`（可把 SKILL 正文粘进去） |
| VS Code / Copilot | 项目内 `.github\copilot-instructions.md` |
| 各家通用 | 项目根已有现成的 **`AGENTS.md`**（本项目为 AI 写好的总入口：纪律 + 文件地图 + 工具速览）。<br>让用户**用客户端打开这个项目文件夹**（或把它加为工作目录），多数客户端会自动读它 |

具体命令（PowerShell 示例，复制 4 个 skill 到 WorkBuddy 用户级）：
```powershell
$src = "<PKG_DIR>\skills"
$dst = "$env:USERPROFILE\.workbuddy\skills"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Get-ChildItem $src -Directory | ForEach-Object {
  $t = Join-Path $dst $_.Name
  New-Item -ItemType Directory -Force -Path $t | Out-Null
  Copy-Item (Join-Path $_.FullName "SKILL.md") $t -Force
}
Copy-Item "<PKG_DIR>\COMPATIBILITY.md" $dst -Force   # 踩坑清单也放进去
```

#### 第 2 层：支持"自定义指令 / 系统提示词 / 助手人设"（次好）

把 **`skills/origin-plotting/SKILL.md` 的正文**（连同 7 条铁律）粘进客户端的
自定义指令框 / 助手提示词 / 人设配置里；再把 `COMPATIBILITY.md` 的关键几行一起贴上。
适用：Cherry Studio（助手 → 提示词）、豆包（智能体 / 自定义提示词）、
各种 Web 端客户端的"系统提示词"入口。

#### 第 3 层：什么都没有（兜底，也要做）

**别放弃**——项目文件夹留在本地，然后告诉用户：

> "以后每次开新对话，第一句话就说：
> **先读 `<PKG_DIR>\skills\origin-plotting\SKILL.md` 和 `COMPATIBILITY.md`，再开始。**"

或者把这两个文件内容**直接粘贴在对话开头**发给 AI。

### 4.2 装完自检（别自己宣布成功）

问 AI 一句：**「你读过本项目的纪律了吗？说说写图之后必须做什么、哪些操作会静默失败？」**

- 答得出"写后必须读回 / 不虚构数据 / 3D 轴标题与 heatmap 静默失败"→ **装进去了** ✅
- 答"不知道" → 回到 4.1 降层或换方式重装

**只有 MCP 装好 + Skill 读到，才算真的装完。**

---

## 第 5 步：验证（必须做，别跳过）

1. 让用户**完全退出并重启** AI 客户端（改配置必须重启才生效）
2. 让用户在对话里说一句：「调用 origin_status」
3. AI 应返回 `"connected": true` 和 Origin 版本信息

如果客户端里看不到工具，先自查：
```bash
cd <PKG_DIR>
python origin_mcp_stdio.py --print-config   # 打印各客户端该填的配置
python origin_mcp_server.py --offline-test  # 引擎自检（不连 Origin）
```

---

## 第 6 步：排障（按现象查表）

| 现象 | 原因与处理 |
|---|---|
| 客户端里没有工具 | 配置 JSON 语法错（常见：反斜杠没转义 / 多了逗号）→ 用 `python -c "import json;json.load(open(r'<配置文件>'))"` 校验 |
| `origin_status` 报连不上 | 先跑 `python origin_mcp_server.py --selftest`；Origin 没开时插件会自动拉起，等 10-20 秒再试 |
| COM 注册问题 | 以**管理员**运行一次 Origin（会自动修复 COM 注册），然后重开 |
| 中文路径/中文列名乱码 | 已内置处理；仍异常就把数据移到纯英文路径 |
| 装到旧版本 | 见第 1 步的版本陷阱：**不带版本号重装**（默认 latest）；`npm view dsh-origin-plugin version` 可查当前最新 |
| 装完客户端里看不到工具 | ①**完全退出并重启**客户端（改 MCP 配置必须重启才生效）②确认配置 JSON 语法正确（反斜杠转义）③问用户"能否看到 dsh-origin 这个 MCP 服务器"④仍不行就用手册第 3 步给的那句话**让客户端自己配** |
| 卡在弹窗不动 | 插件有看门狗会自动点掉；还卡就手动关掉 Origin 的对话框 |
| 某个功能"不支持" | 是**真的**不支持，不是配置问题——查 `<PKG_DIR>\COMPATIBILITY.md`（15 条实测失败场景，附替代方案） |

---

## 完工检查清单

**MCP 部分**
- [ ] Windows + 正版 Origin 2021+
- [ ] Python 3.10+，`import originpro` 通过
- [ ] 客户端配置里有 `dsh-origin`，路径是绝对路径且已转义
- [ ] 客户端重启后能看到工具
- [ ] `origin_status` 返回 `connected: true`
- [ ] 说一句"用 Origin 画 x/y 折线图并导出 PNG"，能出图并给出文件路径

**Skill 部分（别漏，缺了 AI 会用但用得不对）**
- [ ] `skills/origin-plotting/SKILL.md` 已让 AI 读到（原生 skill 目录 / 自定义指令 / 对话开头粘贴，三者之一）
- [ ] `COMPATIBILITY.md` 已让 AI 读到（避开 15 条实测坑）
- [ ] 自检通过：问 AI"写图之后必须做什么、哪些操作会静默失败"，答得出「写后读回、3D 轴标题/heatmap 静默失败」等

全部打勾就装好了。

---

## 附：用户可以怎么使唤你（装好之后）

- 「把这个 CSV 画成 Origin 折线图，期刊风格，导出 PNG」
- 「图例挪右上角，第二条线加粗」
- 「帮我做标准曲线拟合，给我斜率和 R²」
- 「保存成 OPJU 工程文件，我自己再改」

插件内部优先走 `origin_figure`（一次调用完成 写数→画图→验证→导出，约 1 秒），不用反复调用多个工具。
