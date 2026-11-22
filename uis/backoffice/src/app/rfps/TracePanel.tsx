import { useState } from 'react'
import { getTrace, type TraceEvent } from '../../utils/rfps'

const PART_LABELS: Record<string, string> = { intake: 'Recepción', response: 'Generación', approval: 'Aprobación' }

/** Qué agente hizo qué y en qué orden, en las tres partes del flujo. */
export function TracePanel({ ticketId }: { ticketId: string }) {
  const [events, setEvents] = useState<TraceEvent[] | null>(null)
  const [error, setError] = useState('')

  function load(open: boolean) {
    if (open && !events) getTrace(ticketId).then((r) => setEvents(r.events)).catch((e: Error) => setError(e.message))
  }

  return (
    <details data-testid="rfp-trace" className="rounded-md border border-slate-200 p-3 text-sm" onToggle={(e) => load((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer font-semibold text-slate-900">Trazabilidad: agentes y pasos{events ? ` (${events.length})` : ''}</summary>
      {error && <p role="alert" className="text-rose-800">{error}</p>}
      {events && (
        <div className="mt-2 max-h-96 overflow-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50"><tr><th className="px-2 py-1">#</th><th className="px-2 py-1">Parte</th><th className="px-2 py-1">Agente</th><th className="px-2 py-1">Nodo</th><th className="px-2 py-1">Departamento</th><th className="px-2 py-1">Hora</th></tr></thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={e.id} className={`border-t border-slate-100 ${e.status !== 'ok' ? 'text-rose-800' : ''}`}>
                  <td className="px-2 py-1">{i + 1}</td><td className="px-2 py-1">{PART_LABELS[e.part] ?? e.part}</td>
                  <td className="px-2 py-1 font-medium">{e.agent}</td><td className="px-2 py-1">{e.node}</td>
                  <td className="px-2 py-1">{e.department_id ?? '—'}</td>
                  <td className="px-2 py-1">{new Date(e.finished_at).toLocaleTimeString('es-ES')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  )
}
