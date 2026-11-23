// Verificación E2E del chat por WebSocket con el agente real: streaming, interrupción, red cortada a mitad de respuesta,
// recarga y reinicio del API. Un proxy TCP (5190) entre Chromium y el backoffice (5189) corta la red.
// Usar el build (`npx vite preview --port 5189`, con AUTH_PROXY_TARGET al API): el HMR de `vite dev` recarga la página.
//
//   UI_EMAIL=… UI_PASSWORD=… PLAYWRIGHT_MODULE=… CHROMIUM_PATH=… OUT=docs/pr-evidence/chat-websocket \
//   STOP_API='…' START_API='…' node scripts/chat_live_smoke.cjs
const fs = require('fs')
const net = require('net')
const path = require('path')
const { execSync } = require('child_process')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')

const out = process.env.OUT || 'chat-evidence'
const UI = 'http://127.0.0.1:5190'
let cut = false
const pairs = new Set()
net.createServer((client) => {
  if (cut) { client.destroy(); return }
  const server = net.connect(5189, '127.0.0.1')
  const pair = [client, server]
  pairs.add(pair)
  client.pipe(server).pipe(client)
  const end = () => { pairs.delete(pair); client.destroy(); server.destroy() }
  for (const s of pair) { s.on('error', end); s.on('close', end) }
}).listen(5190, '127.0.0.1')
const setCut = (value) => { cut = value; if (value) for (const p of pairs) p.forEach((s) => s.destroy()) }

