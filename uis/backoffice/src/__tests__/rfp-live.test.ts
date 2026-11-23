import { TextDecoder, TextEncoder } from 'util'

Object.assign(globalThis, { TextDecoder })
jest.mock('../utils/auth', () => ({ apiFetch: jest.fn(), apiRequest: jest.fn() }))

import { apiFetch } from '../utils/auth'
import { mergeTicketEvent, mergeTicketList, parseTicketCreated, patchTickets, type RfpTicketCreated } from '../utils/rfpEvents'
import { dismissRfp, listenRfpLive, rfpLiveStore, startRfpLive } from '../utils/rfpLive'
import { backoffDelay } from '../utils/backoff'
import { startRfpStream, type RfpStreamOptions, type StreamStatus } from '../utils/rfpStream'
import type { RfpTicketSummary } from '../utils/rfps'
import { createSseParser, type SseMessage } from '../utils/sse'

const ticket = (id: string, change: Partial<RfpTicketCreated> = {}): RfpTicketCreated => ({
  ticket_id: id, rfp_id: `rfp_${id}`, client_name: 'Luna Cosmetics', client_country: 'US', services_requested: ['warehouse', 'lastmile'],
  status: 'analyzing', created_at: '2026-11-23T10:00:00Z', ...change,
})
const created = (id: number, t: RfpTicketCreated) => `id: ${id}\nevent: rfp_ticket_created\ndata: ${JSON.stringify(t)}\n\n`
const reset = (id: number) => `id: ${id}\nevent: rfp_stream_reset\ndata: {"reason":"replay_limit_exceeded"}\n\n`
const tick = async (times = 40) => { for (let i = 0; i < times; i++) await Promise.resolve() }

type Step = { status?: number; type?: string; chunks?: string[]; stay?: boolean } | 'network-error'

/** Servidor doble: cada conexión consume un paso (trozos del wire y después corte, o conexión abierta). */
function server(steps: Step[]) {
  const requests: { path: string; headers: Record<string, string> }[] = []
  const fetchStream = async (path: string, init: RequestInit = {}) => {
    requests.push({ path, headers: { ...(init.headers as Record<string, string>) } })
    const step = steps.shift() ?? { stay: true }
    if (step === 'network-error') throw new TypeError('Failed to fetch')
    const chunks = [...(step.chunks ?? [])]
    const read = () => {
      const chunk = chunks.shift()
      if (chunk !== undefined) return Promise.resolve({ done: false, value: new Uint8Array(new TextEncoder().encode(chunk)) })
      if (!step.stay) return Promise.resolve({ done: true, value: undefined })
      return new Promise((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(new Error('aborted'))))
    }
    const status = step.status ?? 200
    return { status, ok: status === 200, headers: { get: () => step.type ?? 'text/event-stream; charset=utf-8' }, body: { getReader: () => ({ read }) } } as unknown as Response
  }
  return { requests, fetchStream }
}

async function run(steps: Step[], options: Partial<RfpStreamOptions> = {}, settle = 400) {
  const { requests, fetchStream } = server(steps)
  const received: string[] = []
  const delays: number[] = []
  const statuses: StreamStatus[] = []
  let resets = 0
  const stop = startRfpStream({
    fetchStream, onTicket: (t) => received.push(t.ticket_id), onReset: () => { resets += 1 }, onStatus: (s) => statuses.push(s),
    sleep: async (ms) => { delays.push(ms) }, random: () => 1, ...options,
  })
  await tick(settle)
  stop()
  await tick()
  return { requests, received, delays, statuses, resets }
}

