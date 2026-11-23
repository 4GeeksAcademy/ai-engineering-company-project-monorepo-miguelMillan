// Verificación E2E de las notificaciones SSE de RFP: dos pestañas reales (dashboard y /rfps), subida hecha por «otra
// persona» y caída del API a mitad. Termina con código 1 si algo no se cumple.
//
//   UI_URL=… API_URL=… UI_EMAIL=… UI_PASSWORD=… OUT=docs/pr-evidence/rfp-sse \
//   STOP_API='…' START_API='…'            (comandos que tumban y levantan el API)
//   OFFLINE_CMD='… process request-1.pdf' (crea y clasifica un ticket mientras el API está caído, como haría el worker)
//   node scripts/rfp_live_smoke.cjs rfp-valida.pdf pitch-descartado.pdf
const fs = require('fs')
const path = require('path')
const { execSync } = require('child_process')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')

const [validPdf, pitchPdf] = process.argv.slice(2)
const { UI_URL, API_URL, UI_EMAIL, UI_PASSWORD, STOP_API, START_API, OFFLINE_CMD } = process.env
const out = process.env.OUT || 'rfp-sse-evidence'
const report = { steps: [] }
const started = Date.now()
function note(step, detail = {}) {
  report.steps.push({ t: `+${((Date.now() - started) / 1000).toFixed(1)}s`, step, ...detail })
  console.log(`+${((Date.now() - started) / 1000).toFixed(1)}s`, step, Object.keys(detail).length ? JSON.stringify(detail) : '')
}
function check(condition, what) {
  note(`${condition ? 'OK' : 'FALLO'}: ${what}`)
  if (!condition) throw new Error(what)
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--disable-dev-shm-usage'] })
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 } })
  const dashboard = await context.newPage()
  await dashboard.goto(`${UI_URL}/login`, { waitUntil: 'domcontentloaded' })
  await dashboard.fill('#auth-email', UI_EMAIL)
  await dashboard.fill('#auth-password', UI_PASSWORD)
  await dashboard.click('button[type=submit]')
  await dashboard.waitForURL((url) => !url.pathname.startsWith('/login'))
  await dashboard.goto(`${UI_URL}/`, { waitUntil: 'domcontentloaded' })
  const list = await context.newPage()
  const requests = []
  list.on('request', (r) => { const u = new URL(r.url()); if (u.pathname.startsWith('/rfp-intake')) requests.push(u.pathname + u.search) })
  await list.goto(`${UI_URL}/rfps`, { waitUntil: 'domcontentloaded' })
  const pages = [dashboard, list]
  const state = (page) => page.locator('[data-testid=rfp-live-status]').getAttribute('data-state')
  const allLive = async (timeout) => {
    for (const page of pages) await page.locator('[data-testid=rfp-live-status][data-state=live]').waitFor({ timeout })
  }
  await allLive(30000)
  await list.locator('[data-testid=rfp-row]').first().waitFor({ timeout: 60000 })
  await list.waitForTimeout(8000) // deja terminar la carga inicial de la lista
  for (const page of pages) await page.evaluate(() => { window.__sinRecarga = true })
  const token = await list.evaluate(() => localStorage.getItem('trackflow.accessToken'))
  const fullLists = () => requests.filter((r) => r === '/rfp-intake/tickets').length
  const upload = async (file) => {
    const form = new FormData()
    form.append('file', new Blob([fs.readFileSync(file)], { type: 'application/pdf' }), path.basename(file))
    const response = await fetch(`${API_URL}/rfp-intake/tickets`, { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form })
    return (await response.json()).ticket_id
  }
  const notification = (page, id) => page.locator(`[data-testid=rfp-notification][data-ticket="${id}"]`)
  const row = (id) => list.locator(`[data-testid=rfp-row][data-ticket="${id}"]`)

  // 1. Tiempo real: sube otra persona; primero un documento que se descarta.
  let lists = fullLists()
  const pitch = await upload(pitchPdf)
  const live = await upload(validPdf)
  note('subidas por otra persona', { descartable: pitch, rfp: live })
  for (const page of pages) await notification(page, live).waitFor({ timeout: 120000 })
  await row(live).waitFor()
  check(await row(live).locator('[data-testid=rfp-row-new]').count() === 1, 'la RFP nueva aparece en /rfps marcada «Nueva» y en el dashboard, en vivo')
  check((await Promise.all(pages.map((p) => notification(p, pitch).count()))).every((n) => n === 0), 'el documento descartado no notifica')
  check(fullLists() === lists, 'ningún evento volvió a pedir la lista completa')
  await dashboard.screenshot({ path: path.join(out, '01-dashboard-nueva-rfp.png') })
  await list.screenshot({ path: path.join(out, '02-rfps-nueva-fila.png') })

  // 2. Caída del API: backoff progresivo.
  execSync(STOP_API, { stdio: 'ignore', shell: '/bin/bash' })
  note('API caído')
  await list.locator('[data-testid=rfp-live-status][data-state=reconnecting]').waitFor({ timeout: 60000 })
  const missed = execSync(OFFLINE_CMD, { shell: '/bin/bash' }).toString().match(/"ticket_id": "([0-9a-f-]{36})"/)[1]
  note('ticket creado y clasificado con el API caído', { ticket_id: missed })
  const attempts = []
  const until = Date.now() + 20000
  while (Date.now() < until) {
    const label = await list.locator('[data-testid=rfp-live-status]').innerText()
    if (attempts.at(-1) !== label) attempts.push(label)
    await list.waitForTimeout(200)
  }
  note('reintentos vistos en la UI', { attempts })
  check(attempts.length >= 2 && await state(list) === 'reconnecting', 'reintenta con espera creciente mientras sigue caído')
  await list.screenshot({ path: path.join(out, '03-reconectando.png') })

  // 3. Recuperación: lo perdido llega solo, una vez.
  lists = fullLists()
  execSync(START_API, { stdio: 'ignore', shell: '/bin/bash' })
  note('API levantado')
  await allLive(120000)
  for (const page of pages) await notification(page, missed).waitFor({ timeout: 30000 })
  await row(missed).waitFor()
  await list.waitForTimeout(6000)
  for (const id of [live, missed]) {
    const counts = [await row(id).count(), ...await Promise.all(pages.map((p) => notification(p, id).count()))]
    check(counts.every((n) => n === 1), `ticket ${id.slice(0, 8)}: una fila y un aviso por pestaña tras reconectar`)
  }
  check(fullLists() === lists, 'la recuperación usó Last-Event-ID, sin volver a pedir la lista')
  check((await Promise.all(pages.map((p) => p.evaluate(() => window.__sinRecarga === true)))).every(Boolean), 'ninguna página se recargó')
  check(await list.locator('[data-testid=rfp-error]').count() === 0, 'no queda en pantalla el error de la caída')
  await list.screenshot({ path: path.join(out, '04-recuperado-sin-duplicar.png') })

  // 4. Navegar recarga la página: sigue sin duplicar, y lo descartado no vuelve.
  await notification(list, live).getByRole('button', { name: /Descartar aviso/ }).click()
  await list.reload({ waitUntil: 'domcontentloaded' })
  await list.locator('[data-testid=rfp-live-status][data-state=live]').waitFor({ timeout: 30000 })
  await row(missed).waitFor({ timeout: 60000 })
  await list.waitForTimeout(4000)
  check(await notification(list, live).count() === 0 && await notification(list, missed).count() === 1, 'tras recargar, lo descartado no reaparece y lo no leído se conserva')
  fs.writeFileSync(path.join(out, 'verificacion.json'), JSON.stringify(report, null, 2))
  await browser.close()
})().catch((error) => { console.error(error); process.exit(1) })