const started = Date.now()
const report = []
function note(step, detail) {
  report.push({ t: `+${((Date.now() - started) / 1000).toFixed(1)}s`, step, ...detail })
  console.log(report.at(-1).t, step, detail ? JSON.stringify(detail) : '')
}
function check(condition, what, detail) {
  note(`${condition ? 'OK' : 'FALLO'}: ${what}`, detail)
  if (!condition) throw new Error(what)
}
const NORMALIZE = (t) => t.replace('▍', '').trim()

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--disable-dev-shm-usage'] })
  const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } })
  const frames = []
  const urls = []
  page.on('websocket', (ws) => {
    if (!ws.url().includes('/chat/ws')) return
    urls.push(ws.url())
    ws.on('framereceived', (f) => frames.push({ dir: 'in', at: Date.now(), socket: urls.length, ...JSON.parse(f.payload) }))
    ws.on('framesent', (f) => { const e = JSON.parse(f.payload); frames.push({ dir: 'out', at: Date.now(), socket: urls.length, event: e.event, data: e.event === 'auth' ? {} : e.data }) })
  })
  const shot = (name) => page.screenshot({ path: path.join(out, name), fullPage: true })
  const answers = page.locator('[data-testid=chat-assistant]')
  const status = (index) => answers.nth(index).getAttribute('data-status')
  const text = async (index) => NORMALIZE(await answers.nth(index).locator('p').first().innerText())
  const live = (timeout) => page.locator('[data-testid=chat-status][data-state=live]').waitFor({ timeout })
  async function waitFor(what, predicate, timeout = 240000) {
    const limit = Date.now() + timeout
    while (Date.now() < limit) { if (await predicate()) return; await page.waitForTimeout(100) }
    throw new Error(`timeout: ${what}`)
  }
  const ask = async (message, button) => { await page.fill('#chat-input', message); await page.getByRole('button', { name: button }).click() }
  const streaming = (index, words) => waitFor(`${words} palabras en la respuesta ${index}`, async () => await answers.count() > index
    && await answers.nth(index).locator('p').count() > 0 && (await status(index)) === 'streaming' && (await text(index)).split(/\s+/).length >= words && !(await text(index)).startsWith('Pensando'))

  await page.goto(`${UI}/login`)
  await page.fill('#auth-email', process.env.UI_EMAIL)
  await page.fill('#auth-password', process.env.UI_PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'))
  await page.goto(`${UI}/knowledge`)
  await live(30000)
  await page.evaluate(() => { window.__sinRecarga = true })

  // 1. Token por token.
  await ask('¿Cuál es la política de devoluciones estándar y cuál es la ventana?', 'Enviar')
  await streaming(0, 3)
  const lengths = []
  while (await status(0) === 'streaming') { const n = (await text(0)).length; if (lengths.at(-1) !== n) lengths.push(n); await page.waitForTimeout(80) }
  const chunks = frames.filter((f) => f.event === 'token_chunk')
  check(await status(0) === 'completed' && lengths.length > 5, 'la respuesta crece en pantalla mientras llega', { token_chunk: chunks.length, longitudes_vistas: lengths.length, segundos: ((chunks.at(-1).at - chunks[0].at) / 1000).toFixed(1) })
  await shot('01-respuesta-en-vivo.png')

  // 2. Interrupción a mitad de respuesta.
  await ask('Explícame con detalle todas las tarifas de almacenamiento en Zaragoza y Los Ángeles', 'Enviar')
  await streaming(1, 6)
  await page.fill('#chat-input', 'espera, quiero hacer una devolución, no hablar de tarifas')
  await shot('02-antes-de-interrumpir.png')
  const pressed = Date.now()
  await page.getByRole('button', { name: 'Interrumpir y enviar' }).click()
  await waitFor('interrumpida', async () => (await status(1)) === 'interrupted', 10000)
  const partial = await text(1)
  const cutIndex = frames.findIndex((f) => f.event === 'generation_interrupted')
  note('interrumpida', { ms: frames[cutIndex].at - pressed, parcial: partial })
  await waitFor('respuesta redirigida', async () => await answers.count() === 3 && (await status(2)) !== 'streaming')
  check(await status(2) === 'completed' && await text(1) === partial && await status(1) === 'interrupted', 'el parcial sigue visible y marcado; la respuesta nueva es otro mensaje', { nueva: (await text(2)).slice(0, 160) })
  const after = frames.slice(cutIndex + 1)
  check(after.findIndex((f) => f.event === 'user_message' && f.dir === 'in') < after.findIndex((f) => f.event === 'token_chunk'), 'ningún token de la generación original después de generation_interrupted')
  await shot('03-interrumpida-y-redirigida.png')

  // 3. Red cortada a mitad de respuesta.
  await ask('¿Qué SLA de entrega hay en España y en Estados Unidos? Explícalo', 'Enviar')
  await streaming(3, 4)
  setCut(true)
  await page.locator('[data-testid=chat-status][data-state=reconnecting]').waitFor({ timeout: 10000 })
  const offline = await text(3)
  await page.waitForTimeout(6000)
  check(await text(3) === offline, 'sin red la conversación sigue visible', { intentos: urls.length })
  await shot('04-sin-conexion.png')
  setCut(false)
  await live(60000)
  await waitFor('fin de la respuesta tras reconectar', async () => (await status(3)) !== 'streaming')
  const snapshot = frames.filter((f) => f.event === 'session_snapshot').at(-1)
  const resumed = await text(3)
  check(await status(3) === 'completed' && resumed.startsWith(offline) && await answers.count() === 4, 'reconecta con el mismo session_id, rehidrata y la respuesta sigue donde iba', { mensajes_en_el_snapshot: snapshot.data.messages.length, antes: offline.length, al_final: resumed.length })
  check(new Set(urls.map((u) => new URL(u).searchParams.get('session_id'))).size === 1 && urls.every((u) => !u.includes('token=')), 'todas las conexiones usan el mismo session_id y el JWT no va en la URL', { conexiones: urls.length })
  check(await page.evaluate(() => window.__sinRecarga === true), 'sin recargar la página')

  // 4. Recargar.
  const before = await page.locator('[data-testid=chat-log]').innerText()
  await page.reload()
  await live(30000)
  await waitFor('hilo restaurado', async () => await answers.count() === 4, 10000)
  check(NORMALIZE(await page.locator('[data-testid=chat-log]').innerText()) === NORMALIZE(before) && await status(1) === 'interrupted', 'recargar restaura el hilo completo, con la interrumpida marcada')

  // 5. El API muere a mitad de respuesta.
  await ask('Explícame la cobertura de transportistas en España con detalle', 'Enviar')
  await streaming(4, 12)
  await page.waitForTimeout(3000)
  execSync(process.env.STOP_API, { stdio: 'ignore', shell: '/bin/bash' })
  await page.locator('[data-testid=chat-status][data-state=reconnecting]').waitFor({ timeout: 20000 })
  execSync(process.env.START_API, { stdio: 'ignore', shell: '/bin/bash' })
  await live(120000)
  check(await answers.count() === 5 && await status(4) === 'interrupted' && (await text(4)).length > 0, 'tras reiniciar el API, la respuesta a medias se conserva como interrumpida', { texto: (await text(4)).slice(0, 120) })
  await ask('¿Cuál es la ventana de devolución?', 'Enviar')
  await waitFor('sigue la conversación', async () => await answers.count() === 6 && (await status(5)) === 'completed')
  check(true, 'y la conversación continúa en el mismo hilo')
  await shot('05-tras-reiniciar-el-api.png')

  fs.writeFileSync(path.join(out, 'verificacion.json'), JSON.stringify(report, null, 2))
  const sample = [...frames.slice(0, 6), ...frames.slice(cutIndex - 3, cutIndex + 6)].map(({ dir, event, data }) => ({ dir, event, data: event === 'session_snapshot' ? { ...data, messages: `${data.messages.length} mensajes` } : data }))
  fs.writeFileSync(path.join(out, 'frames.json'), JSON.stringify(sample, null, 1))
  await browser.close()
  process.exit(0)
})().catch((error) => { console.error(error); process.exit(1) })
