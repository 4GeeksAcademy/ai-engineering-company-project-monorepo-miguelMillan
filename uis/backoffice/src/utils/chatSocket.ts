import { abortableSleep, backoffDelay } from './backoff'
import type { ChatEvent } from './chat'

const STABLE_MS = 10_000
const PING_MS = 15_000
const STALE_MS = 40_000 // ni un pong en ese tiempo: conexión muerta aunque nadie la haya cerrado
const UNAUTHORIZED = 4401
const SESSION_GONE = [4404, 4410]

export type ChatStatus =
  | { state: 'connecting' }
  | { state: 'live' }
  | { state: 'reconnecting'; attempt: number; delayMs: number }
  | { state: 'closed' }

type Socket = Pick<WebSocket, 'send' | 'close' | 'readyState' | 'onopen' | 'onmessage' | 'onclose' | 'onerror'>

export type ChatSocketOptions = {
  sessionId: string
  baseUrl?: string
  getToken: () => string | null
  onEvent: (event: ChatEvent) => void
  onDisconnect: () => void
  onStatus: (status: ChatStatus) => void
  onUnauthorized: () => void
  /** La sesión ya no existe, no es nuestra o se cerró: hace falta otra. */
  onSessionGone: () => void
  createSocket?: (url: string) => Socket
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>
  random?: () => number
  pingMs?: number
  staleMs?: number
}

export type ChatConnection = { send: (event: 'user_message' | 'interrupt_requested', data: Record<string, string>) => boolean; stop: () => void }

export function chatUrl(sessionId: string, baseUrl = ''): string {
  const base = baseUrl.replace(/\/$/, '') || window.location.origin
  return `${base.replace(/^http/, 'ws')}/chat/ws?session_id=${encodeURIComponent(sessionId)}`
}

/** Conexión persistente de una sesión. Reconecta con backoff y el mismo `session_id`; el servidor responde con
 *  `session_snapshot` y hasta entonces no se da por viva. El JWT va en el primer frame, no en la URL. */
export function startChatSocket(options: ChatSocketOptions): ChatConnection {
  const { sessionId, baseUrl, getToken, onEvent, onDisconnect, onStatus, onUnauthorized, onSessionGone,
    createSocket = (url) => new WebSocket(url), sleep = abortableSleep, random = Math.random, pingMs = PING_MS, staleMs = STALE_MS } = options
  const stopping = new AbortController()
  let current: Socket | null = null
  let live = false
  let attempt = 0

  const frame = (socket: Socket, event: string, data: Record<string, string>) => socket.send(JSON.stringify({ event, data }))

  function connectOnce(token: string): Promise<number> {
    return new Promise((resolve) => {
      const socket = createSocket(chatUrl(sessionId, baseUrl))
      current = socket
      let liveSince = 0
      let staleTimer: ReturnType<typeof setTimeout> | undefined
      const heard = () => { clearTimeout(staleTimer); staleTimer = setTimeout(() => finish(1006), staleMs) }
      const pingTimer = setInterval(() => { if (live) frame(socket, 'ping', { session_id: sessionId }) }, pingMs)
      const abort = () => finish(1000)

      function finish(code: number) {
        clearTimeout(staleTimer)
        clearInterval(pingTimer)
        stopping.signal.removeEventListener('abort', abort)
        // Con la red muerta, onclose puede tardar minutos: no se espera.
        socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null
        try { socket.close() } catch { /* ya cerrado */ }
        if (current === socket) current = null
        if (liveSince && Date.now() - liveSince >= STABLE_MS) attempt = 0
        live = false
        onDisconnect()
        resolve(code)
      }

      stopping.signal.addEventListener('abort', abort, { once: true })
      heard()
      socket.onopen = () => frame(socket, 'auth', { token })
      socket.onmessage = (message) => {
        heard()
        let event: ChatEvent
        try { event = JSON.parse(String(message.data)) as ChatEvent } catch { return }
        if (event?.data?.session_id !== sessionId) return
        if (event.event === 'session_snapshot') {
          if (!live) { live = true; liveSince = Date.now(); onStatus({ state: 'live' }) }
        } else if (!live) return // antes de rehidratar no se acepta nada
        else attempt = 0
        onEvent(event)
      }
      socket.onclose = (closed) => finish(closed.code)
    })
  }

  async function run() {
    onStatus({ state: 'connecting' })
    while (!stopping.signal.aborted) {
      const token = getToken()
      const code = token ? await connectOnce(token) : UNAUTHORIZED
      if (stopping.signal.aborted) break
      if (code === UNAUTHORIZED) { onStatus({ state: 'closed' }); onUnauthorized(); return }
      if (SESSION_GONE.includes(code)) { onStatus({ state: 'closed' }); onSessionGone(); return }
      attempt += 1
      const delayMs = backoffDelay(attempt, random)
      onStatus({ state: 'reconnecting', attempt, delayMs })
      await sleep(delayMs, stopping.signal)
    }
  }

  void run()
  return {
    send(event, data) {
      if (!live || !current || current.readyState !== 1) return false
      frame(current, event, { session_id: sessionId, ...data })
      return true
    },
    stop: () => stopping.abort(),
  }
}
