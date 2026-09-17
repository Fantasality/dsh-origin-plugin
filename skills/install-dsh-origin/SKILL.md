---
name: install-dsh-origin
description: 帮用户把 dsh-origin-plugin（AI 驱动 OriginLab Origin 画图的 MCP 插件）装好并验证可用。当用户说"帮我装 Origin 插件""配置 MCP""让 AI 能画 Origin 图"或遇到装到旧版本/连不上 Origin 时使用。
---

# 安装 dsh-origin-plugin（给 AI 看的操作手册）

> 你是被用户请来干活的 AI。**按下面的顺序做，不要跳步，不要凭印象改路径**。
> 目标：装完后用户说一句"用 Origin 画 x/y 折线图"，你就能真的画出图并导出文件。

---

## 第 0 步：先判断能不能装（30 秒）

依次确认，任何一条不满足就**停下来告诉用户**，不要硬装：

| 检查 | 怎么查 | 不满足怎么办 |
|---|---|---|
| Windows | `echo %OS%` 或看系统 | 不支持——Origin 只在 Windows |
| 装了 Origin | 找 `C:\Program Files\OriginLab\Origin20*\Origin64.exe`；或让用户打开 Origin 看版本 | 让用户先装正版 Origin（2021+，推荐 2026） |
| Python 3.10+ | `python --version` | 让用户装 Python 并勾 "Add to PATH" |

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

> ⚠️ **版本陷阱（很常见）**：DSH 插件市场里可能显示 **2.2.1** 之类的旧版本（目录源缓存导致）。
> 想装最新就**显式指定版本**：`npm install -g dsh-origin-plugin@2.7.0`。
> 已经装了旧版就先 `npm uninstall -g dsh-origin-plugin` 再装指定版本。

---

## 第 2 步：确认 Python 与入口文件

插件靠 Python 连 Origin，配置里必须写**绝对路径**（写 `python` 经常指向错误环境）。

找一个**装了 originpro 的** Python：
```bash
python -c "import originpro; print('OK')"
```
- 报 `ModuleNotFoundError` → 装依赖：`pip install originpro pywin32 numpy openpyxl pyyaml pillow`
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
   > "是不是下面这些之一：Cursor、Claude Desktop、VS Code、Cline、Windsurf、
   > Kimi Code、Gemini CLI、Codex、Trae、Zed、Continue、WorkBuddy？"
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

写入注意：
- **路径里的反斜杠要转义成 `\\`**（JSON 语法）
- 已配了别的 server 就**合并**，不要覆盖整个文件（先备份）
- Codex 的 TOML 是**追加**一段，不要重写整个文件

---

## 第 4 步：验证（必须做，别跳过）

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

## 第 5 步：排障（按现象查表）

| 现象 | 原因与处理 |
|---|---|
| 客户端里没有工具 | 配置 JSON 语法错（常见：反斜杠没转义 / 多了逗号）→ 用 `python -c "import json;json.load(open(r'<配置文件>'))"` 校验 |
| `origin_status` 报连不上 | 先跑 `python origin_mcp_server.py --selftest`；Origin 没开时插件会自动拉起，等 10-20 秒再试 |
| COM 注册问题 | 以**管理员**运行一次 Origin（会自动修复 COM 注册），然后重开 |
| 中文路径/中文列名乱码 | 已内置处理；仍异常就把数据移到纯英文路径 |
| 装到旧版本（2.2.1 等） | 见第 1 步的版本陷阱：显式 `@2.7.0` 安装 |
| 卡在弹窗不动 | 插件有看门狗会自动点掉；还卡就手动关掉 Origin 的对话框 |
| 某个功能"不支持" | 是**真的**不支持，不是配置问题——查 `<PKG_DIR>\COMPATIBILITY.md`（15 条实测失败场景，附替代方案） |

---

## 完工检查清单

- [ ] Windows + 正版 Origin 2021+
- [ ] Python 3.10+，`import originpro` 通过
- [ ] 客户端配置里有 `dsh-origin`，路径是绝对路径且已转义
- [ ] 客户端重启后能看到工具
- [ ] `origin_status` 返回 `connected: true`
- [ ] 说一句"用 Origin 画 x/y 折线图并导出 PNG"，能出图并给出文件路径

全部打勾就装好了。

---

## 附：用户可以怎么使唤你（装好之后）

- 「把这个 CSV 画成 Origin 折线图，期刊风格，导出 PNG」
- 「图例挪右上角，第二条线加粗」
- 「帮我做标准曲线拟合，给我斜率和 R²」
- 「保存成 OPJU 工程文件，我自己再改」

插件内部优先走 `origin_figure`（一次调用完成 写数→画图→验证→导出，约 1 秒），不用反复调用多个工具。
