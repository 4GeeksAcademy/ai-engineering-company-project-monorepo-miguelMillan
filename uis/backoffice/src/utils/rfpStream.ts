import { createSseParser } from './sse'
import { parseTicketCreated, type RfpTicketCreated } from './rfpEvents'

export const RFP_EVENTS_PATH = '/rfp-intake/events'
const BASE_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000
const STABLE_MS = 10_000
const STALE_MS = 45_000 // tres keep-alive del servidor (15 s): sin bytes en ese tiempo, la conexión está muerta

export type StreamStatus =
  | { state: 'connecting' }
  | { state: 'live' }
  | { state: 'reconnecting'; attempt: number; delayMs: number }
  | { state: 'closed' }

export type RfpStreamOptions = {
  /** fetch autenticado del backoffice (`Authorization: Bearer …`); EventSource no admite cabeceras. */
  fetchStream: (path: string, init: RequestInit) => Promise<Response>
  initialEventId?: string
  onTicket: (ticket: RfpTicketCreated) => void
  /** El servidor no puede reenviar lo perdido: hay que recargar la lista. */
  onReset: () => void
  onStatus?: (status: StreamStatus) => void
  onCursor?: (eventId: string) => void
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>
  random?: () => number
  staleMs?: number
}

/** 1 s, 2 s, 4 s… hasta 30 s, con jitter entre la mitad y el total para que las pestañas no reconecten a la vez. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** (attempt - 1))
  return Math.round(ceiling / 2 + random() * (ceiling / 2))
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => { clearTimeout(timer); resolve() }, { once: true })
  })
}

/** Mantiene el stream abierto. Al reconectar envía `Last-Event-ID` para recibir lo perdido. Devuelve cómo pararlo. */
export function startRfpStream(options: RfpStreamOptions): () => void {
  const { fetchStream, onTicket, onReset, onStatus, onCursor, sleep = defaultSleep, random = Math.random, staleMs = STALE_MS } = options
  const stopping = new AbortController()
  let cursor = options.initialEventId ?? ''
  let attempt = 0

  function setCursor(eventId: string) {
    if (!eventId || eventId === cursor) return
    cursor = eventId
    onCursor?.(eventId)
  }

  /** Una conexión. Devuelve `false` si no tiene sentido reintentar (sesión no válida). */
  async function connectOnce(): Promise<boolean> {
    const connection = new AbortController()
    const abort = () => connection.abort()
    stopping.signal.addEventListener('abort', abort, { once: true })
    let watchdog: ReturnType<typeof setTimeout> | undefined
    const alive = () => { clearTimeout(watchdog); watchdog = setTimeout(abort, staleMs) }
    let openedAt = 0
    try {
      alive()
      const headers: Record<string, string> = { Accept: 'text/event-stream' }
      if (cursor) headers['Last-Event-ID'] = cursor
      const response = await fetchStream(RFP_EVENTS_PATH, { headers, signal: connection.signal, cache: 'no-store' })
      if (response.status === 401) return false
      if (!response.ok || !response.body || !response.headers.get('content-type')?.startsWith('text/event-stream')) {
        throw new Error(`Stream no disponible (${response.status})`)
      }
      openedAt = Date.now()
      onStatus?.({ state: 'live' })
      const parser = createSseParser((message) => {
        attempt = 0
        if (message.event === 'rfp_ticket_created') {
          const ticket = parseTicketCreated(message.data)
          if (ticket) onTicket(ticket)
        } else if (message.event === 'rfp_stream_reset') onReset()
      })
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        alive()
        parser.feed(decoder.decode(value, { stream: true }))
        setCursor(parser.lastEventId()) // después de entregar: si se corta antes, el servidor lo reenvía
      }
    } catch {
      // Red caída, servidor no disponible o conexión muerta: se reintenta con backoff.
    } finally {
      clearTimeout(watchdog)
      stopping.signal.removeEventListener('abort', abort)
      connection.abort()
    }
    if (openedAt && Date.now() - openedAt >= STABLE_MS) attempt = 0
    return true
  }

  async function run() {
    onStatus?.({ state: 'connecting' })
    while (!stopping.signal.aborted) {
      if (!await connectOnce()) break
      if (stopping.signal.aborted) break
      attempt += 1
      const delayMs = backoffDelay(attempt, random)
      onStatus?.({ state: 'reconnecting', attempt, delayMs })
      await sleep(delayMs, stopping.signal)
    }
    onStatus?.({ state: 'closed' })
  }

  void run()
  return () => stopping.abort()
}
