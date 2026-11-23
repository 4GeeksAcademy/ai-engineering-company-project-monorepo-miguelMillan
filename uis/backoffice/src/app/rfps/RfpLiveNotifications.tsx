import { BellRing, X } from 'lucide-react'
import { useEffect, useSyncExternalStore } from 'react'
import { DEPARTMENT_LABELS } from '../../utils/rfps'
import { dismissGap, dismissRfp, openRfp, rfpLiveStore, startRfpLive } from '../../utils/rfpLive'
import type { StreamStatus } from '../../utils/rfpStream'

function statusLabel(status: StreamStatus): string {
  if (status.state === 'live') return 'RFP en vivo'
  if (status.state === 'reconnecting') return `Sin conexión en vivo · reintento ${status.attempt} en ${Math.max(1, Math.round(status.delayMs / 1000))} s`
  return status.state === 'closed' ? 'RFP en vivo detenido' : 'Conectando con RFP en vivo…'
}

/** Aviso de RFP nuevas en tiempo real (SSE), visible en todo el backoffice y distinto del resto del dashboard. */
export function RfpLiveNotifications() {
  const { status, notifications, gap } = useSyncExternalStore(rfpLiveStore.subscribe, rfpLiveStore.getSnapshot)
  useEffect(() => startRfpLive(), [])
  const live = status.state === 'live'

  return (
    <aside aria-label="Notificaciones de RFP en tiempo real" className="mx-auto w-full max-w-6xl space-y-3 px-6 pt-4">
      <p data-testid="rfp-live-status" data-state={status.state} className={`flex items-center justify-end gap-2 text-xs font-medium ${live ? 'text-emerald-800' : 'text-amber-900'}`}>
        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${live ? 'bg-emerald-600' : 'bg-amber-500'}`} />{statusLabel(status)}
      </p>
      {gap && (
        <p role="alert" className="flex items-center justify-between gap-3 rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-950">
          La conexión estuvo caída demasiado tiempo para reenviar todos los avisos: revisa la lista de RFP, ya actualizada.
          <button type="button" onClick={dismissGap} className="font-semibold underline">Entendido</button>
        </p>
      )}
      <ul aria-live="polite" className="space-y-2">
        {notifications.map((n) => (
          <li key={n.ticket_id} data-testid="rfp-notification" data-ticket={n.ticket_id}
            className="flex flex-wrap items-center gap-4 rounded-lg border-l-8 border-violet-700 bg-violet-100 px-4 py-3 text-sm text-violet-950 shadow-sm">
            <BellRing size={20} aria-hidden="true" className="shrink-0 text-violet-800" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold"><span className="mr-2 rounded bg-violet-700 px-2 py-0.5 text-xs text-white">Nueva RFP</span>
                {n.client_name ?? 'Cliente sin identificar'}{n.client_country && ` (${n.client_country})`}</p>
              <p className="text-xs">{n.rfp_id} · {n.services_requested.map((s) => DEPARTMENT_LABELS[s]?.split(' · ')[0] ?? s).join(', ') || 'servicios por identificar'}
                {' · '}{new Date(n.created_at).toLocaleString('es-ES')}</p>
            </div>
            <button type="button" onClick={() => openRfp(n.ticket_id)} className="rounded-md bg-violet-800 px-3 py-1.5 text-xs font-semibold text-white">Ver ticket</button>
            <button type="button" onClick={() => dismissRfp(n.ticket_id)} aria-label={`Descartar aviso de ${n.rfp_id}`} className="rounded p-1 hover:bg-violet-200"><X size={16} aria-hidden="true" /></button>
          </li>
        ))}
      </ul>
    </aside>
  )
}
