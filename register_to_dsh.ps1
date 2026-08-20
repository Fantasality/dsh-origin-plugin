# register_to_dsh.ps1
# 把 Origin 画图插件注册到 DSH（v2.0.4 同步传输版）：
#   1) MCP 插件条目写入 profile patch（幂等，自动备份）
#   2) origin-plotting skill 安装到 DSH skill 根目录（幂等）
# 用法:  powershell -ExecutionPolicy Bypass -File register_to_dsh.ps1
# 卸载:  运行 unregister_from_dsh.ps1，或手动删除 cordis.patch.yml 中
#         "# --- dsh-origin-plugin" 到 "# --- end dsh-origin-plugin" 段。
# 注意:  必须用 UTF-8 编码写回（本脚本用 .NET WriteAllText，避免编码损坏）。
#
# v2.0.2：写 config-only 覆盖而非完整 insert —— bundle 安装时 DSH 已自动注册
#   mcp-origin（默认系统 python + 自定位 server 路径）。这里只用同一 id 把
#   command/args 覆盖成你本机独立 venv 的绝对路径（层级叠加，不会重复）。
#   旧版完整 insert 与 bundle 层撞成两条 mcp-origin 会触发 DSH 启动失败：
#   duplicate loader entry id: mcp-origin。已在此版本彻底移除该风险。
# v2.0.4：args 加 -u + env 加 PYTHONUNBUFFERED=1（配合同步传输，双重 flush 保险）；
#   修正路径：venv 在 %USERPROFILE%\dsh_origin_plugin\.venv（非 dsch_）；
#   server 指向已安装 bundle 的 node_modules 路径；profile patch 路径改为
#   %USERPROFILE%\.dsh\profiles\web\cordis.patch.yml（当前 DSH Desktop 布局）。

$ErrorActionPreference = "Stop"

$dshHome      = Join-Path $env:USERPROFILE ".dsh"
$profilePatch  = Join-Path $dshHome "profiles\web\cordis.patch.yml"
$venvPython    = Join-Path $env:USERPROFILE "dsh_origin_plugin\.venv\Scripts\python.exe"
$serverScript  = Join-Path $dshHome "profiles\web\node_modules\dsh-origin-plugin\origin_mcp_server.py"
$skillFile     = Join-Path $dshHome "profiles\web\node_modules\dsh-origin-plugin\skills\origin-plotting\SKILL.md"

if (-not (Test-Path $venvPython)) { throw "未找到 $venvPython —— 请先创建 venv 并安装依赖（见 README）" }
if (-not (Test-Path $serverScript)) { throw "未找到 $serverScript —— 请先在 DSH 插件市场安装 dsh-origin-plugin" }
if (-not (Test-Path $profilePatch)) { throw "未找到 profile patch: $profilePatch" }
if (-not (Test-Path $skillFile)) { throw "未找到 skill 文件: $skillFile（bundle 是否完整安装？）" }

# ---------- 1) MCP 插件条目（幂等；config-only 覆盖，避免 duplicate id） ----------
$content = [System.IO.File]::ReadAllText($profilePatch, [System.Text.Encoding]::UTF8)
if ($content -match "mcp-origin") {
    Write-Host "[1/2] patch 已包含 mcp-origin，跳过写入。"
} else {
    $bak = "$profilePatch.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item $profilePatch $bak
    Write-Host "[1/2] 已备份原配置 -> $bak"

    $block = @"

# --- dsh-origin-plugin (Origin 画图 MCP 服务器；config-only 覆盖 bundle 默认条目) ---
# 由 register_to_dsh.ps1 写入；卸载时删除本段即可。
# 注意：这是 config-only 覆盖（不带 name、不带 insert），与 bundle 层的 mcp-origin
# 合并；覆盖会整体替换 config，因此必须带上 serverName/transport 等完整字段。
# 请勿改写成完整 insert（会导致 duplicate loader entry id: mcp-origin 启动失败）。
- id: mcp-origin
  config:
    serverName: origin
    transport: stdio
    command: '$($venvPython -replace "\\", "/")'
    args: ['-u', '-X', 'utf8', '$($serverScript -replace "\\", "/")']
    env:
      PYTHONIOENCODING: utf-8
      PYTHONUNBUFFERED: '1'
    failOnStartupError: false
    toolCallTimeoutMs: 120000
# --- end dsh-origin-plugin ---
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($profilePatch, $content + "`r`n" + $block, $utf8NoBom)
    Write-Host "[1/2] 已写入 mcp-origin 的 config-only 覆盖 -> $profilePatch"
}

# ---------- 2) origin-plotting skill（幂等，装到 DSH skill 根目录） ----------
$skillRoot = Join-Path $dshHome "skills"
$dst = Join-Path $skillRoot "origin-plotting\SKILL.md"
New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
Copy-Item $skillFile $dst -Force
Write-Host "[2/2] skill 已安装 -> $dst"

Write-Host ""
Write-Host "完成。下一步：重启 Harness（菜单 Harness -> Restart Harness 或 Ctrl+Shift+R）。"
Write-Host "验证：新建对话说 用 Origin 画 y=x^2 折线图并导出 PNG，模型加载 origin-plotting skill 后即可直接画图。"