describe('parser SSE', () => {
  it('reconstruye los mensajes aunque el wire llegue cortado en cualquier punto, con CRLF, comentarios y UTF-8', () => {
    const wire = `retry: 1000\nid: 4\n\n: keep-alive\n\n${created(5, ticket('a', { client_name: 'Señal, S.L.' }))}`
    for (const text of [wire, wire.replace(/\n/g, '\r\n')]) {
      for (let size = 1; size <= text.length; size += 3) {
        const messages: SseMessage[] = []
        const parser = createSseParser((m) => messages.push(m))
        for (let i = 0; i < text.length; i += size) parser.feed(text.slice(i, i + size))
        expect(messages).toHaveLength(1)
        expect(messages[0]).toMatchObject({ id: '5', event: 'rfp_ticket_created' })
        expect(parseTicketCreated(messages[0].data)?.client_name).toBe('Señal, S.L.')
      }
    }
  })

  it('un bloque con solo id fija el cursor sin emitir, y un mensaje a medias no se entrega', () => {
    const messages: SseMessage[] = []
    const parser = createSseParser((m) => messages.push(m))
    parser.feed('id: 9\n\nevent: rfp_ticket_created\ndata: {"a"')
    expect(parser.lastEventId()).toBe('9')
    expect(messages).toEqual([])
  })
})

describe('reconexión y recuperación', () => {
  it('el backoff crece hasta 30 s y el jitter nunca lo adelanta al intento anterior', () => {
    expect([1, 2, 3, 4, 5, 6, 9].map((n) => backoffDelay(n, () => 1))).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000])
    for (let n = 1; n < 5; n++) expect(backoffDelay(n + 1, () => 0)).toBeGreaterThanOrEqual(backoffDelay(n, () => 1))
  })

  it('reintenta con backoff progresivo y al volver pide lo perdido con Last-Event-ID, sin perder ni repetir', async () => {
    const { requests, received, delays, statuses } = await run([
      { chunks: ['retry: 1000\nid: 6\n\n', created(7, ticket('antes'))] },   // se corta
      'network-error', { status: 503 }, { type: 'text/html' },               // servidor caído
      { chunks: [created(8, ticket('perdido-1')), created(9, ticket('perdido-2'))], stay: true },
    ])
    expect(received).toEqual(['antes', 'perdido-1', 'perdido-2'])
    expect(delays).toEqual([1000, 2000, 4000, 8000])
    expect(requests.map((r) => r.headers['Last-Event-ID'])).toEqual([undefined, '7', '7', '7', '7'])
    expect(statuses.filter((s) => s.state === 'reconnecting').map((s) => s.state === 'reconnecting' && s.attempt)).toEqual([1, 2, 3, 4])
  })

  it('sin eventos previos reanuda desde el cursor inicial, y un evento recibido reinicia el backoff', async () => {
    const first = await run([{ chunks: ['id: 3\n\n'] }, { chunks: [created(4, ticket('a'))] }, 'network-error', 'network-error'])
    expect(first.requests.map((r) => r.headers['Last-Event-ID']).slice(0, 4)).toEqual([undefined, '3', '4', '4'])
    expect(first.delays).toEqual([1000, 1000, 2000, 4000])
  })

  it('una conexión que acepta y se cae enseguida no reintenta sin freno', async () => {
    const { delays } = await run([{ chunks: ['id: 1\n\n'] }, { chunks: ['id: 1\n\n'] }, { chunks: ['id: 1\n\n'] }])
    expect(delays).toEqual([1000, 2000, 4000])
  })

  it('corta y reconecta una conexión muerta que ya no envía ni keep-alive', async () => {
    jest.useFakeTimers()
    const { requests, fetchStream } = server([{ chunks: ['id: 2\n\n'], stay: true }, { stay: true }])
    const stop = startRfpStream({ fetchStream, onTicket: () => {}, onReset: () => {}, sleep: async () => {}, staleMs: 45_000 })
    await tick()
    jest.advanceTimersByTime(44_000); await tick()
    expect(requests).toHaveLength(1)
    jest.advanceTimersByTime(2_000); await tick()
    expect(requests.map((r) => r.headers['Last-Event-ID'])).toEqual([undefined, '2'])
    stop()
    jest.useRealTimers()
  })

  it('un hueco irrecuperable avisa con reset; un 401 detiene los reintentos; un payload ajeno se ignora', async () => {
    expect((await run([{ chunks: [reset(40)], stay: true }])).resets).toBe(1)
    const unauthorized = await run([{ status: 401 }])
    expect([unauthorized.requests.length, unauthorized.delays, unauthorized.statuses.at(-1)]).toEqual([1, [], { state: 'closed' }])
    const odd = await run([{ chunks: ['id: 1\nevent: rfp_ticket_created\ndata: {"event":"x","data":{}}\n\n', 'id: 2\nevent: otro\ndata: {}\n\n', created(3, ticket('ok'))], stay: true }])
    expect(odd.received).toEqual(['ok'])
  })
})

