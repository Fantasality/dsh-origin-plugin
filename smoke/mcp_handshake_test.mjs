// mcp_handshake_test.mjs
// 用 DSH 自带的 @modelcontextprotocol/sdk (v1.30.0, Node) 作为客户端，
// 真实 spawn 本插件的 MCP server，验证：
//   1) 协议版本握手（DSH 的 dsh-mcp-client 用同一个 SDK）
//   2) listTools 能看到 5 个 origin 工具
//   3) callTool(origin_status) 成功
// 运行: node smoke/mcp_handshake_test.mjs
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const SDK = 'C:/Users/Admin/AppData/Local/Programs/DSH Desktop/resources/app/node_modules/@modelcontextprotocol/sdk/dist/esm';
const { Client } = await import(pathToFileURL(join(SDK, 'client/index.js')).href);
const { StdioClientTransport } = await import(pathToFileURL(join(SDK, 'client/stdio.js')).href);

const VENV_PY = 'C:/Users/Admin/dsch_origin_plugin/.venv/Scripts/python.exe';
const SERVER = 'C:/Users/Admin/dsch_origin_plugin/origin_mcp_server.py';

const transport = new StdioClientTransport({
  command: VENV_PY,
  args: ['-X', 'utf8', SERVER],
  stderr: 'pipe',
});

const client = new Client({ name: 'dsh-handshake-test', version: '1.0.0' });
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
  console.log('TOOLS:', names.join(', '));
  if (!names.includes('origin_plot_file')) throw new Error('缺少 origin_plot_file');

  console.log('== callTool(origin_status) ==');
  const res = await client.callTool({ name: 'origin_status', arguments: {} });
  const text = res.content?.map((c) => c.text ?? '').join('') ?? '';
  console.log('STATUS:', text.slice(0, 400));
  if (!text.includes('"ok": true')) throw new Error('origin_status 未返回 ok');

  console.log('HANDSHAKE-TEST OK');
  await client.close();
} catch (e) {
  console.error('HANDSHAKE-TEST FAIL:', e?.stack || e);
  process.exit(1);
}
