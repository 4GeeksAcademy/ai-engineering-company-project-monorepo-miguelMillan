import { useEffect, useState } from 'react'
import { Plus, RefreshCw, X } from 'lucide-react'
import { SupplierForm } from '../../components/SupplierForm'
import { SupplierRow } from '../../components/SupplierRow'
import { categories, categoryLabels, errorMessage, supplierRequest } from '../../utils/suppliers'
import type { Category, Country, Supplier } from '../../utils/suppliers'

const controlClass = 'rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800'

export function SupplierDirectory() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [country, setCountry] = useState<Country | ''>('')
  const [category, setCategory] = useState<Category | ''>('')
  const [showForm, setShowForm] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams()
    if (country) params.set('country', country)
    if (category) params.set('category', category)
    supplierRequest<Supplier[]>(`?${params}`, { signal: controller.signal })
      .then((records) => { if (!controller.signal.aborted) setSuppliers(records) })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(errorMessage(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [country, category, revision])

  function startLoading() {
    setLoading(true)
    setError('')
  }

  function refresh() {
    startLoading()
    setRevision((previous) => previous + 1)
  }

  function created(supplier: Supplier) {
    setShowForm(false)
    setNotice(`${supplier.name} registrado correctamente.`)
    refresh()
  }

  function changed(supplier: Supplier) {
    setSuppliers((previous) => previous.map((item) => item.id === supplier.id ? supplier : item))
    setNotice(`${supplier.name} actualizado correctamente.`)
  }

  return (
    <section aria-labelledby="supplier-directory-title" className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="supplier-directory-title" className="font-display text-2xl font-bold">Directorio de proveedores</h2>
        <button type="button" aria-expanded={showForm} aria-controls="supplier-registration" onClick={() => setShowForm(!showForm)} className="inline-flex items-center gap-2 rounded-md bg-emerald-800 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800">{showForm ? <X size={16} aria-hidden="true" /> : <Plus size={16} aria-hidden="true" />}{showForm ? 'Cerrar formulario' : 'Nuevo proveedor'}</button>
      </header>
      <p role="status" className="text-sm text-emerald-900">{notice}</p>
      <div id="supplier-registration">{showForm && <SupplierForm onCreated={created} />}</div>
      <form onSubmit={(event) => event.preventDefault()} className="flex flex-wrap items-end gap-4" aria-label="Filtros de proveedores">
        <div><label htmlFor="filter-country" className="mb-1 block text-sm font-semibold">País</label><select id="filter-country" value={country} onChange={(event) => { startLoading(); setCountry(event.target.value as Country | '') }} className={controlClass}><option value="">Todos los países</option><option value="USA">USA</option><option value="Spain">Spain</option></select></div>
        <div className="min-w-0"><label htmlFor="filter-category" className="mb-1 block text-sm font-semibold">Categoría de producto</label><select id="filter-category" value={category} onChange={(event) => { startLoading(); setCategory(event.target.value as Category | '') }} className={`${controlClass} max-w-full`}><option value="">Todas las categorías</option>{categories.map((item) => <option key={item} value={item}>{categoryLabels[item]}</option>)}</select></div>
        <button type="button" title="Actualizar directorio" aria-label="Actualizar directorio" disabled={loading} onClick={refresh} className={`${controlClass} flex h-10 w-10 items-center justify-center p-0 disabled:opacity-50`}><RefreshCw size={16} aria-hidden="true" /></button>
      </form>
      <div role="status" className="text-sm text-slate-600">{loading ? 'Cargando proveedores...' : !error && `${suppliers.length} proveedores`}</div>
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      {!loading && !error && (suppliers.length ? (
        <div role="region" aria-label="Listado de proveedores" tabIndex={0} className="overflow-x-auto border-y border-slate-200 focus-visible:outline-2 focus-visible:outline-emerald-800">
          <table className="w-full min-w-[860px] border-collapse text-left">
            <caption className="sr-only">Proveedores de TrackFlow por país, categoría, tarifa y estado</caption>
            <thead className="bg-slate-200/70 text-xs uppercase text-slate-700"><tr>{['Proveedor', 'País', 'Categorías', 'Tarifa por envío / unidad', 'Estado'].map((heading) => <th scope="col" key={heading} className="px-4 py-3 font-semibold">{heading}</th>)}</tr></thead>
            <tbody>{suppliers.map((supplier) => <SupplierRow key={supplier.id} supplier={supplier} onChanged={changed} />)}</tbody>
          </table>
        </div>
      ) : <p className="border-y border-slate-200 py-8 text-sm text-slate-600">No hay proveedores que coincidan con los filtros.</p>)}
    </section>
  )
}