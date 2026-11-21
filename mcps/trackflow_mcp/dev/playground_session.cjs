// Sesión real en MCP Playground (https://mcpplaygroundonline.com/mcp-test-server) con Chromium headless.
// Conecta el servidor MCP por su URL PÚBLICA (Codespaces), con un bearer token de corta duración, ejecuta
// incidents_get_ticket y un intento de escritura en inventario, y guarda capturas de cada paso.
// El token solo se escribe en el campo de contraseña del Playground (enmascarado) y nunca en disco;
// el script comprueba que no aparezca en el texto visible de ninguna pantalla.
//
//   PG_URL=https://$CODESPACE_NAME-8090.app.github.dev/mcp PG_TOKEN=$(~/.config/trackflow-dev/playground-token.sh) \
//   PG_OUT=docs/pr-evidence/mcp-server/playground PLAYWRIGHT_MODULE=$PWD/uis/backoffice/node_modules/playwright \
//   CHROMIUM_PATH=~/.cache/ms-playwright/chromium-1187/chrome-linux/chrome node mcps/trackflow_mcp/dev/playground_session.cjs
const fs = require('fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const token = process.env.PG_TOKEN, out = process.env.PG_OUT || 'playground-evidence';
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const texts = {};
  const shot = async (name) => { await page.screenshot({ path: out + '/' + name + '.png' }); texts[name] = await page.evaluate(() => document.body.innerText); };
  await page.goto('https://mcpplaygroundonline.com/mcp-test-server', { waitUntil: 'networkidle', timeout: 60000 });
  const dismiss = page.getByRole('button', { name: 'Dismiss' }); if (await dismiss.count()) await dismiss.first().click().catch(()=>{});
  await page.getByPlaceholder('https://your-mcp-server.com/mcp').fill(process.env.PG_URL);
  await page.getByPlaceholder('Bearer your-token-here').fill('Bearer ' + token);
  await shot('01-formulario-url-publica');
  await page.getByRole('button', { name: 'Connect to Server' }).click();
  await page.getByText('incidents_get_ticket').first().waitFor({ timeout: 30000 });
  await page.waitForTimeout(1500);
  await shot('02-conectado-5-tools');
  // Tool 1: consulta del ticket real
  await page.getByText('incidents_get_ticket').first().click();
  await page.getByPlaceholder('Enter incident_id...').fill('d32a9b66-b81b-5dce-b90f-c4e8bfc27161');
  await page.getByRole('button', { name: 'Run Tool' }).click();
  await page.waitForTimeout(6000);
  const resp = page.getByRole('button', { name: 'Response' }); if (await resp.count()) await resp.first().click().catch(()=>{});
  await page.waitForTimeout(1500);
  await shot('03-incidents_get_ticket-resultado');
  // Tool 2: intento de escritura sobre inventario
  await page.getByText('inventory_query_products').first().click();
  await page.waitForTimeout(1500);
  const op = page.getByPlaceholder(/operation/i); await op.first().fill('update');
  const pid = page.getByPlaceholder(/product_id/i); if (await pid.count()) await pid.first().fill('1');
  await page.getByRole('button', { name: 'Run Tool' }).click();
  await page.waitForTimeout(6000);
  if (await resp.count()) await resp.first().click().catch(()=>{});
  await page.waitForTimeout(1500);
  await shot('04-inventario-escritura-rechazada');
  // Logs JSON-RPC del Playground
  const logs = page.getByRole('button', { name: /^Logs/ }); if (await logs.count()) { await logs.first().click(); await page.waitForTimeout(1500); await shot('05-logs-json-rpc'); }
  fs.writeFileSync(out + '/page-texts.json', JSON.stringify(texts, null, 2));
  const leaked = Object.entries(texts).filter(([k, v]) => v.includes(token.slice(0, 40)));
  console.log('TOKEN EN TEXTO VISIBLE:', leaked.map(([k]) => k));
  await browser.close();
})();
