// Prueba de RED de las notificaciones SSE: un proxy TCP (5190) entre Chromium y el backoffice (5189) corta la conexión
// («cut») o deja de pasar bytes sin cerrar nada («hole»). Imprime detección, intentos, recuperación y duplicados.
//
// Usar el build (`npx vite preview --port 5189`, con AUTH_PROXY_TARGET al API): en `vite dev` el cliente HMR recarga la
// página al volver la red y enmascararía el resultado.
//   E=email P=contraseña PW=ruta/a/playwright CH=ruta/a/chrome TICKET_CMD='crea un ticket RFP e imprime su ticket_id' \
//   node scripts/rfp_live_network.cjs
const net = require('net'); const { execSync } = require('child_process'); const path = require('path')
const { chromium } = require(process.env.PW)
let mode = 'pass'; const socks = new Set()
net.createServer((c) => {
  if (mode === 'cut') { c.destroy(); return }
  const u = net.connect(5189, '127.0.0.1'); const pair = [c, u]; socks.add(pair)
  c.on('data', (d) => { if (mode !== 'hole') u.write(d) }); u.on('data', (d) => { if (mode !== 'hole') c.write(d) })
  const end = () => { socks.delete(pair); c.destroy(); u.destroy() }
  for (const s of pair) { s.on('error', end); s.on('close', end) }
}).listen(5190, '127.0.0.1')
const setMode = (m) => { mode = m; if (m === 'cut') for (const p of socks) p.forEach((s) => s.destroy()) }
const t0 = Date.now(); const log = (...a) => console.log('+' + ((Date.now() - t0) / 1000).toFixed(1) + 's', ...a)
const ticket = () => execSync(process.env.TICKET_CMD, { shell: '/bin/bash' }).toString().trim().split('\n').at(-1)
;(async () => {
  const b = await chromium.launch({ executablePath: process.env.CH, args: ['--disable-dev-shm-usage'] }); const p = await b.newPage()
  const sse = [], lists = []
  p.on('request', (r) => { const u = new URL(r.url()); if (u.pathname === '/rfp-intake/events') sse.push({ at: Date.now(), type: r.resourceType(), auth: (r.headers().authorization || '').slice(0, 7), last: r.headers()['last-event-id'] }); if (u.pathname === '/rfp-intake/tickets' && !u.search) lists.push(1) })
  await p.goto('http://127.0.0.1:5190/login'); await p.fill('#auth-email', process.env.E); await p.fill('#auth-password', process.env.P); await p.click('button[type=submit]')
  await p.waitForURL((u) => !u.pathname.startsWith('/login')); await p.goto('http://127.0.0.1:5190/rfps')
  const st = (s, t) => p.locator(`[data-testid=rfp-live-status][data-state=${s}]`).waitFor({ timeout: t })
  await st('live', 30000); await p.evaluate(() => { window.__x = 1 }); await p.waitForTimeout(3000)
  const ids = [ticket()]
  const note = (id) => p.locator(`[data-testid=rfp-notification][data-ticket="${id}"]`)
  await note(ids[0]).waitFor({ timeout: 10000 }); log('1. aviso automático', 'SSE:', JSON.stringify(sse.at(-1)))
  for (const m of ['cut', 'hole']) {
    const n = sse.length, l = lists.length; setMode(m); const at = Date.now(); log('--- red:', m)
    await st('reconnecting', 80000); log('detectado en', ((Date.now() - at) / 1000).toFixed(1), 's')
    ids.push(ticket(), ticket()); log('2 tickets creados sin red')
    const gaps = []; await p.waitForTimeout(m === 'cut' ? 12000 : 1000)
    for (let i = n + 1; i < sse.length; i++) gaps.push(((sse[i].at - sse[i - 1].at) / 1000).toFixed(1))
    log('intentos', sse.length - n, 'separación (s)', gaps.join(', '))
    setMode('pass'); const back = Date.now()
    if (m === 'hole') for (const pr of [...socks]) pr.forEach((s) => s.destroy())
    for (const id of ids) await note(id).waitFor({ timeout: 70000 })
    await st('live', 70000); await p.waitForTimeout(4000)
    const dup = []; for (const id of ids) dup.push(`${await p.locator(`[data-testid=rfp-row][data-ticket="${id}"]`).count()}/${await note(id).count()}`)
    log('recuperado; filas/avisos por ticket:', dup.join(' '), '| total avisos', await p.locator('[data-testid=rfp-notification]').count(), '| Last-Event-ID', sse.at(-1).last, '| listas pedidas', lists.length - l, '| recargó', !(await p.evaluate(() => window.__x === 1)))
  }
  log('todas las conexiones por fetch con Bearer:', sse.every((s) => s.type === 'fetch' && s.auth === 'Bearer '), sse.length)
  await b.close(); process.exit(0)
})().catch((e) => { console.error(String(e).slice(0, 500)); process.exit(1) })
