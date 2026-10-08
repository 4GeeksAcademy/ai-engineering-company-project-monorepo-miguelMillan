import { useRef, useState } from 'react'

interface AnalysisReport {
  totals: {
    total_records: number
    valid_records: number
    invalid_records: number
  }
  by_category: Record<string, number>
  by_status: Record<string, number>
  by_country: Record<string, number>
  satisfaction: {
    closed_records: number
    scored_records: number
    average_score: number | null
    score_counts: Record<string, number>
  }
  invalid_by_rule: Record<string, number>
  invalid_records: Array<{ row: number; issues: string[] }>
}

const apiUrl = import.meta.env.VITE_API_URL ?? ''
const issueLabels: Record<string, string> = {
  incident_id: 'ID de incidencia ausente o inválido',
  date: 'Fecha ausente o inválida',
  country: 'País ausente o inválido',
  customer_type: 'Tipo de cliente ausente o inválido',
  tracking_number: 'Número de seguimiento ausente o inválido',
  carrier_country: 'Transportista no válido para el país',
  category: 'Categoría ausente o inválida',
  description: 'Descripción ausente o demasiado corta',
  status: 'Estado ausente o inválido',
  customer_email: 'Email ausente o inválido',
  closed_without_score: 'Incidencia cerrada sin puntuación',
  score_out_of_range: 'Puntuación fuera del rango 1-5',
  duplicate_incident_id: 'ID de incidencia duplicado',
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-5">
      <h3 className="text-sm font-semibold text-slate-600">{label}</h3>
      <p className="mt-2 font-display text-3xl font-bold text-slate-950">{value}</p>
      {detail && <p className="mt-1 text-sm text-slate-500">{detail}</p>}
    </article>
  )
}

function Breakdown({ title, rows }: { title: string; rows: Record<string, number> }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5" aria-label={title}>
      <h3 className="font-display text-lg font-bold text-slate-900">{title}</h3>
      <table className="mt-4 w-full text-left text-sm">
        <thead className="border-b border-slate-200 text-slate-500">
          <tr>
            <th scope="col" className="py-2 font-medium">Valor</th>
            <th scope="col" className="py-2 text-right font-medium">Incidencias válidas</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(rows).map(([name, count]) => (
            <tr key={name} className="border-b border-slate-100 last:border-0">
              <th scope="row" className="py-2 font-medium text-slate-800">{name}</th>
              <td className="py-2 text-right tabular-nums text-slate-700">{count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export function IncidentAnalysis() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [report, setReport] = useState<AnalysisReport | null>(null)
  const [fileName, setFileName] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [dragging, setDragging] = useState(false)

  async function analyzeFile(file?: File) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setError('Selecciona un archivo con extensión .csv.')
      return
    }

    setError('')
    setReport(null)
    setFileName(file.name)
    setLoading(true)
    const formData = new FormData()
    formData.append('file', file)

    try {
      const response = await fetch(`${apiUrl}/api/incidents/analyze`, {
        method: 'POST',
        body: formData,
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload.error ?? 'No se pudo analizar el archivo.')
      }
      setReport(payload as AnalysisReport)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo conectar con la API.')
    } finally {
      setLoading(false)
    }
  }

  function receiveDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    setDragging(false)
    void analyzeFile(event.dataTransfer.files[0])
  }

  const invalidIssues = report
    ? Object.entries(report.invalid_by_rule).filter(([, count]) => count > 0)
    : []

  return (
    <section className="space-y-6" aria-labelledby="incident-analysis-title">
      <div>
        <p className="text-sm font-semibold uppercase text-emerald-800">Experiencia del cliente</p>
        <h2 id="incident-analysis-title" className="mt-1 font-display text-2xl font-bold text-slate-950">
          Análisis de incidencias
        </h2>
        <p className="mt-2 text-slate-600">Carga un CSV de incidencias para revisar volumen, calidad y satisfacción.</p>
      </div>

      <div className="rounded-lg border border-dashed border-slate-400 bg-white p-6 sm:p-8">
        <label
          htmlFor="incident-csv"
          onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={receiveDrop}
          className={`flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-5 py-6 text-center focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-emerald-700 ${dragging ? 'border-emerald-700 bg-emerald-50' : 'border-slate-300 bg-slate-50'}`}
        >
          <input
            ref={inputRef}
            id="incident-csv"
            className="sr-only"
            type="file"
            accept=".csv,text/csv"
            aria-describedby="incident-csv-hint"
            onChange={(event) => void analyzeFile(event.target.files?.[0])}
          />
          <span className="font-semibold text-slate-900">Suelta aquí el archivo CSV o selecciónalo</span>
          <span id="incident-csv-hint" className="mt-1 text-sm text-slate-600">Solo archivos .csv · máximo 10 MB</span>
          {fileName && <span className="mt-2 text-sm font-medium text-emerald-800">{fileName}</span>}
        </label>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={loading}
            className="rounded-md bg-emerald-800 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:cursor-wait disabled:opacity-60"
          >
            {loading ? 'Analizando…' : 'Seleccionar CSV'}
          </button>
          {report && (
            <a
              href={`${apiUrl}/api/incidents/results/export`}
              className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800"
            >
              Descargar resultados CSV
            </a>
          )}
        </div>
        <p className="mt-3 min-h-5 text-sm text-red-800" role="status" aria-live="polite">
          {error}
        </p>
      </div>

      {report && (
        <div className="space-y-6" aria-live="polite">
          <section aria-label="Resumen general" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="Registros procesados" value={String(report.totals.total_records)} />
            <Metric label="Válidos" value={String(report.totals.valid_records)} />
            <Metric label="Inválidos" value={String(report.totals.invalid_records)} />
            <Metric
              label="CSAT medio" value={report.satisfaction.average_score?.toFixed(2) ?? 'N/D'}
              detail={`${report.satisfaction.scored_records} puntuaciones en ${report.satisfaction.closed_records} cierres válidos`}
            />
          </section>

          {invalidIssues.length > 0 && (
            <aside className="rounded-lg border border-amber-300 bg-amber-50 p-5" aria-labelledby="invalid-title">
              <h3 id="invalid-title" className="font-semibold text-amber-950">
                Registros excluidos del análisis: {report.totals.invalid_records}
              </h3>
              <ul className="mt-3 grid gap-x-6 gap-y-2 text-sm text-amber-950 sm:grid-cols-2">
                {invalidIssues.map(([rule, count]) => (
                  <li key={rule} className="flex justify-between gap-4">
                    <span>{issueLabels[rule] ?? rule}</span>
                    <strong className="tabular-nums">{count}</strong>
                  </li>
                ))}
              </ul>
              {report.invalid_records.length > 0 && (
                <details className="mt-4 border-t border-amber-200 pt-3 text-sm text-amber-950">
                  <summary className="cursor-pointer font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-800">
                    Ver filas inválidas y sus motivos
                  </summary>
                  <ul className="mt-3 space-y-1">
                    {report.invalid_records.map(({ row, issues }) => (
                      <li key={row}>Fila {row}: {issues.map((issue) => issueLabels[issue] ?? issue).join(', ')}</li>
                    ))}
                  </ul>
                </details>
              )}
            </aside>
          )}

          <div className="grid gap-4 lg:grid-cols-3">
            <Breakdown title="Por categoría" rows={report.by_category} />
            <Breakdown title="Por estado" rows={report.by_status} />
            <Breakdown title="Por país" rows={report.by_country} />
          </div>
        </div>
      )}
    </section>
  )
}