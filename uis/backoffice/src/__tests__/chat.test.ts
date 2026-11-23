jest.mock('../utils/auth', () => ({ apiRequest: jest.fn() }))

import { apiRequest } from '../utils/auth'
import { chatReducer, createChatSession, initialChatState, isAnswering, storedChatSession, type ChatAction, type ChatEvent, type ChatState } from '../utils/chat'
import { startChatSocket, type ChatSocketOptions, type ChatStatus } from '../utils/chatSocket'

const sid = 'chat_0219'
const snapshot = (messages: unknown[] = []): ChatEvent => ({ event: 'session_snapshot', data: { session_id: sid, messages: messages as never } })
const user = (text: string): ChatEvent => ({ event: 'user_message', data: { session_id: sid, text } })
const token = (text: string, sequence: number): ChatEvent => ({ event: 'token_chunk', data: { session_id: sid, token: text, sequence } })
const interrupted = (id: string): ChatEvent => ({ event: 'generation_interrupted', data: { session_id: sid, message_id: id, status: 'interrupted' } })
const completed = (id: string): ChatEvent => ({ event: 'generation_completed', data: { session_id: sid, message_id: id } })
const apply = (actions: ChatAction[], from: ChatState = initialChatState) => actions.reduce(chatReducer, from)
const view = (s: ChatState) => s.messages.map((m) => [m.role, m.text, m.status])

describe('conversación', () => {
  it('la respuesta crece token a token y solo al final queda completa', () => {
    const steps = [snapshot(), user('rastreo'), token('Tu', 1), token(' pedido', 2), completed('msg_1')]
    const texts = steps.map((_, i) => apply(steps.slice(0, i + 1)).messages.at(-1)?.text)
    expect(texts).toEqual([undefined, '', 'Tu', 'Tu pedido', 'Tu pedido'])
    expect(apply(steps).messages.at(-1)).toEqual({ message_id: 'msg_1', role: 'assistant', text: 'Tu pedido', status: 'completed' })
  })

  it('interrumpir conserva el parcial marcado y la respuesta redirigida es un mensaje nuevo', () => {
    const before = apply([snapshot(), user('rastreo'), token('Tu', 1), token(' pedido', 2)])
    expect(isAnswering(before)).toBe(true)
    const after = apply([interrupted('msg_0449'), user('quiero una devolución'), token('Claro', 1), completed('msg_0450')], before)
    expect(view(after)).toEqual([['user', 'rastreo', 'completed'], ['assistant', 'Tu pedido', 'interrupted'],
      ['user', 'quiero una devolución', 'completed'], ['assistant', 'Claro', 'completed']])
    expect(after.messages.map((m) => m.message_id)).toEqual([null, 'msg_0449', null, 'msg_0450'])
  })

  it('un token tardío, repetido o anterior a la rehidratación no se pinta', () => {
    const live = apply([snapshot(), user('rastreo'), token('Tu', 1), token('Tu', 1), token(' pedido', 2)])
    expect(live.messages.at(-1)?.text).toBe('Tu pedido')
    const cut = apply([interrupted('m'), token(' tarde', 3)], live)
    expect(cut.messages.at(-1)).toMatchObject({ text: 'Tu pedido', status: 'interrupted' })
    const offline = apply([{ event: 'local/disconnected' }, token(' no', 3)], live)
    expect(offline.messages.at(-1)?.text).toBe('Tu pedido')
  })

  it('reconectar restaura el hilo y sigue la respuesta donde iba, sin repetir', () => {
    const lost = apply([snapshot(), user('rastreo'), token('Tu', 1), { event: 'local/disconnected' }])
    const back = apply([snapshot([
      { message_id: 'u', role: 'user', text: 'rastreo', status: 'completed' },
      { message_id: 'a', role: 'assistant', text: 'Tu pedido salió', status: 'streaming', sequence: 3 },
    ]), token(' salió', 3), token(' ayer', 4), completed('a')], lost)
    expect(view(back)).toEqual([['user', 'rastreo', 'completed'], ['assistant', 'Tu pedido salió ayer', 'completed']])
  })

  it('un mensaje que no llegó a enviarse vuelve al cuadro de texto; uno que sí llegó, no', () => {
    const sent = apply([snapshot(), { event: 'local/sent', text: ' hola ' }, { event: 'local/disconnected' }])
    expect(apply([snapshot()], sent)).toMatchObject({ draft: 'hola', pending: null })
    expect(apply([snapshot([{ message_id: 'u', role: 'user', text: 'hola', status: 'completed' }])], sent)).toMatchObject({ draft: '', notice: '' })
    const repeated = apply([snapshot([{ message_id: 'u', role: 'user', text: 'hola', status: 'completed' }]), { event: 'local/sent', text: 'hola' }, { event: 'local/disconnected' }])
    expect(apply([snapshot()], apply([{ event: 'local/draft', text: 'otra cosa' }], sent)).draft).toBe('otra cosa')   // no pisa lo que se está escribiendo
    expect(apply([snapshot([{ message_id: 'u', role: 'user', text: 'hola', status: 'completed' }])], repeated).draft).toBe('hola')
  })

  it('un fallo del agente conserva el parcial y lo marca', () => {
    const state = apply([snapshot(), user('x'), token('Tu', 1), { event: 'error', data: { session_id: sid, code: 'agent_unavailable', message: 'No disponible', message_id: 'm' } }])
    expect(state).toMatchObject({ notice: 'No disponible' })
    expect(state.messages.at(-1)).toMatchObject({ text: 'Tu', status: 'failed' })
  })
})

