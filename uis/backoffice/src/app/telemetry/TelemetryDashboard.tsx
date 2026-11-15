import { useEffect, useState } from 'react'
import { apiRequest } from '../../utils/auth'

type MetricRow = Record<string, string | number>

type TelemetryReport = {
  period: { from: string; to: string }
  metrics: Record<string, MetricRow[]>
}

const metricLabels: Record<string, string> = {
  events_per_day: 'Eventos por día',
  error_rate_by_type: 'Tasa de errores',
  auth_failure_rate: 'Tasa de fallos de autenticación',
  latency_by_route: 'Latencia por ruta',
  dominant_event_types: 'Tipos de evento dominantes',
}

function formatValue(value: string | number): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
  return value
}

function TelemetryTable({ rows }: { rows: MetricRow[] }) {
  if (rows.length === 0) return <p className="text-sm text-slate-600">Sin datos en el periodo.</p>
  const columns = Object.keys(rows[0])
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-600">
          <tr>{columns.map(column => <th key={column} scope="col" className="px-3 py-2">{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${String(row[columns[0]])}-${index}`} className="border-b border-slate-100">
              {columns.map(column => <td key={column} className="whitespace-nowrap px-3 py-2">{formatValue(row[column])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function TelemetryDashboard() {
  const [report, setReport] = useState<TelemetryReport | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    apiRequest<TelemetryReport>('/telemetry/report')
      .then(result => { if (active) setReport(result) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'No se pudo cargar el reporte.') })
    return () => { active = false }
  }, [])

  if (error) return <p role="alert" className="text-red-900">{error}</p>
  if (!report) return <p role="status">Cargando telemetría...</p>

  return (
    <section aria-labelledby="telemetry-title" className="space-y-6">
      <header>
        <h2 id="telemetry-title" className="font-display text-2xl font-bold">Telemetría técnica</h2>
        <p className="mt-2 text-sm text-slate-600">
          Ventana visualizada: {report.period.from} — {report.period.to}
        </p>
      </header>
      <div className="grid gap-6 lg:grid-cols-2">
        {Object.entries(report.metrics).map(([name, rows]) => (
          <article key={name} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h3 className="mb-4 font-display text-lg font-semibold">{metricLabels[name] ?? name}</h3>
            <TelemetryTable rows={rows} />
          </article>
        ))}
      </div>
    </section>
  )
}
