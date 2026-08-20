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

const pkg = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8'),
)

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

/** Cordis plugin entry: a no-op apply so the loader creates a fiber for
 *  dsh-origin-plugin, which lets the plugin market read it as "live/active"
 *  (verify.js L149: if (loaderLive) → state='live') instead of perpetually
 *  "restart to apply" (L162: if (inBundles && !loaderLive) → state='restart').
 *  The real tools are mounted by the mcp-origin insert in cordis.patch.yml. */
export default { ...plugin, apply() {} }
