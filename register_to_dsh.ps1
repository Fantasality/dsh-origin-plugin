# register_to_dsh.ps1
# 把 Origin 画图 MCP 插件注册到 DSH web profile（幂等，自动备份）。
# 用法:  powershell -ExecutionPolicy Bypass -File register_to_dsh.ps1
# 卸载:  运行 unregister_from_dsh.ps1，或手动删除 cordis.patch.yml 中
#         "# --- dsh-origin-plugin" 到 "# --- end dsh-origin-plugin" 段。
# 注意:  必须用 UTF-8 编码写回（本脚本用 .NET WriteAllText，避免编码损坏）。

$ErrorActionPreference = "Stop"

$pluginRoot   = Join-Path $env:USERPROFILE "dsch_origin_plugin"
$profilePatch = Join-Path $env:APPDATA "dsh-desktop\harness\profiles\web\cordis.patch.yml"
$venvPython   = Join-Path $pluginRoot ".venv\Scripts\python.exe"
$serverScript = Join-Path $pluginRoot "origin_mcp_server.py"

if (-not (Test-Path $venvPython)) { throw "未找到 $venvPython —— 请先创建 venv 并安装依赖（见 README）" }
if (-not (Test-Path $serverScript)) { throw "未找到 $serverScript" }
if (-not (Test-Path $profilePatch)) { throw "未找到 profile patch: $profilePatch" }

# 幂等：已有标记则跳过
$content = [System.IO.File]::ReadAllText($profilePatch, [System.Text.Encoding]::UTF8)
if ($content -match "dsh-origin-plugin") {
    Write-Host "已注册过 dsh-origin-plugin，跳过（如需重装请先 unregister）。"
    exit 0
}

# 备份（时间戳）
$bak = "$profilePatch.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item $profilePatch $bak
Write-Host "已备份原配置 -> $bak"

# 追加（以 UTF-8 无 BOM 写回，保证与 harness 解析兼容）
$block = @"

# --- dsh-origin-plugin (Origin 画图 MCP 服务器) ---
# 由 register_to_dsh.ps1 写入；卸载时删除本段即可。
- insert:
    - id: mcp-origin
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: origin
        transport: stdio
        command: '$($venvPython -replace "\\", "/")'
        args: ['-X', 'utf8', '$($serverScript -replace "\\", "/")']
        toolCallTimeoutMs: 120000
# --- end dsh-origin-plugin ---
"@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($profilePatch, $content + "`r`n" + $block, $utf8NoBom)

Write-Host "已写入 mcp-origin 条目 -> $profilePatch"
Write-Host "下一步：在 DSH Desktop 菜单 Harness -> Restart Harness（或 Ctrl+Shift+R）重启生效。"
Write-Host "验证：新建对话输入“用 Origin 画 y=x^2 折线图并导出 PNG”，模型应能调用 mcp__origin__origin_plot_file。"
