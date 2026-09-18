/**
 * issue #1 冒烟：apply() 返回值必须被 cordis 判为合法 effect（或 nullable）。
 *
 * 用 **DSH 内置的真实 cordis** 加载插件，跑一遍 apply()，看 cordis 是否接受。
 * 修补前：apply() 返回普通对象 → cordis 抛 TypeError: Invalid effect（DSH 起不来）。
 * 修补后：apply() 返回 undefined → cordis 判 OK。
 *
 * 运行:
 *   node smoke/issue1_apply_effect.mjs
 * 可用环境变量 DSH_CORDIS 指定 cordis 的 lib/index.js 路径。
 */
import { pathToFileURL } from 'node:url'
import { existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { homedir } from 'node:os'

const here = dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const repoRoot = join(here, '..')
// DSH_PLUGIN 可指向任意 index.js（反向验证时指向"修补前"的坏副本）
const PLUGIN = process.env.DSH_PLUGIN || join(repoRoot, 'index.js')

function findCordis() {
  if (process.env.DSH_CORDIS) return process.env.DSH_CORDIS
  const localAppData = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
  const candidates = [
    join(localAppData, 'DSH Desktop', 'dsh-desktop', 'node_modules', '@deepseek-ai', 'cordis', 'lib', 'index.js'),
    join(localAppData, 'DSH Desktop', 'resources', 'app', 'node_modules', '@deepseek-ai', 'cordis', 'lib', 'index.js'),
  ]
  for (const c of candidates) if (existsSync(c)) return c
  throw new Error('找不到 @deepseek-ai/cordis，请用 DSH_CORDIS 指定 lib/index.js 路径')
}

const CORDIS = findCordis()
console.log('cordis   :', CORDIS)
console.log('plugin   :', PLUGIN)

const { Context } = await import(pathToFileURL(CORDIS).href)
const mod = await import(pathToFileURL(PLUGIN).href)
const plugin = mod.default

if (!plugin || typeof plugin.apply !== 'function') {
  console.error('[FAIL] 插件未导出 apply()')
  process.exit(1)
}

// 1) apply() 的返回值本身
const ctx = { effect: (fn) => { ctx.__effect = fn } }
let threw = false, ret
try {
  ret = plugin.apply(ctx)
} catch (e) {
  threw = true
  ret = e
}
console.log('\n--- apply() 直接调用 ---')
console.log('threw    :', threw)
console.log('returned :', ret === undefined ? 'undefined' : `plain object with keys [${Object.keys(ret).join(', ')}]`)

const retOk = !threw && ret === undefined
console.log(retOk ? '[OK ] 返回值是 undefined（cordis 视为 nullable）'
                  : '[FAIL] 返回值不是 undefined')

// 2) 兼容性：真实 cordis 能加载整个插件（等价于 DSH 启动时的挂载）
console.log('\n--- 真实 cordis 加载（兼容性）---')
let cordisOk = true, cordisErr = ''
try {
  const root = new Context()
  root.plugin(plugin)
} catch (e) {
  cordisOk = false
  cordisErr = `${e.name}: ${e.message}`
}
console.log('cordis verdict :', cordisOk ? 'OK' : `REJECTED -> ${cordisErr}`)
console.log(cordisOk ? '[OK ] cordis 能挂载该插件'
                     : `[FAIL] cordis 拒绝：${cordisErr}`)

// 说明：本机 cordis（4.0.2）对返回值的处理路径与 DSH 运行时加载插件的路径不同，
// 因此"是否被拒"不足以复现 issue。**真正的判据是 apply() 的返回值必须是
// nullable（undefined）** —— 与报告者在真实 DSH 上的验证一致：
//   修补前 plain object -> DSH 启动崩溃 TypeError: Invalid effect
//   修补后 undefined    -> DSH 正常启动，74 工具挂载
const pass = retOk && cordisOk
console.log(`\nISSUE1-SMOKE: ${pass ? 'PASS' : 'FAIL'}`)
process.exit(pass ? 0 : 1)
