# 快速上手（小白版）

> ## 😵 看不懂下面的任何一段话？
> **直接跳到「方式三 · 让 AI 帮你装」**——把那个文件发给你的 AI，说一句话，剩下的它全包。
> 这是最省事的路，也是我们最推荐新手走的路。

> ## 🧩 一个所有方式的前提
> **先把项目拿到本地**（术语叫"克隆"，就一条命令）：
> ```
> git clone https://github.com/Fantasality/dsh-origin-plugin.git
> ```
> 没装 git 的话，也可以直接下载压缩包解压：
> https://github.com/Fantasality/dsh-origin-plugin/archive/refs/tags/v2.7.0.zip
> （国内下载慢就用加速：把网址开头换成 `ghproxy.com/`）
>
> 拿到之后，你会有一个叫 `dsh-origin-plugin` 的文件夹——下面方式一、方式四都要用它。

## 先选一种用法

| 你是谁 | 推荐方式 | 要装什么 | 大概几分钟 |
|---|---|---|---|
| 我完全不懂技术，只想能用 | **方式三：让 AI 帮你装** ⭐最推荐 | 什么都不用装 | 1 分钟 |
| 我只想点一下就能用 | **方式一：Origin 里点按钮**（需先克隆项目） | 一个 App 文件 | 3 分钟 |
| 我没有 AI 客户端，但想让 AI 帮我写脚本 | **方式二：复制脚本粘进 Origin** | **什么都不用装** | 1 分钟 |
| 我有 Cursor / Claude Desktop / Kimi Code 等 | **方式三：一键接入 MCP** | 一条命令 | 2 分钟 |
| 我用 DSH（DeepSeek Harness） | **方式四：DSH 市场安装**（需先克隆项目才能自己搭） | 插件市场安装 | 1 分钟 |

**都要有**：Windows + 已安装并能正常打开的正版 Origin（2021 及以上，2026 实测）。

---

## 方式一：Origin 里点按钮（最省力）

> ⚠️ **这个方式需要先完成上面的「克隆项目」步骤**——App 文件在项目文件夹里的 `origin_app/` 目录，不在网上。

适合：不想碰命令行、不想配 Python 的人。

1. 打开你克隆下来的 `dsh-origin-plugin` 文件夹，在**命令行**里执行：
   ```
   cd dsh-origin-plugin
   python scripts/build_origin_app.py
   ```
   （不会开命令行？→ 别折腾了，**用方式三让 AI 帮你做**）
2. 它会在屏幕上打印三行命令，照着做：
   - 把生成的 `DSHOriginBridge` 文件夹复制到 `%LOCALAPPDATA%\OriginLab\Apps`
   - 在 Origin 的 **Command Window** 里跑那条 `mkOPX ...` 命令（**必须是反斜杠路径**，正斜杠会卡住）
   - 把生成的 `.opx` 文件拖进 Origin 窗口
3. 在 Origin 的 **Apps Gallery** 里会出现 **DSHOriginBridge** 按钮——**点一下启动，再点一下停止**。
4. 启动后，你的 AI 客户端（Cursor / Claude Desktop 等）就能连上这个 Origin 了。

> ⚠️ 别把文件夹直接拖进 Apps Gallery——Origin 会显示"接受"但**什么都不会发生**（实测过的坑）。必须走 `mkOPX` 打包。

---

## 方式二：复制脚本粘进 Origin（零安装）

适合：**任何 AI 都能用**——哪怕这个 AI 完全不支持插件。**不需要克隆项目。**

1. 把 Skill 文件 `skills/origin-scripting/SKILL.md` 的内容发给 AI（或让 AI 读这个文件）。
2. 对它说：「帮我生成一段 Origin 脚本，画 x/y 的折线图并导出 PNG」。
3. 把 AI 给出的脚本复制，粘贴到 Origin 的 **Script Window**（或 Python Console）里运行。

**优点**：不需要装 MCP、不需要配 Python 环境、不需要市场账号。
**注意**：脚本里的路径、列名要按你自己的数据改（SKILL 里的模板都标注了"改哪几行"）。

---

## 方式三：让 AI 帮你装 ⭐ 新手最推荐

**不管什么情况，这条路都能走通。**

1. 打开这个文件：[`skills/install-dsh-origin/SKILL.md`](skills/install-dsh-origin/SKILL.md)
2. **把整个文件的内容复制**，发给你正在用的 AI（WorkBuddy、Cursor、ChatGPT、豆包、随便什么）
3. 附上一句话：
   > **按这个手册帮我把 dsh-origin-plugin 装好并验证可用。**
4. 它会：检查你的电脑环境 → 找 Python → 找到你的客户端配置文件 → 写好 → 让你重启客户端 → 再验证一次

