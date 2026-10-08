import { useState, type FormEvent } from 'react'
import { Plus, RotateCcw } from 'lucide-react'
import { categories, categoryLabels, errorMessage, positiveRate, supplierRequest } from '../utils/suppliers'
import type { Category, Country, Supplier, SupplierInput, SupplierStatus } from '../utils/suppliers'

const inputClass = 'mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800'
const buttonClass = 'inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:opacity-50'

export function SupplierForm({ onCreated }: { onCreated: (supplier: Supplier) => void }) {
  const [country, setCountry] = useState<Country>('USA')
  const [selectedCategories, setSelectedCategories] = useState<Category[]>([])
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    setError('')
    try {
      const name = String(data.get('name') ?? '').trim()
      if (!name) throw new Error('Introduce el nombre comercial del proveedor.')
      if (!selectedCategories.length) throw new Error('Selecciona al menos una categoría de producto o servicio.')
      const payload: SupplierInput = {
        name, country, categories: selectedCategories,
        rate_per_shipment: positiveRate(String(data.get('rate_per_shipment'))),
        currency: country === 'USA' ? 'USD' : 'EUR',
        status: data.get('status') as SupplierStatus,
        service_zone: String(data.get('service_zone') ?? '').trim() || null,
        contact_email: String(data.get('contact_email') ?? '').trim() || null,
        notes: String(data.get('notes') ?? '').trim() || null,
      }
      setPending(true)
      const created = await supplierRequest<Supplier>('', { method: 'POST', body: JSON.stringify(payload) })
      form.reset()
      onCreated(created)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setPending(false)
    }
  }

  return (
    <section aria-labelledby="supplier-create-title" className="border-y border-slate-200 bg-white py-6">
      <h3 id="supplier-create-title" className="font-display text-lg font-semibold">Nuevo proveedor</h3>
      <form onSubmit={submit} onReset={() => { setCountry('USA'); setSelectedCategories([]); setError('') }} className="mt-4 space-y-5">
        <fieldset disabled={pending} className="grid min-w-0 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <legend className="sr-only">Datos del contrato</legend>
          <div><label htmlFor="supplier-name" className="text-sm font-semibold">Nombre comercial *</label><input id="supplier-name" name="name" required className={inputClass} /></div>
          <div><label htmlFor="supplier-country" className="text-sm font-semibold">País *</label><select id="supplier-country" name="country" value={country} onChange={(event) => setCountry(event.target.value as Country)} className={inputClass}><option value="USA">USA</option><option value="Spain">Spain</option></select></div>
          <div><label htmlFor="supplier-currency" className="text-sm font-semibold">Moneda del contrato</label><input id="supplier-currency" name="currency" value={country === 'USA' ? 'USD' : 'EUR'} readOnly className={`${inputClass} bg-slate-50`} /></div>
          <div><label htmlFor="supplier-rate" className="text-sm font-semibold">Tarifa por envío / unidad *</label><input id="supplier-rate" name="rate_per_shipment" type="number" step="any" min="0" required className={inputClass} /></div>
          <div><label htmlFor="supplier-status" className="text-sm font-semibold">Estado *</label><select id="supplier-status" name="status" className={inputClass}><option value="active">Activo</option><option value="suspended">Suspendido</option></select></div>
          <div><label htmlFor="supplier-zone" className="text-sm font-semibold">Zona de cobertura</label><input id="supplier-zone" name="service_zone" className={inputClass} /></div>
          <div><label htmlFor="supplier-email" className="text-sm font-semibold">Email de contacto</label><input id="supplier-email" name="contact_email" type="email" className={inputClass} /></div>
          <div className="sm:col-span-2"><label htmlFor="supplier-notes" className="text-sm font-semibold">Notas</label><textarea id="supplier-notes" name="notes" rows={2} className={inputClass} /></div>
        </fieldset>
        <fieldset disabled={pending} aria-describedby={error ? 'supplier-create-error' : undefined} className="min-w-0">
          <legend className="text-sm font-semibold">Categorías de producto o servicio *</legend>
          <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {categories.map((category) => (
              <label key={category} htmlFor={`create-${category}`} className="flex items-start gap-2 text-sm">
                <input id={`create-${category}`} type="checkbox" name="categories" value={category} checked={selectedCategories.includes(category)} onChange={(event) => setSelectedCategories((previous) => event.target.checked ? [...previous, category] : previous.filter((item) => item !== category))} className="mt-1 accent-emerald-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800" />
                {categoryLabels[category]}
              </label>
            ))}
          </div>
        </fieldset>
        <p id="supplier-create-error" role="alert" className="text-sm text-red-800">{error}</p>
        <div className="flex flex-wrap gap-3">
          <button type="submit" disabled={pending} className={`${buttonClass} bg-emerald-800 text-white hover:bg-emerald-900`}><Plus size={16} aria-hidden="true" />{pending ? 'Registrando...' : 'Registrar proveedor'}</button>
          <button type="reset" disabled={pending} className={`${buttonClass} border border-slate-300 bg-white text-slate-700`}><RotateCcw size={16} aria-hidden="true" />Limpiar</button>
        </div>
      </form>
    </section>
  )
}