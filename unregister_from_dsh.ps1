# unregister_from_dsh.ps1
# 从 DSH web profile 移除 Origin 画图 MCP 插件（自动备份）。

$ErrorActionPreference = "Stop"
$profilePatch = Join-Path $env:APPDATA "dsh-desktop\harness\profiles\web\cordis.patch.yml"
if (-not (Test-Path $profilePatch)) { throw "未找到 $profilePatch" }

$content = [System.IO.File]::ReadAllText($profilePatch, [System.Text.Encoding]::UTF8)
if ($content -notmatch "dsh-origin-plugin") {
    Write-Host "未发现 dsh-origin-plugin 段，无需卸载。"
    exit 0
}

$bak = "$profilePatch.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item $profilePatch $bak
Write-Host "已备份 -> $bak"

$pattern = "(?s)\r?\n# --- dsh-origin-plugin.*?# --- end dsh-origin-plugin ---\r?\n?"
$new = [System.Text.RegularExpressions.Regex]::Replace($content, $pattern, "`r`n")
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($profilePatch, $new, $utf8NoBom)
Write-Host "已移除 dsh-origin-plugin 段。重启 Harness 生效。"