// ---------------------------------------------------------------- conexión
class FakeSocket {
  static all: FakeSocket[] = []
  readyState = 1
  sent: { event: string; data: Record<string, string> }[] = []
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((m: { data: string }) => void) | null = null
  onclose: ((c: { code: number }) => void) | null = null
  onerror: (() => void) | null = null
  url: string
  constructor(url: string) { this.url = url; FakeSocket.all.push(this) }
  send(data: string) { this.sent.push(JSON.parse(data)) }
  close() { this.closed = true }
  open() { this.onopen?.() }
  say(event: ChatEvent | string) { this.onmessage?.({ data: typeof event === 'string' ? event : JSON.stringify(event) }) }
  drop(code = 1006) { this.onclose?.({ code }) }
}
const tick = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }

function start(options: Partial<ChatSocketOptions> = {}) {
  FakeSocket.all = []
  const log = { events: [] as string[], delays: [] as number[], statuses: [] as ChatStatus[], disconnects: 0, unauthorized: 0, gone: 0 }
  const connection = startChatSocket({
    sessionId: sid, getToken: () => 'jwt', onEvent: (e) => log.events.push(e.event), onStatus: (s) => log.statuses.push(s),
    onDisconnect: () => { log.disconnects += 1 }, onUnauthorized: () => { log.unauthorized += 1 }, onSessionGone: () => { log.gone += 1 },
    createSocket: (url) => new FakeSocket(url) as unknown as WebSocket, sleep: async (ms) => { log.delays.push(ms) }, random: () => 1, ...options,
  })
  return { connection, log, last: () => FakeSocket.all.at(-1) as FakeSocket }
}

