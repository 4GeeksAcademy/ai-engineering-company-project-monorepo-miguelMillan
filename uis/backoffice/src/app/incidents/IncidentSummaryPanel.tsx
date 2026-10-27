import { useEffect, useState } from 'react'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import { branchLabels, categoryLabels, incidentButton, incidentError, incidentRequest, originLabels, statusLabels } from '../../utils/incidents'
import type { IncidentSummary } from '../../utils/incidents'

export function IncidentSummaryPanel({ revision }: { revision: number }) {
  const [result, setResult] = useState({ summary: null as IncidentSummary | null, error: '', revision: -1, retry: -1 })
  const [retry, setRetry] = useState(0)
  const loading = result.revision !== revision || result.retry !== retry
  const summary = result.summary
  const error = loading ? '' : result.error

  useEffect(() => {
    const controller = new AbortController()
    incidentRequest<IncidentSummary>('/summary', { signal: controller.signal })
      .then((summary) => { if (!controller.signal.aborted) setResult({ summary, error: '', revision, retry }) })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setResult({ summary: null, error: incidentError(cause), revision, retry }) })
    return () => controller.abort()
  }, [revision, retry])

  return (
    <section aria-labelledby="incident-summary-title" aria-busy={loading} className="space-y-4 border-y border-slate-200 py-6">
      <header className="flex flex-wrap items-center justify-between gap-3"><h3 id="incident-summary-title" className="font-display text-xl font-semibold">Resumen de incidencias</h3>{!loading && !error && summary && <p className="text-sm font-semibold">Total registrado: {summary.total}</p>}</header>
      {loading ? <p role="status" className="flex items-center gap-2 text-sm text-slate-600"><LoaderCircle size={18} className="animate-spin" aria-hidden="true" />Cargando resumen...</p> : error ? <div className="space-y-3"><p role="alert" className="text-sm text-red-800">{error}</p><button type="button" onClick={() => setRetry((value) => value + 1)} className={`${incidentButton} border border-slate-300 bg-white`}><RefreshCw size={16} aria-hidden="true" />Reintentar resumen</button></div> : summary && <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-4">
        {([
          ['Por estado', statusLabels, summary.by_status], ['Por categoría', categoryLabels, summary.by_category],
          ['Por origen', originLabels, summary.by_origin], ['Por sede', branchLabels, summary.by_branch],
        ] as const).map(([title, labels, counts]) => <article key={title} className="min-w-0"><h4 className="mb-3 text-sm font-bold text-slate-700">{title}</h4><dl className="space-y-2">{Object.entries(labels).map(([key, label]) => <div key={key} className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 text-sm"><dt className="break-words text-slate-600">{label}</dt><dd className="font-semibold tabular-nums">{(counts as Record<string, number>)[key] ?? 0}</dd></div>)}</dl></article>)}
      </div>}
    </section>
  )
}