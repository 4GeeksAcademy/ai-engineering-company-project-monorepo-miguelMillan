import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import { IncidentSummaryPanel } from './IncidentSummaryPanel'
import { branchLabels, categoryLabels, incidentButton, incidentControl, incidentError, incidentRequest, originLabels, statusLabels, transitions } from '../../utils/incidents'
import type { Incident, IncidentStatus } from '../../utils/incidents'

export function IncidentDirectory() {
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [filters, setFilters] = useState({ status: '', origin: '', branch: '' })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revision, setRevision] = useState(0)
  const [summaryRevision, setSummaryRevision] = useState(0)
  const [pending, setPending] = useState<Set<string>>(new Set())
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})
  const updating = useRef(new Set<string>())

  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value) })
    incidentRequest<Incident[]>(`?${params}`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setIncidents(result) })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(incidentError(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [filters, revision])

  function refresh() {
    setLoading(true)
    setError('')
    setRevision((value) => value + 1)
    setSummaryRevision((value) => value + 1)
  }

  async function changeStatus(incident: Incident, status: IncidentStatus) {
    if (updating.current.has(incident.id)) return
    updating.current.add(incident.id)
    setPending(new Set(updating.current))
    setRowErrors((previous) => ({ ...previous, [incident.id]: '' }))
    setNotice('')
    setIncidents((previous) => previous.map((item) => item.id === incident.id ? { ...item, status } : item))
    try {
      const saved = await incidentRequest<Incident>(`/${incident.id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) })
      setIncidents((previous) => previous.flatMap((item) => item.id !== saved.id ? [item] : filters.status && saved.status !== filters.status ? [] : [saved]))
      setNotice('Estado actualizado correctamente.')
      setSummaryRevision((value) => value + 1)
    } catch (cause) {
      setIncidents((previous) => previous.map((item) => item.id === incident.id ? incident : item))
      setRowErrors((previous) => ({ ...previous, [incident.id]: incidentError(cause) }))
    } finally {
      updating.current.delete(incident.id)
      setPending(new Set(updating.current))
    }
  }

  const hasFilters = Object.values(filters).some(Boolean)

  return (
    <section aria-labelledby="incident-directory-title" className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3"><h2 id="incident-directory-title" className="font-display text-2xl font-bold">Incidencias registradas</h2><button type="button" disabled={loading || !!pending.size} onClick={refresh} className={`${incidentButton} border border-slate-300 bg-white`}><RefreshCw size={16} aria-hidden="true" />Actualizar</button></header>
      <IncidentSummaryPanel revision={summaryRevision} />
      <form onSubmit={(event) => event.preventDefault()} aria-label="Filtros de incidencias">
        <fieldset disabled={!!pending.size} className="grid min-w-0 gap-4 sm:grid-cols-3">
          <legend className="sr-only">Filtrar incidencias</legend>
          {([['status', 'Estado', statusLabels], ['origin', 'Origen', originLabels], ['branch', 'Sede', branchLabels]] as const).map(([field, label, labels]) => <div key={field}><label htmlFor={`incidents-filter-${field}`} className="mb-2 block text-sm font-semibold">{label}</label><select id={`incidents-filter-${field}`} value={filters[field]} onChange={(event) => { setLoading(true); setError(''); setNotice(''); setFilters((previous) => ({ ...previous, [field]: event.target.value })) }} className={incidentControl}><option value="">{field === 'branch' ? 'Todas las sedes' : field === 'status' ? 'Todos los estados' : 'Todos los orígenes'}</option>{Object.entries(labels).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></div>)}
        </fieldset>
      </form>
      {notice && <p role="status" className="text-sm text-emerald-900">{notice}</p>}
      <section aria-label="Listado de incidencias" aria-busy={loading}>
        {loading ? <p role="status" className="flex items-center gap-2 py-6 text-sm text-slate-600"><LoaderCircle size={18} className="animate-spin" aria-hidden="true" />Cargando incidencias...</p> : error ? <div className="space-y-3 border-y border-red-200 py-6"><p role="alert" className="text-sm text-red-800">{error}</p><button type="button" onClick={refresh} className={`${incidentButton} border border-slate-300 bg-white`}><RefreshCw size={16} aria-hidden="true" />Reintentar listado</button></div> : !incidents.length ? <p role="status" className="border-y border-slate-200 py-8 text-sm text-slate-600">{hasFilters ? 'No hay incidencias que coincidan con los filtros seleccionados.' : 'Todavía no hay incidencias registradas.'}</p> : <>
          <p className="mb-3 text-sm text-slate-600">{incidents.length} incidencias</p>
          <div role="region" aria-label="Tabla de incidencias" tabIndex={0} className="overflow-x-auto border-y border-slate-200 focus-visible:outline-2 focus-visible:outline-emerald-800">
            <table className="w-full min-w-[850px] table-fixed text-left text-sm"><caption className="sr-only">Incidencias de TrackFlow y actualización de su estado</caption><thead className="bg-slate-200/70 text-slate-700"><tr><th scope="col" className="w-[30%] px-4 py-3">Incidencia</th><th scope="col" className="w-[20%] px-4 py-3">Categoría</th><th scope="col" className="w-[12%] px-4 py-3">Origen</th><th scope="col" className="w-[18%] px-4 py-3">Sede</th><th scope="col" className="w-[20%] px-4 py-3">Estado</th></tr></thead><tbody>
              {incidents.map((incident) => <tr key={incident.id} className="border-t border-slate-200 align-top"><td className="break-words px-4 py-4"><details><summary className="cursor-pointer font-semibold focus-visible:outline-2 focus-visible:outline-emerald-800">{incident.title}</summary><p className="mt-3 whitespace-pre-wrap">{incident.description}</p><dl className="mt-3 space-y-2 text-xs text-slate-600"><div><dt>ID</dt><dd className="break-all">{incident.id}</dd></div><div><dt>Creación (UTC)</dt><dd>{new Date(incident.created_at).toLocaleString('es-ES', { timeZone: 'UTC' })}</dd></div><div><dt>Última modificación (UTC)</dt><dd>{new Date(incident.updated_at).toLocaleString('es-ES', { timeZone: 'UTC' })}</dd></div></dl></details></td><td className="break-words px-4 py-4">{categoryLabels[incident.category]}</td><td className="px-4 py-4">{originLabels[incident.origin]}</td><td className="break-words px-4 py-4">{branchLabels[incident.branch]}</td><td className="px-4 py-4">
                <label htmlFor={`incident-status-${incident.id}`} className="sr-only">Estado de {incident.title}</label><select id={`incident-status-${incident.id}`} value={incident.status} disabled={pending.has(incident.id) || !transitions[incident.status].length} onChange={(event) => void changeStatus(incident, event.target.value as IncidentStatus)} aria-describedby={rowErrors[incident.id] ? `incident-status-error-${incident.id}` : undefined} className={incidentControl}><option value={incident.status}>{statusLabels[incident.status]}</option>{transitions[incident.status].map((status) => <option key={status} value={status}>{statusLabels[status]}</option>)}</select>
                {pending.has(incident.id) && <p role="status" className="mt-2 flex items-center gap-1 text-xs"><LoaderCircle size={14} className="animate-spin" aria-hidden="true" />Guardando...</p>}{!pending.has(incident.id) && !transitions[incident.status].length && <p className="mt-2 text-xs text-slate-600">Estado final</p>}{rowErrors[incident.id] && <p id={`incident-status-error-${incident.id}`} role="alert" className="mt-2 text-sm text-red-800">{rowErrors[incident.id]}</p>}
              </td></tr>)}
            </tbody></table>
          </div>
        </>}
      </section>
    </section>
  )
}