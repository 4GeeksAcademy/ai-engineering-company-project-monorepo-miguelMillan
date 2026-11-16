import { useEffect, useState } from 'react'
import { Calendar, AlertTriangle, Zap, TrendingDown, TrendingUp } from 'lucide-react'
import { AsyncErrorState } from '../../components/AsyncErrorState'
import { apiRequest } from '../../utils/auth'
import { track } from '../../services/telemetry'

interface WeeklyEntry {
  warehouse: 'los_angeles' | 'zaragoza'
  client_id: string
  inbound_units_count: number
  outbound_orders_count: number
  stockout_events_count: number
  discrepancy_events_count: number
  discrepancy_rate: number
}

interface WeeklyReport {
  week_start: string
  entries: WeeklyEntry[]
}

const warehouseLabels = {
  los_angeles: 'Los Ángeles',
  zaragoza: 'Zaragoza',
} as const

function formatDate(isoDate: string): string {
  const date = new Date(isoDate)
  return date.toLocaleDateString('es-ES', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
}

function useWeeklyReport() {
  const [report, setReport] = useState<WeeklyReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  function reload() {
    setError('')
    setLoading(true)
    apiRequest<WeeklyReport>('/reporting/weekly-warehouse-client-performance')
      .then((data: WeeklyReport) => {
        setReport(data)
        track('backoffice_section_viewed', { section: 'reporting' })
      })
      .catch((cause: unknown) => {
        const message = cause instanceof Error ? cause.message : 'No se pudo cargar el reporte semanal.'
        setError(message)
        track('frontend_data_load_failed', { section: 'reporting' })
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    reload()
  }, [])

  return { report, loading, error, reload }
}

export function ReportingPage() {
  const { report, loading, error, reload } = useWeeklyReport()

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-700"></div>
          <p className="mt-4 text-slate-600">Cargando reporte de desempeño...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return <AsyncErrorState message={error} onRetry={reload} />
  }

  if (!report || report.entries.length === 0) {
    return (
      <div className="min-h-screen bg-slate-50 p-6">
        <div className="max-w-6xl mx-auto">
          <h1 className="text-3xl font-bold text-slate-900 mb-2">Reporte Semanal de Desempeño</h1>
          <p className="text-slate-600 mb-8">No hay datos disponibles para mostrar.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="max-w-6xl mx-auto">
        <header className="mb-8">
          <h1 className="text-3xl font-bold text-slate-900 mb-2">Reporte Semanal de Desempeño</h1>
          <div className="flex items-center gap-2 text-slate-600 mb-4">
            <Calendar className="w-4 h-4" />
            <span>Semana iniciada: {formatDate(report.week_start)}</span>
          </div>
          <p className="text-sm text-slate-600">
            Desempeño de almacenes y clientes: volumen de entrada, throughput de salida, frecuencia de quiebre de stock y precisión de inventario.
          </p>
        </header>

        <div className="space-y-6">
          {report.entries.map((entry) => (
            <section key={`${entry.warehouse}-${entry.client_id}`} className="bg-white rounded-lg border border-slate-200 p-6 shadow-sm">
              <h2 className="text-lg font-semibold text-slate-900 mb-1">
                {warehouseLabels[entry.warehouse]} · {entry.client_id}
              </h2>
              <p className="text-xs text-slate-500 mb-6">Almacén: {entry.warehouse} | Cliente: {entry.client_id}</p>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {/* KPI 1: Volumen de entrada */}
                <div className="bg-gradient-to-br from-blue-50 to-blue-100 rounded-lg p-4 border border-blue-200">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <p className="text-xs font-semibold text-blue-700 uppercase tracking-wider">Volumen de entrada</p>
                      <p className="text-2xl font-bold text-blue-900">{entry.inbound_units_count.toLocaleString('es-ES')}</p>
                    </div>
                    <TrendingUp className="w-5 h-5 text-blue-600" />
                  </div>
                  <p className="text-xs text-blue-700">Unidades recibidas esta semana</p>
                </div>

                {/* KPI 2: Throughput de salida */}
                <div className="bg-gradient-to-br from-emerald-50 to-emerald-100 rounded-lg p-4 border border-emerald-200">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <p className="text-xs font-semibold text-emerald-700 uppercase tracking-wider">Throughput de salida</p>
                      <p className="text-2xl font-bold text-emerald-900">{entry.outbound_orders_count.toLocaleString('es-ES')}</p>
                    </div>
                    <TrendingDown className="w-5 h-5 text-emerald-600" />
                  </div>
                  <p className="text-xs text-emerald-700">Pedidos despachados esta semana</p>
                </div>

                {/* KPI 3: Frecuencia de quiebre de stock */}
                <div className="bg-gradient-to-br from-amber-50 to-amber-100 rounded-lg p-4 border border-amber-200">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider">Frecuencia de quiebre</p>
                      <p className="text-2xl font-bold text-amber-900">{entry.stockout_events_count.toLocaleString('es-ES')}</p>
                    </div>
                    <AlertTriangle className="w-5 h-5 text-amber-600" />
                  </div>
                  <p className="text-xs text-amber-700">Veces que el stock cayó bajo mínimo</p>
                </div>

                {/* KPI 4: Tasa de discrepancia */}
                <div className="bg-gradient-to-br from-red-50 to-red-100 rounded-lg p-4 border border-red-200">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <p className="text-xs font-semibold text-red-700 uppercase tracking-wider">Tasa de discrepancia</p>
                      <p className="text-2xl font-bold text-red-900">{(entry.discrepancy_rate * 100).toFixed(1)}%</p>
                    </div>
                    <Zap className="w-5 h-5 text-red-600" />
                  </div>
                  <p className="text-xs text-red-700">Precisión de inventario ({entry.discrepancy_events_count}/{entry.outbound_orders_count})</p>
                </div>
              </div>
            </section>
          ))}
        </div>

        <div className="mt-8 text-xs text-slate-500 text-center">
          <p>Reporte automático generado para {report.entries.length} combinación(es) de almacén y cliente.</p>
        </div>
      </div>
    </div>
  )
}
