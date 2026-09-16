// mcp_handshake_test.mjs
// 用 DSH 同款 @modelcontextprotocol/sdk (Node) 作为客户端，真实 spawn 本插件的
// MCP server，验证：
//   1) 协议版本握手（DSH 的 dsh-mcp-client 用同一个 SDK）
//   2) listTools 能看到全部 origin 工具（>=35，含 v2.1 新工具）
//   3) callTool(origin_status) 成功且带 capabilities 能力握手
//   4) callTool(origin_plot_plan) 离线计划流走通
// 运行:
//   node smoke/mcp_handshake_test.mjs
// 可用环境变量覆盖（默认自动探测本机布局）：
//   DSH_SDK         @modelcontextprotocol/sdk 的 dist/esm 绝对路径
//   ORIGIN_VENV_PY  插件 venv 的 python.exe 绝对路径
//   ORIGIN_SERVER   origin_mcp_server.py 绝对路径
import { pathToFileURL } from 'node:url';
import { join, dirname } from 'node:path';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';

// ---- SDK 定位：DSH Desktop 应用目录的 node_modules 优先 ----
function findSdk() {
  if (process.env.DSH_SDK) return process.env.DSH_SDK;
  const localAppData = process.env.LOCALAPPDATA
    ?? join(homedir(), 'AppData', 'Local');
  const candidates = [
    join(localAppData, 'DSH Desktop', 'dsh-desktop', 'node_modules',
      '@modelcontextprotocol', 'sdk', 'dist', 'esm'),
    join(localAppData, 'DSH Desktop', 'resources', 'app', 'node_modules',
      '@modelcontextprotocol', 'sdk', 'dist', 'esm'),
  ];
  for (const c of candidates) {
    if (existsSync(join(c, 'client', 'index.js'))) return c;
  }
  throw new Error('找不到 @modelcontextprotocol/sdk，请用 DSH_SDK 指定 dist/esm 路径');
}

// ---- server / venv 定位 ----
const here = dirname(fileURLToPath(import.meta.url));          // <repo>/smoke
const repoRoot = dirname(here);
const SERVER = process.env.ORIGIN_SERVER
  ?? join(repoRoot, 'origin_mcp_server.py');
const VENV_PY = process.env.ORIGIN_VENV_PY
  ?? [
    join(homedir(), 'dsh_origin_plugin', '.venv', 'Scripts', 'python.exe'),
    join(dirname(dirname(fileURLToPath(import.meta.url))),
         '..', '_chem_venv', 'Scripts', 'python.exe'),
    join(repoRoot, '.venv', 'Scripts', 'python.exe'),
  ].find(existsSync)
  ?? join(homedir(), 'dsh_origin_plugin', '.venv', 'Scripts', 'python.exe');

const SDK = findSdk();
const { Client } = await import(pathToFileURL(join(SDK, 'client/index.js')).href);
const { StdioClientTransport } = await import(pathToFileURL(join(SDK, 'client/stdio.js')).href);

console.log('sdk    :', SDK);
console.log('python :', VENV_PY, existsSync(VENV_PY) ? '(ok)' : '(MISSING)');
console.log('server :', SERVER, existsSync(SERVER) ? '(ok)' : '(MISSING)');

const transport = new StdioClientTransport({
  command: VENV_PY,
  args: ['-X', 'utf8', SERVER],
  stderr: 'pipe',
});

const client = new Client({ name: 'dsh-handshake-test', version: '2.1.0' });
transport.onmessage = (msg) => {
  if (msg?.method === 'notifications/message' || msg?.params?.level === 'error') {
    console.log('[server-log]', JSON.stringify(msg).slice(0, 300));
  }
};

try {
  console.log('== connect (initialize 握手) ==');
  await client.connect(transport);
  console.log('connected OK');

  console.log('== listTools ==');
  const tools = await client.listTools();
  const names = tools.tools.map((t) => t.name).sort();
  console.log(`TOOLS(${names.length}):`, names.join(', '));
  if (names.length < 43) throw new Error(`工具数 ${names.length} < 43`);
  for (const must of ['origin_plot_file', 'origin_plot_plan', 'origin_execute_plan',
    'origin_plot_template', 'origin_verify_graph', 'origin_load_file',
    'origin_save_project', 'origin_export_delivery',
    'origin_list_pages', 'origin_inspect_graph', 'origin_edit_plot',
    'origin_edit_axis', 'origin_edit_legend', 'origin_edit_page',
    'origin_manage_pages', 'origin_add_text']) {
    if (!names.includes(must)) throw new Error(`缺少 ${must}`);
  }

  console.log('== callTool(origin_status) ==');
  const res = await client.callTool({ name: 'origin_status', arguments: {} });
  const text = res.content?.map((c) => c.text ?? '').join('') ?? '';
  console.log('STATUS:', text.slice(0, 300));
  if (!text.includes('"ok": true')) throw new Error('origin_status 未返回 ok');
  if (!text.includes('capabilities')) throw new Error('origin_status 缺少 capabilities');

  console.log('== callTool(origin_plot_plan) 离线计划流 ==');
  const planRes = await client.callTool({
    name: 'origin_plot_plan',
    arguments: {
      columns: { time_s: [1, 2, 3, 4], signal: [2, 4, 9, 16] },
      plot_type: 'line_symbol', title: 'handshake',
    },
  });
  const planText = planRes.content?.map((c) => c.text ?? '').join('') ?? '';
  const plan = JSON.parse(planText);
  if (!plan.ok || !plan.plan_id) throw new Error('origin_plot_plan 失败: ' + planText.slice(0, 200));
  console.log('plan_id:', plan.plan_id, '| roles:', JSON.stringify(plan.roles));

  console.log('HANDSHAKE-TEST OK');
  await client.close();
  process.exit(0);
} catch (e) {
  console.error('HANDSHAKE-TEST FAIL:', e?.stack || e);
  process.exit(1);
}