**如果它问你"你用的是哪个客户端"**，就照实说（比如"用 WorkBuddy"或"用 Cursor"）——手册里有如何应对"不认识你那个客户端"的兜底流程。

装完验证：在 AI 里说一句「调用 origin_status」，回复里有 `connected: true` 就成了。

**其它接入方式（懂点技术再看）**：

- **自动写配置**：`python install.py`（会扫描 13 种常见客户端并写入，带备份）
- **手动配置**：
  ```json
  {"mcpServers": {"dsh-origin": {"command": "你的python绝对路径", "args": ["项目绝对路径/origin_mcp_stdio.py"]}}}
  ```
- **看该填什么**：`python origin_mcp_stdio.py --print-config`
- **npx 直接拉起**：`npx dsh-origin-plugin`
- **多个 AI 共用一个 Origin**（HTTP 模式）：
  ```
  python origin_mcp_http.py --port 8731
  ```
  客户端填 `"url": "http://127.0.0.1:8731/mcp"`；可用 `DSH_ORIGIN_HTTP_TOKEN` 设口令。

---

## 方式四：DSH（DeepSeek Harness）内原生

> **两条路**：
> - **想直接用** → 在 DSH Desktop 的**插件市场**里搜 `dsh-origin`（来源选 1024Store 或 dshfind），点安装。**这条路不需要克隆项目。**
> - **想自己搭/改代码** → 先按前面的「克隆项目」做，再参考项目里的 `cordis.patch.yml`。
>
> ⚠️ 市场里的版本号可能滞后（比如显示 2.2.1）。想用最新版就用方式三里那条 npm 命令指定版本装。

---

## 第一次出图（复制这段给你的 AI）

> 用 Origin 画一张图：x = 1,2,3,4,5，y = 1,4,9,16,25，期刊风格，导出 PNG。

插件内部会走 **`origin_figure`**——一次调用完成 写数据 → 画图 → 验证 → 导出（约 1 秒），
而不需要 AI 来回调用七八次工具。这也是本插件**提速的主路径**。

想自己调细节（换颜色、改轴、挪图例、加误差棒），再说一句就行，AI 会用细粒度工具改。

---

## 国内加速 / 镜像

| 场景 | 地址 | 说明 |
|---|---|---|
| npm 安装加速 | `npm install -g dsh-origin-plugin --registry=https://registry.npmmirror.com` | 淘宝 npm 镜像，已同步最新版 |
| npm 包主页 | https://npmmirror.com/package/dsh-origin-plugin | 可查看版本与下载量 |
| GitHub 下载加速 | 把 `github.com` 换成 `ghproxy.com/https://github.com`（例：`ghproxy.com/https://github.com/Fantasality/dsh-origin-plugin/archive/refs/tags/v2.7.0.tar.gz`） | Release 源码包加速 |
| 插件市场 | DSH 桌面端市场搜 `dsh-origin`（1024Store / dshfind） | 国内直连，无需科学上网 |

> ⚠️ **市场里可能显示旧版本**：目录源缓存会滞后（例如停在 2.2.1）。
> 想用最新版，就用上面的 npm 命令装，或直接指定版本：`npm install -g dsh-origin-plugin@2.7.0`。

---

## 常见问题

| 现象 | 怎么办 |
|---|---|
| **装到了旧版本**（比如 2.2.1） | 目录源缓存导致。用 `npm install -g dsh-origin-plugin@2.7.0` 指定版本；或把上面那个 SKILL 文件发给 AI 让它处理 |
| 连不上 Origin | 先让 AI 调 `origin_diagnose`（检查安装 / COM 注册 / 残留进程 / 导出目录权限）。Origin 没开时插件会自动拉起它 |
| 中文路径/中文列名乱码 | 已内置处理（编码自动探测）。若仍异常，把文件移到纯英文路径再试 |
| 感觉慢 | ①让 AI 用 `origin_figure` 一次画完 ②先跑 `origin_warmup` 预热 ③项目里图页太多（>200）时跑 `origin_pages_gc`——实测 793 页会让某些操作慢 30 倍 |
| Origin 卡在弹窗 | 插件有看门狗会自动点掉；真卡死时手动关掉对话框重试 |
| AI 说某个功能不支持 | 是**真实的**不支持，不是它不会用。完整清单见 [COMPATIBILITY.md](COMPATIBILITY.md)（15 条实测过的失败场景） |

---

## 下一步

- 全部能力与工具清单：[README.md](README.md)
- 英文说明：[README.en.md](README.en.md)
- 已知限制与替代方案：[COMPATIBILITY.md](COMPATIBILITY.md)
- 版本历史：[CHANGELOG.md](CHANGELOG.md)
