import { expect, test } from '@playwright/test'

const baseUrl = process.env.TRACKFLOW_URL ?? 'http://localhost:5174'

test('API integrada: proxy, validacion y CRUD persistente', async ({ request }) => {
  const invalid = await request.post(`${baseUrl}/suppliers`, { data: {
    name: 'Invalid supplier', country: 'USA', categories: ['carrier_last_mile'],
    rate_per_shipment: 0, currency: 'USD', status: 'active',
  } })
  expect(invalid.status()).toBe(422)
  const created = await request.post(`${baseUrl}/suppliers`, { data: {
    name: `API E2E ${Date.now()}`, country: 'Spain', categories: ['carrier_last_mile', 'carrier_international'],
    rate_per_shipment: 3.5, currency: 'EUR', status: 'active',
  } })
  expect(created.status()).toBe(201)
  const supplier = await created.json()
  try {
    const filtered = await request.get(`${baseUrl}/suppliers?country=Spain&category=carrier_international`)
    expect((await filtered.json()).some((item: { id: number }) => item.id === supplier.id)).toBeTruthy()
    const rate = await request.patch(`${baseUrl}/suppliers/${supplier.id}/rate`, { data: { rate_per_shipment: 4.5 } })
    expect(rate.status()).toBe(200)
    const updated = await rate.json()
    expect(updated.rate_per_shipment).toBe(4.5)
    expect(updated.updated_at > supplier.updated_at).toBeTruthy()
    for (const status of ['suspended', 'active']) {
      const changed = await request.patch(`${baseUrl}/suppliers/${supplier.id}/status`, { data: { status } })
      expect(changed.status()).toBe(200)
      expect((await changed.json()).status).toBe(status)
    }
    const rejected = await request.patch(`${baseUrl}/suppliers/${supplier.id}/status`, { data: { status: 'inactive' } })
    expect(rejected.status()).toBe(422)
  } finally {
    expect((await request.delete(`${baseUrl}/suppliers/${supplier.id}`)).status()).toBe(204)
  }
  expect((await request.get(`${baseUrl}/suppliers/${supplier.id}`)).status()).toBe(404)
  const incidents = await request.post(`${baseUrl}/api/incidents/analyze`, {
    multipart: { file: { name: 'empty.csv', mimeType: 'text/csv', buffer: Buffer.from('') } },
  })
  expect(incidents.status()).toBe(422)
})

