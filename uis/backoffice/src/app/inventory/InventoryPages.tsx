import { useEffect, useState } from 'react'
import { ArrowDownToLine, ArrowUpFromLine, CircleAlert, CircleCheck, RefreshCw } from 'lucide-react'
import { inventoryRequest } from '../../utils/inventory'
import type { InventoryOrder, SKU, StockEntryInput, StockExitInput, StockExitType } from '../../utils/inventory'

const controlClass = 'min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800'
const warehouseLabels = { LA: 'Los Ángeles (LA)', ZGZ: 'Zaragoza (ZGZ)' }
const inventoryPaths = {
  products: '/backoffice/inventory/products',
  inbound: '/backoffice/inventory/orders/inbound',
  outbound: '/backoffice/inventory/orders/outbound',
  orders: '/backoffice/inventory/orders',
} as const

function messageFrom(cause: unknown): string {
  return cause instanceof Error ? cause.message : 'No se pudo completar la solicitud de inventario.'
}

function useProducts() {
  const [products, setProducts] = useState<SKU[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    inventoryRequest<SKU[]>('/products', { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setProducts(items) })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(messageFrom(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [])

  return { products, loading, error }
}

function ProductPicker({ products, loading, error, value, onChange }: {
  products: SKU[]
  loading: boolean
  error: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div>
      <label htmlFor="sku-id" className="mb-1 block text-sm font-semibold">Producto (SKU)</label>
      <select id="sku-id" required value={value} onChange={(event) => onChange(event.target.value)} className={controlClass} disabled={loading || Boolean(error)}>
        <option value="">{loading ? 'Cargando productos...' : 'Selecciona un producto'}</option>
        {products.map((product) => <option key={product.id} value={product.id}>{product.name} · {product.sku} · {warehouseLabels[product.warehouse]}</option>)}
      </select>
      {error && <p role="alert" className="mt-2 text-sm text-red-800">{error}</p>}
      {!loading && !error && products.length === 0 && <p className="mt-2 text-sm text-slate-600">No hay SKU disponibles.</p>}
    </div>
  )
}

function InventoryNavigation({ path }: { path: string }) {
  const links = [
    [inventoryPaths.products, 'Productos'],
    [inventoryPaths.inbound, 'Entrada de stock'],
    [inventoryPaths.outbound, 'Salida de stock'],
    [inventoryPaths.orders, 'Historial de órdenes'],
  ] as const

  return (
    <nav aria-label="Secciones de inventario" className="flex flex-wrap gap-2 border-b border-slate-200 pb-4">
      {links.map(([href, label]) => <a key={href} href={href} aria-current={path === href ? 'page' : undefined} className={`rounded-md px-3 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 ${path === href ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-200'}`}>{label}</a>)}
    </nav>
  )
}

function PageHeader({ title, description }: { title: string; description: string }) {
  return <header><h2 className="font-display text-2xl font-bold">{title}</h2><p className="mt-2 text-sm text-slate-600">{description}</p></header>
}

function ProductsPage() {
  const { products, loading, error } = useProducts()

  return (
    <section aria-labelledby="inventory-products-title" className="space-y-5">
      <PageHeader title="Productos (SKU)" description="Stock actual por SKU y almacén." />
      {loading && <p role="status" className="text-sm text-slate-600">Cargando productos...</p>}
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      {!loading && !error && (products.length ? (
        <div role="region" aria-label="Productos de inventario" tabIndex={0} className="overflow-x-auto border-y border-slate-200 focus-visible:outline-2 focus-visible:outline-emerald-800">
          <table className="w-full min-w-[900px] border-collapse text-left">
            <caption className="sr-only">SKU, cliente, categoría, almacén y stock actual</caption>
            <thead className="bg-slate-200/70 text-xs uppercase text-slate-700"><tr>{['Producto / SKU', 'Cliente', 'Categoría', 'Almacén', 'Stock actual', 'Acciones'].map((heading) => <th scope="col" key={heading} className="px-4 py-3 font-semibold">{heading}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-200 bg-white">
              {products.map((product) => {
                // Stock bajo: cinco unidades o menos; saludable: más de cinco.
                const lowStock = product.current_stock <= 5
                return <tr key={product.id}>
                  <th scope="row" className="px-4 py-4 font-medium"><span className="block text-slate-900">{product.name}</span><span className="mt-1 block text-xs text-slate-600">{product.sku} · #{product.id}</span></th>
                  <td className="px-4 py-4 text-sm">{product.client_name}</td>
                  <td className="px-4 py-4 text-sm">{product.category}</td>
                  <td className="px-4 py-4 text-sm">{warehouseLabels[product.warehouse]}</td>
                  <td className="px-4 py-4"><span className={`inline-flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm font-semibold ${lowStock ? 'bg-amber-100 text-amber-950' : 'bg-emerald-100 text-emerald-950'}`}><span aria-hidden="true">{lowStock ? <CircleAlert size={16} /> : <CircleCheck size={16} />}</span>{product.current_stock} unidades <span className="sr-only">{lowStock ? 'Stock bajo' : 'Stock saludable'}</span></span></td>
                  <td className="px-4 py-4"><div className="flex flex-wrap gap-2">
                    <a href={`${inventoryPaths.inbound}?sku_id=${product.id}`} className="inline-flex min-h-10 items-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800"><ArrowDownToLine size={16} aria-hidden="true" />Entrada</a>
                    <a href={`${inventoryPaths.outbound}?sku_id=${product.id}`} className="inline-flex min-h-10 items-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800"><ArrowUpFromLine size={16} aria-hidden="true" />Salida</a>
                  </div></td>
                </tr>
              })}
            </tbody>
          </table>
        </div>
      ) : <p className="border-y border-slate-200 py-8 text-sm text-slate-600">No hay productos de inventario.</p>)}
    </section>
  )
}

function InboundOrderPage() {
  const { products, loading, error: productsError } = useProducts()
  const [skuId, setSkuId] = useState(() => new URLSearchParams(window.location.search).get('sku_id') ?? '')
  const [quantity, setQuantity] = useState('')
  const [reference, setReference] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const selectedProduct = products.find((product) => String(product.id) === skuId)

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedProduct) return
    setSubmitting(true)
    setError('')
    setNotice('')
    const body: StockEntryInput = { sku_id: selectedProduct.id, quantity: Number(quantity), reference: reference.trim(), warehouse: selectedProduct.warehouse }
    try {
      await inventoryRequest('/orders/inbound', { method: 'POST', body: JSON.stringify(body) })
      setSkuId('')
      setQuantity('')
      setReference('')
      setNotice('Entrada de stock registrada correctamente.')
    } catch (cause) {
      setError(messageFrom(cause))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="space-y-5">
      <PageHeader title="Registrar entrada de stock" description="Registra la recepción de mercancía en el almacén asociado al SKU." />
      {notice && <p role="status" className="border-l-4 border-emerald-700 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">{notice}</p>}
      {error && <p role="alert" className="border-l-4 border-red-700 bg-red-50 px-4 py-3 text-sm text-red-950">{error}</p>}
      <form onSubmit={submit} className="max-w-2xl space-y-5 rounded-md border border-slate-200 bg-white p-5">
        <ProductPicker products={products} loading={loading} error={productsError} value={skuId} onChange={(value) => { setSkuId(value); setError(''); setNotice('') }} />
        {selectedProduct && <p className="text-sm text-slate-600">Almacén receptor: <span className="font-semibold text-slate-900">{warehouseLabels[selectedProduct.warehouse]}</span></p>}
        <div><label htmlFor="inbound-quantity" className="mb-1 block text-sm font-semibold">Cantidad recibida</label><input id="inbound-quantity" type="number" min="1" step="1" required value={quantity} onChange={(event) => setQuantity(event.target.value)} className={controlClass} /></div>
        <div><label htmlFor="inbound-reference" className="mb-1 block text-sm font-semibold">Referencia</label><input id="inbound-reference" type="text" required value={reference} onChange={(event) => setReference(event.target.value)} placeholder="PO-2024-0098" className={controlClass} /></div>
        <button type="submit" disabled={submitting || loading || Boolean(productsError) || !products.length} className="min-h-11 rounded-md bg-emerald-800 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:cursor-not-allowed disabled:opacity-50">{submitting ? 'Registrando...' : 'Registrar entrada'}</button>
      </form>
    </section>
  )
}

function OutboundOrderPage() {
  const { products, loading, error: productsError } = useProducts()
  const [skuId, setSkuId] = useState(() => new URLSearchParams(window.location.search).get('sku_id') ?? '')
  const [quantity, setQuantity] = useState('')
  const [exitType, setExitType] = useState<StockExitType>('dispatch')
  const [trackingNumber, setTrackingNumber] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const selectedProduct = products.find((product) => String(product.id) === skuId)
  const exceedsStock = Boolean(selectedProduct && quantity && Number(quantity) > selectedProduct.current_stock)

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedProduct) return
    setSubmitting(true)
    setError('')
    setNotice('')
    const body: StockExitInput = {
      sku_id: selectedProduct.id,
      quantity: Number(quantity),
      exit_type: exitType,
      tracking_number: exitType === 'dispatch' ? trackingNumber.trim() : null,
      warehouse: selectedProduct.warehouse,
    }
    try {
      await inventoryRequest('/orders/outbound', { method: 'POST', body: JSON.stringify(body) })
      setSkuId('')
      setQuantity('')
      setExitType('dispatch')
      setTrackingNumber('')
      setNotice('Salida de stock registrada correctamente.')
    } catch (cause) {
      setError(messageFrom(cause))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="space-y-5">
      <PageHeader title="Registrar salida de stock" description="Registra un despacho o una pérdida confirmada del almacén asociado al SKU." />
      {notice && <p role="status" className="border-l-4 border-emerald-700 bg-emerald-50 px-4 py-3 text-sm text-emerald-950">{notice}</p>}
      <form onSubmit={submit} className="max-w-2xl space-y-5 rounded-md border border-slate-200 bg-white p-5">
        <ProductPicker products={products} loading={loading} error={productsError} value={skuId} onChange={(value) => { setSkuId(value); setError(''); setNotice(''); setQuantity('') }} />
        {selectedProduct && <p role="status" className="border-l-4 border-slate-400 bg-slate-50 px-4 py-3 text-sm">Stock actual en {warehouseLabels[selectedProduct.warehouse]}: <strong>{selectedProduct.current_stock} unidades</strong></p>}
        <div>
          <label htmlFor="outbound-quantity" className="mb-1 block text-sm font-semibold">Cantidad de salida</label>
          <input id="outbound-quantity" type="number" min="1" step="1" required value={quantity} onChange={(event) => { setQuantity(event.target.value); setError(''); setNotice('') }} aria-describedby={exceedsStock || error ? 'outbound-quantity-message' : undefined} aria-invalid={Boolean(error)} className={controlClass} />
          {exceedsStock && <p id="outbound-quantity-message" role="alert" className="mt-2 text-sm font-medium text-amber-900">La cantidad supera el stock actual. La API validará el stock disponible al registrar la salida.</p>}
          {error && <p id={exceedsStock ? undefined : 'outbound-quantity-message'} role="alert" className="mt-2 text-sm font-medium text-red-800">{error}</p>}
        </div>
        <div><label htmlFor="exit-type" className="mb-1 block text-sm font-semibold">Tipo de salida</label><select id="exit-type" value={exitType} onChange={(event) => { const next = event.target.value as StockExitType; setExitType(next); if (next === 'loss') setTrackingNumber(''); setError('') }} className={controlClass}><option value="dispatch">Despacho</option><option value="loss">Pérdida</option></select></div>
        {exitType === 'dispatch' && <div><label htmlFor="tracking-number" className="mb-1 block text-sm font-semibold">Número de seguimiento</label><input id="tracking-number" type="text" required value={trackingNumber} onChange={(event) => setTrackingNumber(event.target.value)} className={controlClass} /></div>}
        {productsError && <p role="alert" className="text-sm text-red-800">{productsError}</p>}
        <button type="submit" disabled={submitting || loading || Boolean(productsError) || !products.length} className="min-h-11 rounded-md bg-emerald-800 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:cursor-not-allowed disabled:opacity-50">{submitting ? 'Registrando...' : 'Registrar salida'}</button>
      </form>
    </section>
  )
}

function orderDirection(order: InventoryOrder): 'inbound' | 'outbound' {
  const movement = `${order.type ?? ''} ${order.order_type ?? ''} ${order.movement_type ?? ''} ${order.kind ?? ''}`.toLowerCase()
  if (movement.includes('inbound') || movement.includes('entry') || movement.includes('entrada')) return 'inbound'
  if (movement.includes('outbound') || movement.includes('exit') || movement.includes('salida')) return 'outbound'
  return order.exit_type || !order.reference ? 'outbound' : 'inbound'
}

function orderProductName(order: InventoryOrder): string {
  if (typeof order.sku === 'string') return order.sku
  return order.sku?.name ?? order.product?.name ?? order.sku_name ?? order.name ?? `SKU #${order.sku_id}`
}

function OrderHistoryPage() {
  const [orders, setOrders] = useState<InventoryOrder[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    inventoryRequest<InventoryOrder[]>('/orders', { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setOrders(items) })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(messageFrom(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [revision])

  return (
    <section aria-labelledby="inventory-orders-title" className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3"><PageHeader title="Historial de órdenes" description="Movimientos de stock de entrada y salida. Consulta de solo lectura." /><button type="button" title="Actualizar historial" aria-label="Actualizar historial" disabled={loading} onClick={() => { setError(''); setLoading(true); setRevision((value) => value + 1) }} className="flex h-11 w-11 items-center justify-center rounded-md border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:opacity-50"><RefreshCw size={17} aria-hidden="true" /></button></header>
      {loading && <p role="status" className="text-sm text-slate-600">Cargando órdenes...</p>}
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      {!loading && !error && (orders.length ? <div role="region" aria-label="Historial de órdenes de inventario" tabIndex={0} className="overflow-x-auto border-y border-slate-200 focus-visible:outline-2 focus-visible:outline-emerald-800">
        <table className="w-full min-w-[850px] border-collapse text-left">
          <caption className="sr-only">Historial de entradas y salidas con producto, cantidad, fecha y usuario</caption>
          <thead className="bg-slate-200/70 text-xs uppercase text-slate-700"><tr>{['Producto', 'Cantidad', 'Tipo de orden', 'Almacén', 'Fecha de creación', 'user_uuid'].map((heading) => <th scope="col" key={heading} className="px-4 py-3 font-semibold">{heading}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-200 bg-white">{orders.map((order) => {
            const direction = orderDirection(order)
            const date = new Date(order.created_at)
            return <tr key={`${direction}-${order.id}`}>
              <th scope="row" className="px-4 py-4 font-medium">{orderProductName(order)}<span className="mt-1 block text-xs text-slate-600">SKU #{order.sku_id}</span></th>
              <td className="px-4 py-4 text-sm">{order.quantity}</td>
              <td className="px-4 py-4"><span className={`inline-flex rounded-md px-2.5 py-1 text-xs font-semibold ${direction === 'inbound' ? 'bg-emerald-100 text-emerald-950' : 'bg-amber-100 text-amber-950'}`}>{direction === 'inbound' ? 'Entrada de stock' : `Salida de stock${order.exit_type ? ` · ${order.exit_type === 'dispatch' ? 'despacho' : 'pérdida'}` : ''}`}</span></td>
              <td className="px-4 py-4 text-sm">{warehouseLabels[order.warehouse]}</td>
              <td className="px-4 py-4 text-sm">{Number.isNaN(date.getTime()) ? order.created_at : date.toLocaleString('es-ES')}</td>
              <td className="px-4 py-4 font-mono text-xs">{order.user_uuid}</td>
            </tr>
          })}</tbody>
        </table>
      </div> : <p className="border-y border-slate-200 py-8 text-sm text-slate-600">No hay órdenes de inventario.</p>)}
    </section>
  )
}

export function InventoryPage({ path }: { path: string }) {
  return (
    <section className="space-y-6">
      <InventoryNavigation path={path} />
      {path === inventoryPaths.products && <ProductsPage />}
      {path === inventoryPaths.inbound && <InboundOrderPage />}
      {path === inventoryPaths.outbound && <OutboundOrderPage />}
      {path === inventoryPaths.orders && <OrderHistoryPage />}
      {!Object.values(inventoryPaths).includes(path as typeof inventoryPaths[keyof typeof inventoryPaths]) && <p role="alert" className="text-sm text-red-800">La página de inventario solicitada no existe.</p>}
    </section>
  )
}