describe('lista del dashboard', () => {
  const row = (id: string, change: Partial<RfpTicketSummary> = {}): RfpTicketSummary => ({ ...mergeTicketEvent([], ticket(id))[0], ...change })

  it('añade el ticket nuevo arriba y no lo duplica ni retrocede su estado si el evento se repite', () => {
    const list = mergeTicketEvent([row('viejo', { created_at: '2026-11-22T10:00:00+00:00' })], ticket('nuevo'))
    expect(list.map((t) => [t.ticket_id, t.status])).toEqual([['nuevo', 'analizando'], ['viejo', 'analizando']])
    const advanced = patchTickets(list, [row('nuevo', { status: 'terminado', original_filename: 'luna.pdf' })])
    const again = mergeTicketEvent(advanced, ticket('nuevo'))
    expect(again).toHaveLength(2)
    expect(again[0]).toMatchObject({ status: 'terminado', original_filename: 'luna.pdf' })
  })

  it('recargar la lista no borra un ticket que llegó por el stream mientras la petición estaba en vuelo', () => {
    const fetched = [row('viejo', { created_at: '2026-11-23T09:00:00+00:00', status: 'terminado' })]
    const current = [row('nuevo'), row('viejo', { created_at: '2026-11-23T09:00:00+00:00' }), row('muy-viejo', { created_at: '2026-01-01T00:00:00Z' })]
    expect(mergeTicketList(current, fetched).map((t) => [t.ticket_id, t.status])).toEqual([['nuevo', 'analizando'], ['viejo', 'terminado']])
  })
})

describe('notificaciones', () => {
  const token = (sub: string) => `h.${btoa(JSON.stringify({ sub }))}.s`
  const ids = () => rfpLiveStore.getSnapshot().notifications.map((n) => n.ticket_id)

  async function page(steps: Step[]) {
    const { requests, fetchStream } = server(steps)
    ;(apiFetch as jest.Mock).mockImplementation(fetchStream)
    const stop = startRfpLive()
    await tick(200)
    return { requests, stop }
  }

  beforeEach(() => { sessionStorage.clear(); localStorage.setItem('trackflow.accessToken', token('7')) })

  it('el mismo ticket_id se notifica una sola vez, también tras recargar la página o descartarlo', async () => {
    const first = await page([{ chunks: ['id: 1\n\n', created(2, ticket('a')), created(2, ticket('a')), created(3, ticket('b'))], stay: true }])
    expect(ids()).toEqual(['b', 'a'])
    dismissRfp('a')
    first.stop()
    const second = await page([{ chunks: [created(2, ticket('a')), created(3, ticket('b')), created(4, ticket('c'))], stay: true }])
    expect(second.requests[0].headers['Last-Event-ID']).toBe('3')   // reanuda donde se quedó la página anterior
    expect(ids()).toEqual(['c', 'b'])
    second.stop()
    localStorage.setItem('trackflow.accessToken', token('8'))        // otra persona en la misma pestaña: no hereda nada
    const other = await page([{ stay: true }])
    expect([ids(), other.requests[0].headers['Last-Event-ID']]).toEqual([[], undefined])
    other.stop()
  })

  it('la primera conexión y un reset piden la lista; un evento normal no', async () => {
    let resyncs = 0
    const tickets: string[] = []
    const off = listenRfpLive({ onTicket: (t) => tickets.push(t.ticket_id), onResync: () => { resyncs += 1 }, onOpen: () => {} })
    const one = await page([{ chunks: ['id: 1\n\n', created(2, ticket('a')), created(3, ticket('b'))], stay: true }])
    expect([resyncs, tickets]).toEqual([1, ['a', 'b']])
    one.stop()
    const two = await page([{ chunks: [reset(90)], stay: true }])
    expect([resyncs, rfpLiveStore.getSnapshot().gap]).toEqual([3, true])
    two.stop(); off()
  })
})
