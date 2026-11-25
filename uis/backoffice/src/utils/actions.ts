import { apiRequest } from './auth'

export interface PendingAction {
  id: string
  kind: string
  summary: string
  requested_by: string
  requested_role: string
  requested_at: string
  expires_at: string
}

export const ACTION_LABELS: Record<string, string> = {
  dispatch_high_value: 'Despacho de alto valor',
  incident_close: 'Cierre de incidencia pedido por un agente',
}

export async function listPendingActions(signal?: AbortSignal): Promise<PendingAction[]> {
  return (await apiRequest<{ actions: PendingAction[] }>('/actions', { signal })).actions
}

export function decideAction(id: string, decision: 'confirm' | 'reject', reason: string): Promise<unknown> {
  return apiRequest(`/actions/${encodeURIComponent(id)}/${decision}`, { method: 'POST', body: JSON.stringify({ reason }) })
}
