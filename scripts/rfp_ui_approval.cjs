// E2E de las tres partes POR LA UI (uis/backoffice, /rfps) con Chromium headless (Playwright):
// sube una RFP → espera a que la Parte 3 pida aprobación → cada responsable decide desde el formulario de su
// departamento → (arbitraje si hay conflicto) → documento final. Guarda una captura por paso.
//
//   UI_URL=http://127.0.0.1:5174 UI_EMAIL=… UI_PASSWORD=… OUT=docs/pr-evidence/rfp-approval \
//   PLAYWRIGHT_MODULE=$PWD/uis/backoffice/node_modules/playwright CHROMIUM_PATH=… \
//   CAPACITY=4000 node scripts/rfp_ui_approval.cjs ruta/trackflow-request-2.pdf
//
// Con CAPACITY, Ana Whitfield compromete esa capacidad (pedidos/mes) al aprobar warehouse: si es menor que el volumen
// de la RFP dispara el arbitraje volume-vs-capacity (árbitro Miguel Torres) y lastmile vuelve a aprobación.
const fs = require('fs')
const path = require('path')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')

;(async () => {
  const pdf = process.argv[2]
  const out = process.env.OUT || 'rfp-approval-evidence'
  const base = process.env.UI_URL || 'http://127.0.0.1:5174'
  const capacity = process.env.CAPACITY
  const timeoutMs = Number(process.env.WAIT_MS || 40 * 60 * 1000)
  fs.mkdirSync(out, { recursive: true })
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--disable-dev-shm-usage'] })
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } })
  const log = (...a) => console.log(new Date().toISOString(), ...a)
  const shot = (name) => page.screenshot({ path: path.join(out, name), fullPage: true })
  const fileName = path.basename(pdf)

  await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
  await page.fill('#auth-email', process.env.UI_EMAIL)
  await page.fill('#auth-password', process.env.UI_PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 })
  await page.goto(`${base}/rfps`, { waitUntil: 'networkidle' })
  if (!process.env.SKIP_UPLOAD) {   // SKIP_UPLOAD=1 retoma el ticket más reciente de ese archivo
    await page.setInputFiles('input[name=rfp]', [pdf])
    await page.click('form button[type=submit]')
    await page.getByRole('status').filter({ hasText: 'RFP subida' }).waitFor({ timeout: 30000 })
  }
  const started = Date.now()
  log(process.env.SKIP_UPLOAD ? 'retomado' : 'subida', fileName)

  // Abre el detalle del ticket recién subido (la fila más reciente con ese archivo) y espera a una condición.
  async function openDetail() {
    for (let attempt = 1; ; attempt++) {
      try {   // la página refresca sola mientras hay trabajo: una navegación puede abortarse y se reintenta
        await page.goto(`${base}/rfps`, { waitUntil: 'domcontentloaded' })
        await page.locator('[data-testid=rfp-row]', { hasText: fileName }).first().click({ timeout: 15000 })
        await page.locator('section[aria-label^="Ticket "]').first().waitFor({ timeout: 15000 })
        await page.waitForTimeout(2500)
        return
      } catch (error) {
        if (attempt >= 5) throw error
        log('reintento de navegación:', error.message.split('\n')[0])
        await page.waitForTimeout(3000)
      }
    }
  }
  async function waitFor(description, predicate) {
    while (Date.now() - started < timeoutMs) {
      await openDetail()
      const status = await page.locator('section[aria-label^="Ticket "] [data-testid=rfp-status]').first().innerText()
      if (await predicate(status)) return status
      log(`esperando: ${description} · estado ${status}`)
      await page.waitForTimeout(15000)
    }
    throw new Error(`timeout: ${description}`)
  }
  const panel = (owner) => page.locator('[data-testid=rfp-approval]', { hasText: `Aprobación del responsable · ${owner}` })
  const panelStatus = (owner) => panel(owner).locator('[data-testid=rfp-approval-status]').innerText()
  async function approve(owner, commitments = {}) {
    const form = panel(owner)
    await form.locator('input[type=radio][value=approve]').check()
    for (const [name, value] of Object.entries(commitments)) await form.locator(`input[name=${name}]`).fill(String(value))
    await form.getByRole('button', { name: `Aprobar como ${owner}` }).click()
    log('decisión enviada:', owner, 'aprobar', JSON.stringify(commitments))
  }
  const waitingFor = async (owner) => (await panel(owner).count()) > 0 && (await panelStatus(owner)).startsWith('Esperando tu decisión')

  const done = (name) => fs.existsSync(path.join(out, name))
  // 1. Partes 1 y 2 en segundo plano hasta que la Parte 3 pide las aprobaciones (o retomar donde quedó).
  await waitFor('Parte 3 esperando a los responsables', async (s) => s.startsWith('Terminado')
    || ((s.startsWith('Esperando aprobación') && !s.includes('·')) && (await page.locator('[data-testid=rfp-approval]').count()) > 0))
  if (!done('01-esperando-aprobacion.png')) await shot('01-esperando-aprobacion.png')
  const owners = (await page.locator('[data-testid=rfp-approval] h4').allInnerTexts()).map((o) => o.split('· ')[1])
  log('responsables:', owners.join(' | '))
  const first = owners.includes('Carlos Vega') ? 'Carlos Vega' : owners[0]
  const arbitrated = async () => (await page.locator('[data-testid=rfp-arbitration]').count()) > 0

  // 2. Carlos Vega aprueba lastmile mientras warehouse sigue esperando (ramas independientes).
  if (await waitingFor(first) && !(await arbitrated())) {
    await approve(first)
    await waitFor(`${first} aprobado`, async () => (await panelStatus(first)).startsWith('Aprobada'))
    await shot('02-una-aprobada-otra-esperando.png')
  }
  // 3. El resto aprueba (Ana Whitfield con su capacidad comprometida, si se pidió).
  for (const owner of owners) {
    if (owner !== first && await waitingFor(owner)) {
      await approve(owner, owner === 'Ana Whitfield' && capacity ? { capacity_monthly_orders: capacity } : {})
    }
  }
  // 4. Arbitraje: si una sección vuelve a pedir decisión con una versión nueva, se aprueba.
  let status = await waitFor('documento final o nueva decisión tras arbitraje', async (s) => s.startsWith('Terminado')
    || ((await arbitrated()) && await waitingFor(first)))
  if (!status.startsWith('Terminado')) {
    await shot('03-arbitraje-nueva-version.png')
    await approve(first)
    status = await waitFor('documento final', async (s) => s.startsWith('Terminado'))
  }
  await shot('04-terminado-documento-final.png')
  await page.locator('[data-testid=rfp-trace] summary').click()
  await page.waitForTimeout(2500)
  await page.locator('[data-testid=rfp-trace]').screenshot({ path: path.join(out, '05-trazabilidad.png') })
  log('terminado en', Math.round((Date.now() - started) / 1000), 's')
  await browser.close()
})().catch((error) => { console.error(error); process.exit(1) })