describe('conexión WebSocket', () => {
  it('lleva session_id en la URL y el JWT en el primer frame, nunca en la URL', async () => {
    const { connection, last } = start()
    last().open()
    expect(last().url).toBe(`ws://localhost/chat/ws?session_id=${sid}`)
    expect(last().sent).toEqual([{ event: 'auth', data: { token: 'jwt' } }])
    connection.stop()
  })

  it('no está viva ni deja enviar hasta recibir el snapshot', () => {
    const { connection, log, last } = start()
    last().open()
    last().say(token('antes', 1))
    expect([log.events, connection.send('user_message', { text: 'hola' })]).toEqual([[], false])
    last().say(snapshot())
    expect(connection.send('interrupt_requested', { new_input: 'otra cosa' })).toBe(true)
    expect(last().sent.at(-1)).toEqual({ event: 'interrupt_requested', data: { session_id: sid, new_input: 'otra cosa' } })
    last().say('no json'); last().say(JSON.stringify({ event: 'token_chunk', data: { session_id: 'otra' } }))
    expect(log.events).toEqual(['session_snapshot'])
    connection.stop()
  })

  it('reconecta con backoff progresivo y el mismo session_id, y se autentica otra vez', async () => {
    const { connection, log, last } = start()
    last().open(); last().say(snapshot())
    for (let i = 0; i < 4; i++) { last().drop(); await tick() }
    expect(log.delays).toEqual([1000, 2000, 4000, 8000])
    expect(new Set(FakeSocket.all.map((s) => s.url)).size).toBe(1)
    last().open()
    expect(last().sent[0].event).toBe('auth')
    last().say(snapshot()); last().say(user('hola'))          // conversación viva: el backoff vuelve a empezar
    last().drop(); await tick()
    expect(log.delays.at(-1)).toBe(1000)
    expect(log.disconnects).toBe(5)
    connection.stop()
  })

  it('una conexión que se rehidrata y cae enseguida no reintenta sin freno', async () => {
    const { connection, log, last } = start()
    for (let i = 0; i < 3; i++) { last().open(); last().say(snapshot()); last().drop(); await tick() }
    expect(log.delays).toEqual([1000, 2000, 4000])
    connection.stop()
  })

  it('detecta una conexión muerta que nadie cerró', async () => {
    jest.useFakeTimers()
    const { connection, log, last } = start({ pingMs: 15_000, staleMs: 40_000 })
    const first = last()
    first.open(); first.say(snapshot())
    jest.advanceTimersByTime(15_000)
    expect(first.sent.at(-1)).toEqual({ event: 'ping', data: { session_id: sid } })
    first.say({ event: 'pong', data: { session_id: sid } })
    jest.advanceTimersByTime(39_000); await tick()
    expect(FakeSocket.all).toHaveLength(1)
    jest.advanceTimersByTime(2_000); await tick()
    expect([FakeSocket.all.length, first.closed, log.disconnects]).toEqual([2, true, 1])
    connection.stop()
    jest.useRealTimers()
  })

  it.each([[4401, 'unauthorized'], [4404, 'gone'], [4410, 'gone']] as const)('el cierre %i no se reintenta', async (code, what) => {
    const { log, last } = start()
    last().open(); last().drop(code); await tick()
    expect([log[what], log.delays, FakeSocket.all.length, log.statuses.at(-1)]).toEqual([1, [], 1, { state: 'closed' }])
  })

  it('parar la conexión no reconecta', async () => {
    const { connection, log, last } = start()
    last().open(); last().say(snapshot()); connection.stop(); await tick()
    expect([FakeSocket.all.length, last().closed, log.delays]).toEqual([1, true, []])
  })
})

describe('sesión guardada', () => {
  it('se reutiliza al recargar, pero no la hereda otra persona', async () => {
    const jwt = (sub: string) => `h.${btoa(JSON.stringify({ sub }))}.s`
    localStorage.clear()
    localStorage.setItem('trackflow.accessToken', jwt('1'))
    expect(storedChatSession()).toBeNull()
    ;(apiRequest as jest.Mock).mockResolvedValue({ session_id: sid })
    await createChatSession()
    expect(storedChatSession()).toBe(sid)
    localStorage.setItem('trackflow.accessToken', jwt('2'))
    expect(storedChatSession()).toBeNull()
  })

  it('dos montajes simultáneos comparten una sola sesión; tras un fallo se puede reintentar', async () => {
    const request = apiRequest as jest.Mock
    request.mockReset()
    let n = 0
    request.mockImplementation(async () => ({ session_id: `chat_${++n}` }))
    expect(await Promise.all([createChatSession(), createChatSession()])).toEqual(['chat_1', 'chat_1'])
    expect([storedChatSession(), await createChatSession(), storedChatSession()]).toEqual(['chat_1', 'chat_2', 'chat_2'])
    request.mockRejectedValueOnce(new Error('sin red'))
    await expect(createChatSession()).rejects.toThrow('sin red')
    expect(await createChatSession()).toBe('chat_3')
  })
})
