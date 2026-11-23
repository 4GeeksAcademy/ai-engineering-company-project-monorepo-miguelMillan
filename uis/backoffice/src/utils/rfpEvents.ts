import type { RfpTicketSummary } from './rfps'

/** Payload de `rfp_ticket_created` (BRIEFING de tiempo real, §3). */
export type RfpTicketCreated = {
  ticket_id: string
  rfp_id: string
  client_name: string | null
  client_country: string | null
  services_requested: string[]
  status: string
  created_at: string
}

const text = (value: unknown): value is string => typeof value === 'string' && value !== ''
const textOrNull = (value: unknown): value is string | null => value === null || typeof value === 'string'

/** Valida el JSON de `data:`. Un payload con otra forma se ignora en lugar de romper el dashboard. */
export function parseTicketCreated(json: string): RfpTicketCreated | null {
  try {
    const p = JSON.parse(json) as Record<string, unknown> | null
    if (!p || !text(p.ticket_id) || !text(p.rfp_id) || !text(p.status) || !text(p.created_at) || Number.isNaN(Date.parse(p.created_at))
      || !textOrNull(p.client_name) || !textOrNull(p.client_country)
      || !Array.isArray(p.services_requested) || !p.services_requested.every(text)) return null
    return {
      ticket_id: p.ticket_id, rfp_id: p.rfp_id, client_name: p.client_name, client_country: p.client_country,
      services_requested: p.services_requested, status: p.status, created_at: p.created_at,
    }
  } catch {
    return null
  }
}

const newestFirst = (a: RfpTicketSummary, b: RfpTicketSummary) => Date.parse(b.created_at) - Date.parse(a.created_at)

/** Añade el ticket del evento, o completa el que ya estaba. Nunca lo duplica ni retrocede su estado (un evento reenviado
 *  trae el estado inicial, y el ticket puede haber avanzado). */
export function mergeTicketEvent(tickets: RfpTicketSummary[], event: RfpTicketCreated): RfpTicketSummary[] {
  if (tickets.some((t) => t.ticket_id === event.ticket_id)) {
    return tickets.map((t) => t.ticket_id !== event.ticket_id ? t : {
      ...t, rfp_id: event.rfp_id, client_name: t.client_name ?? event.client_name, client_country: t.client_country ?? event.client_country,
      departments_needed: t.departments_needed.length ? t.departments_needed : event.services_requested,
    })
  }
  const added: RfpTicketSummary = {
    ticket_id: event.ticket_id, rfp_id: event.rfp_id, status: event.status === 'analyzing' ? 'analizando' : event.status,
    pipeline_stage: 'clasificado', original_filename: '', created_at: event.created_at, updated_at: event.created_at, attempts: 1,
    last_error: null, discard_reason: null, response_stage: null, response_attempts: 0, approval_stage: null,
    approval_thread_id: null, approval_note: null, client_name: event.client_name, client_country: event.client_country,
    departments_needed: event.services_requested,
  }
  return [added, ...tickets].sort(newestFirst)
}

/** Lista recién pedida. Conserva los tickets que llegaron por el stream mientras la petición estaba en vuelo. */
export function mergeTicketList(current: RfpTicketSummary[], fetched: RfpTicketSummary[]): RfpTicketSummary[] {
  const known = new Set(fetched.map((t) => t.ticket_id))
  const newest = Math.max(0, ...fetched.map((t) => Date.parse(t.created_at)))
  return [...fetched, ...current.filter((t) => !known.has(t.ticket_id) && Date.parse(t.created_at) > newest)].sort(newestFirst)
}

/** Refresco parcial: solo cambia los tickets consultados. */
export function patchTickets(current: RfpTicketSummary[], updates: RfpTicketSummary[]): RfpTicketSummary[] {
  const byId = new Map(updates.map((t) => [t.ticket_id, t]))
  return current.map((t) => byId.get(t.ticket_id) ?? t)
}
