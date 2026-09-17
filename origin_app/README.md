# DSH Origin Bridge · Origin App 安装指南（三步，零改 mcp.json）

> 给**不会配 Python、不会改 mcp.json、不知道 MCP 是什么**的研究生/实验员：
> 把下面这个 App 装进 Origin，以后只要点一下按钮，就能让 AI 客户端（Cursor / Claude /
> Kimi Code / WorkBuddy 等）通过 MCP 驱动你本机的 Origin 画图。

本目录 `origin_app/` 是一个 **Origin App 源码包**。你不需要懂 Python，按三步走完就能用。
（更省事的一键生成见仓库根 `scripts/build_origin_app.py`，它会把文件夹放到正确位置并打印第 2 步要用的命令。）

---

## 第一步：把 App 文件夹放到 Origin 的 Apps 目录

Origin 只认固定目录里的 App。把整个 `origin_app/` 文件夹复制过去，并重命名为
`DSHOriginBridge`：

```
复制目标（AppData 下的 Origin Apps 目录）：
C:\Users\<你的用户名>\AppData\Local\OriginLab\Apps\DSHOriginBridge\
```

最简单：在仓库根目录跑（会自动复制并打印下一步命令）：

```bat
python scripts/build_origin_app.py
```

复制后，里面应至少包含这四个文件：`App.ini`、`launch.ogs`、`bridge_manager.py`、
`README.md`。

> 若你手动复制，请从文件管理器整文件夹复制，**不要只拖 `launch.ogs` 一个文件**——
> 缺 `bridge_manager.py` 按钮点了也没用。

---

## 第二步：在 Origin 里打包成 .opx（mkOPX）

打开 Origin，在底部 **Command Window**（命令窗口）输入（**路径必须用反斜杠 `\`**）：

```
mkOPX app:="DSHOriginBridge" opx:="C:\Users\<你的用户名>\AppData\Local\OriginLab\Apps\DSHOriginBridge.opx";
```

要点（踩过坑的都在这）：
- **反斜杠**：`C:\...` 用反斜杠。用正斜杠 `C:/...` 会让 `mkOPX` **卡死不动**，只能强关 Origin 重来。
- `app:=` 后面是第一步文件夹的名字（不含路径），`opx:=` 是输出 .opx 的完整路径。
- 回车后没报错、命令窗口回到可输入状态，就打包成功了。

---

## 第三步：拖进 Origin，点按钮

1. 把生成的 `DSHOriginBridge.opx` 从文件管理器**拖进 Origin 窗口**（或双击 .opx 安装）。
2. 在 Origin 顶部菜单 **Apps（Apps Gallery）** 里找到 **DSH Origin Bridge** 按钮。
3. 点一下 → 启动桥接（再点一下 → 停止）。状态会打印在 Command Window。

之后，在你的 AI 客户端（已按 `install.py` 或手动配好 `dsh-origin` MCP 服务器）里直接说
"用 Origin 画 xxx"，AI 就会通过桥接驱动 Origin。

> 桥接真正拉起的是插件根的 `origin_mcp_stdio.py`，并作为一个**脱离 Origin 界面线程的后台
> 进程（sidecar）**运行，所以 Origin 界面不会卡。进程 PID 记在
> `%LOCALAPPDATA%\OriginLab\dsh-origin\bridge.pid`，Origin 关掉时由按钮/退出清理逻辑回收。

---

## 常见坑（必看）

1. **反斜杠 vs 正斜杠**：`mkOPX` 的路径参数必须用反斜杠 `\`。正斜杠会让命令窗口卡死。
2. **"拖文件夹"无效**：把 `origin_app/` **文件夹**拖进 Apps Gallery 会被 Origin **静默接受但不生效**——
   按钮不会出现。必须先 `mkOPX` 打成 `.opx` 再拖 `.opx`，或把文件夹放在
   `%LOCALAPPDATA%\OriginLab\Apps\` 下（第一步的做法）让 Origin 直接识别源码 App。
3. **防火墙弹窗**：桥接起来后若走 HTTP transport（`ORIGIN_BRIDGE_TRANSPORT=http`），
   Windows 可能弹"是否允许 Python 访问网络"。**只勾"专用网络"**即可，它只监听
   `127.0.0.1`（本机回环），不会对外暴露。
4. **Python 依赖**：桥接侧需要 `mcp / originpro / pywin32` 等包（见仓库 `requirements.txt`）。
   若 Origin 内嵌 Python 没装这些，请设环境变量
   `ORIGIN_BRIDGE_PYTHON` 指向已装好依赖的解释器，例如：
   `D:\workbuddyworkspace\compare\_chem_venv\Scripts\python.exe`，再点按钮。
5. **按钮点了没反应**：先到 Command Window 看有没有报错；或手动跑
   `run -pyf "<App目录>\bridge_manager.py" --status` 看状态，再到
   `%LOCALAPPDATA%\OriginLab\dsh-origin\bridge.log` 查启动日志。

---

## 手动控制（不用按钮也行）

在 Origin Command Window 直接调 Python：

```
run -pyf "C:\Users\<你>\AppData\Local\OriginLab\Apps\DSHOriginBridge\bridge_manager.py" --start
run -pyf "C:\Users\<你>\AppData\Local\OriginLab\Apps\DSHOriginBridge\bridge_manager.py" --status
run -pyf "C:\Users\<你>\AppData\Local\OriginLab\Apps\DSHOriginBridge\bridge_manager.py" --stop
```

或在系统命令行（PowerShell / CMD）直接跑（需先 `cd` 到 App 目录或用绝对路径）：

```bat
python "C:\Users\<你>\AppData\Local\OriginLab\Apps\DSHOriginBridge\bridge_manager.py" --status
```
