/**
 * 方法 C（MCP 客户端）× 大学物理化学案例 × 方法 D 前置检查
 *
 * 用 DSH 同款 Node MCP SDK 连 origin_mcp_stdio.py，对四个物化案例各调一次
 * origin_figure（端到端一次出图），断言文件落盘且非空。
 */
import fs from 'node:fs';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';

// ---- SDK 定位（与 mcp_handshake_test.mjs 同逻辑：DSH Desktop 目录优先）----
function findSdk() {
  if (process.env.DSH_SDK) return process.env.DSH_SDK;
  const localAppData = process.env.LOCALAPPDATA ?? join(homedir(), 'AppData', 'Local');
  const candidates = [
    join(localAppData, 'DSH Desktop', 'dsh-desktop', 'node_modules', '@modelcontextprotocol', 'sdk', 'dist', 'esm'),
    join(localAppData, 'DSH Desktop', 'resources', 'app', 'node_modules', '@modelcontextprotocol', 'sdk', 'dist', 'esm'),
  ];
  for (const c of candidates) {
    if (existsSync(join(c, 'client', 'index.js'))) return c;
  }
  throw new Error('找不到 @modelcontextprotocol/sdk，请用 DSH_SDK 指定 dist/esm 路径');
}
const SDK_DIR = findSdk();
const { Client } = await import(pathToFileURL(join(SDK_DIR, 'client', 'index.js')).href);
const { StdioClientTransport } = await import(pathToFileURL(join(SDK_DIR, 'client', 'stdio.js')).href);

const PY = String.raw`D:\workbuddyworkspace\compare\_chem_venv\Scripts\python.exe`;
const ENTRY = String.raw`D:\workbuddyworkspace\compare\dsh-origin-plugin\origin_mcp_stdio.py`;
const OUT = String.raw`D:\workbuddyworkspace\compare\_chem_out`;
const R = 8.314;

const cases = {
  boyle: (() => {
    const v = Array.from({ length: 19 }, (_, i) => 0.01 + 0.005 * i);
    const cols = { V_m3: v };
    for (const T of [300, 400, 500]) cols[`P_${T}K_kPa`] = v.map(v => +(1 * R * T / v / 1000).toFixed(4));
    return { cols, x: 'V_m3', y: ['P_300K_kPa', 'P_400K_kPa', 'P_500K_kPa'], type: 'line', label: '理想气体等温线族' };
  })(),
  beer: {
    cols: { c_mmol_L: [0, 2, 4, 6, 8, 10], absorbance: [0.0008, 0.3068, 0.6073, 0.9143, 1.2188, 1.5228] },
    x: 'c_mmol_L', y: ['absorbance'], type: 'scatter', label: '朗伯-比尔标准曲线'
  },
  titration: (() => {
    const v = [], ph = [];
    for (let x = 0; x <= 50; x += 0.5) {
      v.push(+x.toFixed(2));
      if (x < 24.9) ph.push(+(-Math.log10(0.1 * (25 - x) / (25 + x))).toFixed(3));
      else if (x > 25.1) ph.push(+(14 + Math.log10(0.1 * (x - 25) / (25 + x))).toFixed(3));
      else ph.push(7.0);
    }
    return { cols: { V_NaOH_mL: v, pH: ph }, x: 'V_NaOH_mL', y: ['pH'], type: 'line_symbol', label: '酸碱滴定曲线' };
  })(),
  arrhenius: (() => {
    const Ea = 50000, A = 1e7, Ts = [300, 310, 320, 330, 340];
    return {
      cols: {
        'invT_K-1': Ts.map(T => +(1 / T).toFixed(8)),
        ln_k: Ts.map(T => +Math.log(A * Math.exp(-Ea / (R * T))).toFixed(5))
      },
      x: 'invT_K-1', y: ['ln_k'], type: 'scatter', label: '阿伦尼乌斯图'
    };
  })(),
};

const transport = new StdioClientTransport({ command: PY, args: ['-X', 'utf8', ENTRY] });
const client = new Client({ name: 'four-ways-test', version: '1.0.0' });
await client.connect(transport);

const tools = await client.listTools();
console.log(`tools/list -> ${tools.tools.length} 个工具`);
if (tools.tools.length < 70) throw new Error(`工具数 ${tools.tools.length} < 70`);

const pass = [], fail = [];
for (const [name, c] of Object.entries(cases)) {
  const out = `${OUT}\\C_${name}.png`;
  const res = await client.callTool({
    name: 'origin_figure',
    arguments: {
      columns: c.cols, x_column: c.x, y_columns: c.y, plot_type: c.type,
      fmt: 'png', file_path: out, title: c.label
    }
  });
  const body = JSON.parse(res.content[0].text);
  const good = body.ok && fs.existsSync(out) && fs.statSync(out).size > 0;
  (good ? pass : fail).push(name);
  console.log(`[${good ? 'OK ' : 'FAIL'}] C ${name}（${c.label}） ${good ? fs.statSync(out).size + 'B' : body.error}`);
}

await client.close();
console.log(`\nFOUR-WAYS(C): ${pass.length} passed, ${fail.length} failed`);
process.exit(fail.length ? 1 : 0);
