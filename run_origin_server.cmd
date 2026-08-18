@echo off
rem ============================================================
rem  dsh-origin-plugin 启动器：优先用本目录 .venv，否则退回系统 python
rem  供 @deepseek-ai/dsh-mcp-client 以 stdio 方式启动
rem ============================================================
setlocal
set "PLUGIN_DIR=%~dp0"
if exist "%PLUGIN_DIR%.venv\Scripts\python.exe" (
  "%PLUGIN_DIR%.venv\Scripts\python.exe" -X utf8 "%PLUGIN_DIR%origin_mcp_server.py" %*
) else (
  python -X utf8 "%PLUGIN_DIR%origin_mcp_server.py" %*
)
exit /b %ERRORLEVEL%
