// Evidencia E2E del almacenamiento de telemetría (POST /telemetry/events → Supabase), con Chromium headless.
//  1. Evento de negocio REAL: registra una entrada de stock desde el backoffice. La UI emite `inbound_order_created`
//     con su cliente de telemetría (lotes, flush cada 10 s) y se captura el lote y la respuesta del endpoint.
//  2. Lote MIXTO enviado al mismo endpoint desde la misma sesión: 2 eventos válidos y 3 inválidos. Se espera
//     received=5, stored=2, rejected=3; los válidos se guardan aunque haya inválidos en el lote.
//
//   UI_URL=http://127.0.0.1:5174 UI_EMAIL=… UI_PASSWORD=… OUT=docs/pr-evidence/telemetry-storage \
//   PLAYWRIGHT_MODULE=$PWD/uis/backoffice/node_modules/playwright SKU=CLT-SNK-W-42-Z \
//   node scripts/telemetry_storage_smoke.cjs
const fs = require('fs')
const path = require('path')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')

;(async () => {
  const out = process.env.OUT || 'telemetry-evidence'
  const base = process.env.UI_URL || 'http://127.0.0.1:5174'
  const sku = process.env.SKU || 'CLT-SNK-W-42-Z'
  fs.mkdirSync(out, { recursive: true })
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined })
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } })
  const log = (...a) => console.log(new Date().toISOString(), ...a)

  // Todos los lotes que la UI envía al endpoint de telemetría, con su respuesta.
  const batches = []
  page.on('response', async (response) => {
    const request = response.request()
    if (request.method() !== 'POST' || !new URL(response.url()).pathname.endsWith('/telemetry/events')) return
    batches.push({ status: response.status(), request: JSON.parse(request.postData() || '{}'), response: await response.json().catch(() => null) })
  })

  await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
  await page.fill('#auth-email', process.env.UI_EMAIL)
  await page.fill('#auth-password', process.env.UI_PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 })

  // 1. Evento de negocio real desde el formulario de entradas de stock.
  await page.goto(`${base}/backoffice/inventory/orders/inbound`, { waitUntil: 'networkidle' })
  const option = page.locator('#sku-id option', { hasText: sku })
  await page.selectOption('#sku-id', await option.getAttribute('value'))
  await page.fill('#inbound-quantity', '3')
  await page.fill('#inbound-reference', `PO-TEL-EVID-${Date.now()}`)
  await page.click('button[type=submit]')
  await page.getByRole('status').filter({ hasText: 'Entrada de stock registrada' }).waitFor({ timeout: 30000 })
  await page.screenshot({ path: path.join(out, '01-entrada-de-stock-registrada.png'), fullPage: true })
  log('entrada registrada; esperando el flush del lote de telemetría (≤ 10 s)…')

  const deadline = Date.now() + 30000
  let business
  while (!business && Date.now() < deadline) {
    business = batches.find((b) => b.request.events?.some((e) => e.event_type === 'inbound_order_created'))
    if (!business) await page.waitForTimeout(1000)
  }
  if (!business) throw new Error('La UI no envió inbound_order_created en 30 s')
  fs.writeFileSync(path.join(out, 'business-event-batch.json'), JSON.stringify(business, null, 2))
  log('lote con evento de negocio:', JSON.stringify(business.response))

  // 2. Lote mixto: mismo endpoint y misma sesión que usa la UI.
  const mixed = await page.evaluate(async () => {
    const now = new Date().toISOString()
    const sessionId = crypto.randomUUID()
    const base = { timestamp: now, sessionId, userId: null, schemaVersion: '1.0.0' }
    const ev = (eventType, properties, extra = {}) => ({ eventId: crypto.randomUUID(), requestId: crypto.randomUUID(), ...base, event_type: eventType, properties, ...extra })
    const events = [
      ev('backoffice_section_viewed', { section: 'inventory_inbound' }),
      ev('frontend_retry_requested', { section: 'inventory_inbound' }),
      // Inválido: almacén sin normalizar («LA» en vez de «los_angeles») → lo rechaza el JSON Schema.
      ev('inbound_order_created', { warehouse: 'LA', client_id: 'purestep_footwear', product_id: 'CLT-SNK-W-42-Z', product_category: 'fashion', quantity: 3, order_id: 1 }),
      // Inválido: tipo de evento fuera de la allowlist → lo rechaza el modelo Pydantic.
      ev('pedido_creado', { section: 'inventory_inbound' }),
      // Inválido: userId con PII (un email) en vez del hash u_<sha256> → rechazado.
      ev('backoffice_section_viewed', { section: 'inventory_inbound' }, { userId: 'cliente@example.com' }),
    ]
    const response = await fetch('/telemetry/events', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events }) })
    return { status: response.status, request: { events }, response: await response.json() }
  })
  mixed.expected = { valid: mixed.request.events.slice(0, 2).map((e) => e.eventId), invalid: mixed.request.events.slice(2).map((e) => e.eventId) }
  fs.writeFileSync(path.join(out, 'mixed-batch.json'), JSON.stringify(mixed, null, 2))
  log('lote mixto:', mixed.status, JSON.stringify(mixed.response))
  await browser.close()
})().catch((error) => { console.error(error); process.exit(1) })
