import { apiFetch } from './auth'
import { getSessionToken } from './session'
import { type RfpTicketCreated } from './rfpEvents'
import { startRfpStream, type StreamStatus } from './rfpStream'

const storageKey = 'trackflow.rfpLive'
const MAX_SEEN = 200

export type RfpLiveState = {
  status: StreamStatus
  notifications: RfpTicketCreated[]
  /** Hubo un corte que el servidor no pudo reenviar: la lista se recargó, pero puede haber RFP sin notificación. */
  gap: boolean
}

type Saved = { user: string; cursor: string; seen: string[]; notifications: RfpTicketCreated[] }
type Listener = { onTicket: (ticket: RfpTicketCreated) => void; onResync: () => void; onOpen: (ticketId: string) => void }

let state: RfpLiveState = { status: { state: 'connecting' }, notifications: [], gap: false }
let saved: Saved = { user: '', cursor: '', seen: [], notifications: [] }
const subscribers = new Set<() => void>()
const listeners = new Set<Listener>()

function currentUser(): string {
  try {
    return String(JSON.parse(atob((getSessionToken() ?? '').split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).sub)
  } catch {
    return ''
  }
}

function set(change: Partial<RfpLiveState>) {
  state = { ...state, ...change }
  subscribers.forEach((notify) => notify())
}

function persist(change: Partial<Saved>) {
  saved = { ...saved, ...change }
  try { sessionStorage.setItem(storageKey, JSON.stringify(saved)) } catch { /* sin almacenamiento: solo en memoria */ }
}

/** La navegación del backoffice recarga la página: el cursor y lo no leído sobreviven en la pestaña, por usuario. */
function restore() {
  const user = currentUser()
  saved = { user, cursor: '', seen: [], notifications: [] }
  try {
    const stored = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null') as Saved | null
    if (stored && stored.user === user && Array.isArray(stored.seen) && Array.isArray(stored.notifications)) saved = { ...saved, ...stored }
  } catch { /* contenido ilegible: se empieza de cero */ }
  set({ notifications: saved.notifications, gap: false })
}

function receive(ticket: RfpTicketCreated) {
  listeners.forEach((listener) => listener.onTicket(ticket))
  if (saved.seen.includes(ticket.ticket_id)) return // reenvío: ya se notificó (aunque se descartara)
  const notifications = [ticket, ...state.notifications]
  persist({ seen: [ticket.ticket_id, ...saved.seen].slice(0, MAX_SEEN), notifications })
  set({ notifications })
}

export function startRfpLive(): () => void {
  restore()
  let opened = false
  return startRfpStream({
    fetchStream: apiFetch,
    initialEventId: saved.cursor,
    onTicket: receive,
    onReset: () => { set({ gap: true }); listeners.forEach((listener) => listener.onResync()) },
    onStatus: (status) => {
      set({ status })
      // Primera conexión: cubre los tickets creados entre la carga de la lista y la apertura del stream.
      if (status.state === 'live' && !opened) { opened = true; listeners.forEach((listener) => listener.onResync()) }
    },
    onCursor: (cursor) => persist({ cursor }),
  })
}

export function dismissRfp(ticketId: string) {
  const notifications = state.notifications.filter((n) => n.ticket_id !== ticketId)
  persist({ notifications })
  set({ notifications })
}

export function dismissGap() { set({ gap: false }) }

export function openRfp(ticketId: string) {
  dismissRfp(ticketId)
  if (listeners.size > 0) listeners.forEach((listener) => listener.onOpen(ticketId))
  else window.location.assign(`/rfps?ticket=${encodeURIComponent(ticketId)}`)
}

export function listenRfpLive(listener: Listener): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export const rfpLiveStore = {
  subscribe: (notify: () => void) => { subscribers.add(notify); return () => { subscribers.delete(notify) } },
  getSnapshot: () => state,
}
