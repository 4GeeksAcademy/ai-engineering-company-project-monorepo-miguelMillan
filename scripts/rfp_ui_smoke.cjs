// Smoke E2E de la recepción de RFP POR LA UI (uis/backoffice), con Chromium headless (Playwright).
// Inicia sesión, abre /rfps, sube los PDF por el formulario y espera a que la UI muestre el estado final de cada
// ticket (analizando → análisis completo | descartado). Guarda capturas de cada paso y del detalle de cada ticket.
//
//   UI_URL=http://127.0.0.1:5174 UI_EMAIL=… UI_PASSWORD=… OUT=docs/pr-evidence/rfp-intake \
//   PLAYWRIGHT_MODULE=$PWD/uis/backoffice/node_modules/playwright CHROMIUM_PATH=… \
//   node scripts/rfp_ui_smoke.cjs ruta/trackflow-request-1.pdf ruta/trackflow-request-2.pdf ruta/trackflow-request-3.pdf
const fs = require('fs')
const path = require('path')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')

;(async () => {
  const files = process.argv.slice(2)
  const out = process.env.OUT || 'rfp-ui-evidence'
  const base = process.env.UI_URL || 'http://127.0.0.1:5174'
  const timeoutMs = Number(process.env.WAIT_MS || 15 * 60 * 1000)
  fs.mkdirSync(out, { recursive: true })
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined })
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } })
  const log = (...a) => console.log(new Date().toISOString(), ...a)

  await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
  await page.fill('#auth-email', process.env.UI_EMAIL)
  await page.fill('#auth-password', process.env.UI_PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 })
  await page.goto(`${base}/rfps`, { waitUntil: 'networkidle' })
  await page.screenshot({ path: path.join(out, '01-rfps-antes-de-subir.png'), fullPage: true })
  const rowsBefore = (await page.$$('[data-testid=rfp-row]')).length

  await page.setInputFiles('input[name=rfp]', files)
  await page.click('button[type=submit]')
  await page.getByRole('status').filter({ hasText: 'RFP subida' }).waitFor({ timeout: 30000 })
  const uploadedAt = Date.now()
  await page.screenshot({ path: path.join(out, '02-subidas-analizando.png'), fullPage: true })
  log('subidas', files.length, 'RFP; esperando a que terminen los análisis…')

  // La UI refresca sola mientras haya tickets «Analizando». Solo cuentan los tickets de ESTA subida: las filas
  // nuevas aparecen arriba (orden por fecha de creación descendente).
  while (Date.now() - uploadedAt < timeoutMs) {
    const statuses = await page.$$eval('[data-testid=rfp-row] [data-testid=rfp-status]', (els) => els.map((e) => e.textContent))
    const mine = statuses.slice(0, Math.max(0, statuses.length - rowsBefore))
    log('estados de esta subida en la UI:', JSON.stringify(mine))
    if (mine.length >= files.length && mine.every((s) => !s.startsWith('Analizando'))) break
    await page.waitForTimeout(10000)
  }
  await page.screenshot({ path: path.join(out, '03-estados-finales.png'), fullPage: true })

  const rows = (await page.$$('[data-testid=rfp-row]')).slice(0, files.length)
  for (let i = 0; i < rows.length; i++) {
    const row = (await page.$$('[data-testid=rfp-row]'))[i]
    const name = (await row.$eval('td span', (e) => e.textContent)).replace(/\.pdf$/, '')
    await row.click()
    await page.waitForTimeout(2500)
    await page.screenshot({ path: path.join(out, `04-detalle-${name}.png`), fullPage: true })
    log('detalle', name)
  }
  await browser.close()
})()