test('directorio: filtros, alta, tarifa, estado y responsive', async ({ page, request }, testInfo) => {
  const name = `Proveedor E2E ${Date.now()}`
  let createdId: number | undefined
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => consoleErrors.push(error.message))
  try {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(baseUrl)
    await page.getByRole('button', { name: 'Proveedores', exact: true }).click()
    await expect(page.getByRole('table')).toBeVisible()
    const initial = await request.get(`${baseUrl}/suppliers`)
    expect(initial.ok()).toBeTruthy()
    const records = await initial.json()
    await expect(page.locator('tbody tr')).toHaveCount(records.length)

    await page.getByLabel('País', { exact: true }).selectOption('Spain')
    await page.getByLabel('Categoría de producto', { exact: true }).selectOption('carrier_international')
    await expect(page.locator('tbody tr')).toHaveCount(1)
    await expect(page.getByRole('row', { name: /DHL Express España/ })).toBeVisible()
    await page.getByLabel('Categoría de producto', { exact: true }).selectOption('fleet_maintenance')
    await expect(page.getByText('No hay proveedores que coincidan con los filtros.')).toBeVisible()
    await page.getByLabel('País', { exact: true }).selectOption('')
    await page.getByLabel('Categoría de producto', { exact: true }).selectOption('')
    await expect(page.locator('tbody tr')).toHaveCount(records.length)
    await page.screenshot({ path: testInfo.outputPath('suppliers-desktop.png'), fullPage: true })

    await page.getByRole('button', { name: 'Nuevo proveedor', exact: true }).click()
    await page.getByLabel('Nombre comercial *', { exact: true }).fill('Limpiar prueba')
    await page.getByLabel('País *', { exact: true }).selectOption('Spain')
    await expect(page.getByLabel('Moneda del contrato')).toHaveValue('EUR')
    await page.getByRole('button', { name: 'Limpiar', exact: true }).click()
    await expect(page.getByLabel('Nombre comercial *', { exact: true })).toHaveValue('')
    await expect(page.getByLabel('Moneda del contrato')).toHaveValue('USD')
    await page.getByLabel('Nombre comercial *', { exact: true }).fill(name)
    await page.getByLabel('País *', { exact: true }).selectOption('Spain')
    await page.getByLabel('Tarifa por envío / unidad *', { exact: true }).fill('4.25')
    await page.getByRole('button', { name: 'Registrar proveedor', exact: true }).click()
    await expect(page.getByRole('alert').filter({ hasText: 'Selecciona al menos una categoría' })).toBeVisible()
    await page.getByLabel('Última milla', { exact: true }).check()
    await page.getByLabel('Transporte internacional', { exact: true }).check()
    await page.getByLabel('Email de contacto', { exact: true }).fill('operaciones@example.com')
    await page.getByLabel('Zona de cobertura', { exact: true }).fill('Aragón')
    await page.getByLabel('Notas', { exact: true }).fill('Contrato de prueba')
    await page.route('**/suppliers', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: 'Contrato rechazado por la API.' }) })
      } else await route.continue()
    })
    await page.getByRole('button', { name: 'Registrar proveedor', exact: true }).click()
    await expect(page.getByRole('alert').filter({ hasText: 'Contrato rechazado por la API.' })).toBeVisible()
    await page.unroute('**/suppliers')

    const creation = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith('/suppliers'))
    await page.getByRole('button', { name: 'Registrar proveedor', exact: true }).click()
    const response = await creation
    expect(response.status()).toBe(201)
    const created = await response.json()
    createdId = created.id
    expect(created.currency).toBe('EUR')
    expect(created.categories).toEqual(['carrier_last_mile', 'carrier_international'])
    const row = page.getByRole('row', { name: new RegExp(name) })
    await expect(row).toBeVisible()
    await page.getByLabel(`Tarifa de ${name}`, { exact: true }).fill('0')
    await page.getByRole('button', { name: `Guardar tarifa de ${name}`, exact: true }).click()
    await expect(row.getByRole('alert')).toHaveText('La tarifa debe ser un número mayor que cero.')
    await page.getByLabel(`Tarifa de ${name}`, { exact: true }).fill('6.75')
    await page.getByRole('button', { name: `Guardar tarifa de ${name}`, exact: true }).click()
    await expect(page.getByRole('button', { name: `Guardar tarifa de ${name}`, exact: true })).toBeDisabled()
    const updated = await (await request.get(`${baseUrl}/suppliers/${createdId}`)).json()
    expect(updated.rate_per_shipment).toBe(6.75)
    expect(updated.updated_at > created.updated_at).toBeTruthy()

    const toggle = page.getByRole('switch', { name: `Proveedor activo: ${name}`, exact: true })
    await toggle.uncheck()
    await expect(row.getByText('Suspendido', { exact: true })).toBeVisible()
    await expect(toggle).toBeEnabled()
    await toggle.check()
    await expect(row.getByText('Activo', { exact: true })).toBeVisible()
    await expect(toggle).toBeEnabled()

    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    await page.screenshot({ path: testInfo.outputPath('suppliers-mobile.png'), fullPage: true })
    await page.getByRole('button', { name: 'Nuevo proveedor', exact: true }).click()
    await expect(page.getByLabel('Nombre comercial *', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    await page.screenshot({ path: testInfo.outputPath('suppliers-mobile-form.png'), fullPage: true })
    expect(consoleErrors).toEqual([])
  } finally {
    if (createdId !== undefined) {
      expect((await request.delete(`${baseUrl}/suppliers/${createdId}`)).status()).toBe(204)
    }
  }
})