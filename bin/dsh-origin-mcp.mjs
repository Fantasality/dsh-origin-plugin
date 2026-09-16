#!/usr/bin/env node
// dsh-origin-mcp —— npx 启动器（v2.3.0 新增）
// ==============================================
// 让没有 DSH 的用户也能通过 npx 直接拉起 Origin MCP stdio 服务器：
//
//     npx -y dsh-origin-plugin            # 直接启动（stdio）
//     npx -y dsh-origin-plugin --doctor   # 环境自检
//     npx -y dsh-origin-plugin --print-config
//
// 启动器只做三件事：定位 Python、校验 Origin 相关依赖、以 stdio 方式
// 拉起 origin_mcp_stdio.py（全部工具与协议由 Python 侧实现）。
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const pluginDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const entry = path.join(pluginDir, 'origin_mcp_stdio.py')

function pickPython() {
  const candidates = []
  if (process.env.DSH_ORIGIN_PYTHON) candidates.push(process.env.DSH_ORIGIN_PYTHON)
  candidates.push(process.platform === 'win32' ? 'python' : 'python3', 'python')
  for (const cmd of candidates) {
    const r = spawnSync(cmd, ['--version'], { encoding: 'utf8' })
    if (r.status === 0) return cmd
  }
  return null
}

const py = pickPython()
if (!py) {
  console.error('[dsh-origin-mcp] 未找到 Python。请安装 Python 3.10+，',
    '或用环境变量 DSH_ORIGIN_PYTHON 指定解释器路径。')
  process.exit(1)
}
if (!existsSync(entry)) {
  console.error(`[dsh-origin-mcp] 入口缺失: ${entry}（npm 包不完整，请重装）`)
  process.exit(1)
}

// 依赖预检：缺依赖时给出一次性安装命令，不静默失败
const probe = spawnSync(py, ['-c', 'import mcp, originpro, win32com, numpy, openpyxl'],
  { encoding: 'utf8' })
if (probe.status !== 0) {
  console.error('[dsh-origin-mcp] 缺少 Python 依赖（mcp/originpro/pywin32/numpy/openpyxl）。')
  console.error(`请执行: "${py}" -m pip install -r ${path.join(pluginDir, 'requirements.txt')}`)
  console.error('（需本机已安装 OriginLab Origin；插件通过 COM 驱动它）')
  process.exit(1)
}

// 透传 stdio：MCP JSON-RPC 由 Python 侧同步主循环处理
const child = spawnSync(py, [entry, ...process.argv.slice(2)],
  { stdio: 'inherit', env: process.env })
process.exit(child.status ?? 0)
