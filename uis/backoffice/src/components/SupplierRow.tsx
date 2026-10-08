import { useState, type FormEvent } from 'react'
import { Save } from 'lucide-react'
import { categoryLabels, errorMessage, positiveRate, supplierRequest } from '../utils/suppliers'
import type { Supplier } from '../utils/suppliers'

export function SupplierRow({ supplier, onChanged }: { supplier: Supplier; onChanged: (supplier: Supplier) => void }) {
  const [rate, setRate] = useState(String(supplier.rate_per_shipment))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const active = supplier.status === 'active'

  async function update(kind: 'rate' | 'status') {
    setError('')
    try {
      const payload = kind === 'rate'
        ? { rate_per_shipment: positiveRate(rate) }
        : { status: active ? 'suspended' : 'active' }
      setPending(true)
      const updated = await supplierRequest<Supplier>(`/${supplier.id}/${kind}`, { method: 'PATCH', body: JSON.stringify(payload) })
      setRate(String(updated.rate_per_shipment))
      onChanged(updated)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setPending(false)
    }
  }

  function saveRate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void update('rate')
  }

  return (
    <tr className={`border-b border-slate-200 align-top ${active ? 'bg-white' : 'bg-amber-50/60'}`} aria-busy={pending}>
      <th scope="row" className="max-w-64 px-4 py-4 text-left font-normal">
        <p className="font-semibold">{supplier.name}</p>
        {supplier.service_zone && <p className="mt-1 text-xs text-slate-600">{supplier.service_zone}</p>}
        {supplier.contact_email && <a href={`mailto:${supplier.contact_email}`} className="mt-1 block break-all text-xs text-emerald-800 underline focus-visible:outline-2">{supplier.contact_email}</a>}
        {supplier.notes && <p className="mt-2 text-xs text-slate-600">{supplier.notes}</p>}
      </th>
      <td className="px-4 py-4 text-sm">{supplier.country}</td>
      <td className="max-w-56 px-4 py-4 text-sm"><ul className="space-y-1">{supplier.categories.map((category) => <li key={category}>{categoryLabels[category]}</li>)}</ul></td>
      <td className="px-4 py-4">
        <form onSubmit={saveRate} className="flex items-center gap-2">
          <label htmlFor={`rate-${supplier.id}`} className="sr-only">Tarifa de {supplier.name}</label>
          <input id={`rate-${supplier.id}`} type="number" step="any" min="0" required value={rate} onChange={(event) => setRate(event.target.value)} disabled={pending} className="w-28 rounded-md border border-slate-300 bg-white px-2 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800" />
          <span className="text-xs font-semibold">{supplier.currency}</span>
          <button type="submit" title={`Guardar tarifa de ${supplier.name}`} aria-label={`Guardar tarifa de ${supplier.name}`} disabled={pending || Number(rate) === supplier.rate_per_shipment} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-slate-300 bg-white text-emerald-800 hover:bg-emerald-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:opacity-40"><Save size={16} aria-hidden="true" /></button>
        </form>
        <time dateTime={supplier.updated_at} className="mt-2 block text-xs text-slate-500">{new Date(supplier.updated_at).toLocaleString('es-ES')}</time>
        <p role="alert" className="mt-1 max-w-64 text-xs text-red-800">{error}</p>
      </td>
      <td className="px-4 py-4">
        <span className={`inline-block rounded px-2 py-1 text-xs font-semibold ${active ? 'bg-emerald-100 text-emerald-900' : 'bg-amber-100 text-amber-900'}`}>{active ? 'Activo' : 'Suspendido'}</span>
        <label htmlFor={`status-${supplier.id}`} className="mt-3 flex items-center gap-2 whitespace-nowrap text-xs font-medium">
          <input id={`status-${supplier.id}`} type="checkbox" role="switch" checked={active} disabled={pending} onChange={() => void update('status')} aria-label={`Proveedor activo: ${supplier.name}`} className="h-4 w-4 accent-emerald-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800" />
          {active ? 'Suspender' : 'Activar'}
        </label>
      </td>
    </tr>
  )
}