// dsh-origin-plugin — bundle entry artifact
// ===========================================
// This is a *Python* MCP plugin: the actual server is origin_mcp_server.py,
// launched by DSH through the mcp-origin loader entry (cordis.patch.yml →
// @deepseek-ai/dsh-mcp-client, stdio).
//
// Why an index.js at all?  The DSH plugin market refuses packages that look
// "source-only" — a carrier bundle (patch mounts a client, ships no main/
// exports/index.js of its own) is indistinguishable from a checkout that still
// needs a build step, so it gets rejected with "nothing installable / 没有可
// 安装的内容". Providing a real entry artifact here makes the market recognize
// a prebuilt bundle (entryArtifactExists) and lets install complete WITHOUT
// needing build approval (allowBuilds).
//
// This module carries a no-op `apply` so the cordis loader creates a fiber
// for dsh-origin-plugin itself. Without it, the bundle has no fiber of its
// own (only the mcp-origin insert does), and the plugin market's installed-
// state check (verify.js → loaderLive) reads it as perpetually "restart to
// apply" even after a restart. The apply body is empty: this bundle's
// runtime value is in cordis.patch.yml (insert mcp-origin), not in JS code.

import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const pkg = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8'),
)

const pluginRoot = path.dirname(fileURLToPath(import.meta.url))

/** Static descriptor of this DSH bundle (kept in sync with package.json). */
export const plugin = {
  name: pkg.name,
  version: pkg.version,
  kind: 'python-mcp-bundle',
  server: 'origin_mcp_server.py',
  loader: 'mcp-origin (@deepseek-ai/dsh-mcp-client, stdio)',
  layout: '28 MCP tools · styled plots · inline preview · stats batch',
  note: 'Wired into DSH by cordis.patch.yml; main/exports exist so the plugin ' +
    'market recognizes a prebuilt artifact instead of a build-required checkout.',
}

export function describe() {
  return plugin
}

/** Cordis plugin entry: a no-op-ish apply so the loader creates a fiber for
 *  dsh-origin-plugin, which lets the plugin market read it as "live/active"
 *  (verify.js L149: if (loaderLive) → state='live') instead of perpetually
 *  "restart to apply" (L162: if (inBundles && !loaderLive) → state='restart').
 *  The real tools are mounted by the mcp-origin insert in cordis.patch.yml.
 *
 *  v2.3.0: register a ctx.effect so unloading the plugin releases engine-side
 *  resources (drains the Python COM task queue and stops its worker thread)
 *  instead of leaving them to the daemon reaper. We never kill Origin64.exe
 *  here — the user may still be using Origin by hand; residual-process cleanup
 *  is governed by DSH_ORIGIN_AUTOKILL / manual taskkill. */
export default {
  ...plugin,
  apply(ctx) {
    if (ctx && typeof ctx.effect === 'function') {
      ctx.effect(() => {
        // Fail-open best effort: ask the Python engine to shut down gracefully.
        // Any failure (no python on PATH, engine already gone) is non-fatal.
        try {
          const py = process.env.DSH_ORIGIN_PYTHON || 'python'
          spawnSync(py, ['-c',
            'import sys; sys.path.insert(0, r\'' + pluginRoot + '\'); '
            + 'import origin_engine as e; print(e.shutdown())'],
            { stdio: 'ignore', timeout: 8000 })
        } catch { /* unload must never throw */ }
      })
    }
    return { ...plugin }
  },
}
