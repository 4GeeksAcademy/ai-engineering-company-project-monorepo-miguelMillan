import { LoaderCircle, MessagesSquare, Send, Square, SquarePen } from 'lucide-react'
import { useEffect, useReducer, useRef, useState, type FormEvent } from 'react'
import { chatReducer, closeChatSession, createChatSession, initialChatState, isAnswering, storedChatSession, type ChatMessage } from '../../utils/chat'
import { startChatSocket, type ChatConnection, type ChatStatus } from '../../utils/chatSocket'
import { clearSession, getSessionToken } from '../../utils/auth'
import { KNOWLEDGE_QUESTION_MAX_LENGTH, knowledgeExampleQuestions, validateKnowledgeQuestion } from '../../utils/knowledge'

const focusRing = 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 dark:focus-visible:outline-emerald-400'

function statusLabel(status: ChatStatus): string {
  if (status.state === 'live') return 'Conectado'
  if (status.state === 'reconnecting') return `Sin conexión · reintento ${status.attempt} en ${Math.max(1, Math.round(status.delayMs / 1000))} s`
  return status.state === 'closed' ? 'Desconectado' : 'Conectando…'
}

function Bubble({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return <p data-testid="chat-user" className="ml-auto max-w-[85%] whitespace-pre-line rounded-2xl rounded-br-sm bg-slate-800 px-4 py-2 text-sm text-white">{message.text}</p>
  }
  const interrupted = message.status === 'interrupted'
  return (
    <div data-testid="chat-assistant" data-status={message.status} data-message={message.message_id ?? ''}
      className={`max-w-[85%] rounded-2xl rounded-bl-sm border px-4 py-2 text-sm leading-6 ${interrupted ? 'border-amber-400 bg-amber-50 text-slate-700 dark:bg-amber-950/30 dark:text-slate-200'
        : message.status === 'failed' ? 'border-red-300 bg-red-50 dark:bg-red-950/30' : 'border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40'}`}>
      {message.text
        ? <p className="whitespace-pre-line">{message.text}{message.status === 'streaming' && <span aria-hidden="true" className="ml-0.5 animate-pulse">▍</span>}</p>
        : message.status === 'streaming' && <p role="status" className="flex items-center gap-2 text-slate-600 dark:text-slate-300"><LoaderCircle className="animate-spin" size={16} aria-hidden="true" />Pensando…</p>}
      {interrupted && <p className="mt-1 text-xs font-semibold text-amber-900 dark:text-amber-300">⏹ Respuesta interrumpida{message.text ? '' : ' antes de empezar'}</p>}
      {message.status === 'failed' && <p className="mt-1 text-xs font-semibold text-red-900 dark:text-red-300">No se pudo completar esta respuesta.</p>}
    </div>
  )
}

/** Chat con el First-line CX agent: respuesta en vivo, interrumpible, sobre una conexión WebSocket por sesión. */
export function KnowledgeAssistant() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const [status, setStatus] = useState<ChatStatus>({ state: 'connecting' })
  const [sessionId, setSessionId] = useState<string | null>(storedChatSession)
  const [problem, setProblem] = useState('')
  const connection = useRef<ChatConnection | null>(null)
  const end = useRef<HTMLDivElement | null>(null)
  const answering = isAnswering(state)

  useEffect(() => {
    if (sessionId) return
    let cancelled = false
    createChatSession().then((id) => { if (!cancelled) setSessionId(id) }).catch((e: Error) => { if (!cancelled) setProblem(e.message) })
    return () => { cancelled = true }
  }, [sessionId])

  useEffect(() => {
    if (!sessionId) return
    const socket = startChatSocket({
      sessionId, baseUrl: import.meta.env.VITE_API_URL as string | undefined, getToken: getSessionToken, onEvent: dispatch, onStatus: setStatus,
      onDisconnect: () => dispatch({ event: 'local/disconnected' }),
      onUnauthorized: () => { clearSession(); window.location.replace('/login?session=expired') },
      onSessionGone: () => { dispatch({ event: 'local/reset' }); setSessionId(null) },
    })
    connection.current = socket
    return () => { socket.stop(); connection.current = null }
  }, [sessionId])

  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [state.messages])

  function send(text: string) {
    const invalid = validateKnowledgeQuestion(text)
    if (invalid) { setProblem(invalid); return }
    const trimmed = text.trim()
    // Escribir mientras el agente responde lo redirige: se aborta y empieza un turno nuevo.
    const sent = answering
      ? connection.current?.send('interrupt_requested', { new_input: trimmed })
      : connection.current?.send('user_message', { text: trimmed })
    if (!sent) { setProblem('No hay conexión ahora mismo. Tu mensaje sigue aquí: envíalo al reconectar.'); return }
    setProblem('')
    dispatch({ event: 'local/sent', text: trimmed })
  }

  async function startOver() {
    if (!sessionId) return
    try {
      await closeChatSession(sessionId)
      dispatch({ event: 'local/reset' })
      setSessionId(null) // el efecto crea la siguiente
    } catch (e) {
      setProblem((e as Error).message)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    send(state.draft)
  }

  const warning = problem || state.notice

  return (
    <section aria-labelledby="knowledge-title" className="rounded-2xl border border-slate-200 bg-white p-6 text-slate-900 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
      <div className="flex flex-wrap items-start gap-3">
        <MessagesSquare className="mt-1 shrink-0 text-emerald-800 dark:text-emerald-400" size={24} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h2 id="knowledge-title" className="font-display text-2xl font-bold">Asistente CX</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-600 dark:text-slate-300">
            Primera línea de Customer Experience: tracking, devoluciones y preguntas frecuentes. Puedes escribir mientras responde para redirigirlo.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <p data-testid="chat-status" data-state={status.state} className={`flex items-center gap-2 text-xs font-medium ${state.ready ? 'text-emerald-800 dark:text-emerald-400' : 'text-amber-900 dark:text-amber-300'}`}>
            <span aria-hidden="true" className={`h-2 w-2 rounded-full ${state.ready ? 'bg-emerald-600' : 'bg-amber-500'}`} />{statusLabel(status)}
          </p>
          <button type="button" onClick={() => void startOver()} disabled={!state.ready || state.messages.length === 0}
            className={`inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold disabled:opacity-50 dark:border-slate-600 ${focusRing}`}>
            <SquarePen size={14} aria-hidden="true" /> Nueva conversación
          </button>
        </div>
      </div>

      <div data-testid="chat-log" role="log" aria-live="polite" aria-label="Conversación" className="mt-6 max-h-[28rem] min-h-40 space-y-3 overflow-y-auto rounded-xl border border-slate-200 p-4 dark:border-slate-700">
        {state.messages.length === 0 && <p className="text-sm text-slate-500 dark:text-slate-400">{state.ready ? 'Escribe tu consulta para empezar.' : 'Recuperando la conversación…'}</p>}
        {state.messages.map((message, index) => <Bubble key={index} message={message} />)}
        <div ref={end} />
      </div>

      <form onSubmit={handleSubmit} className="mt-4 space-y-3" noValidate>
        <label htmlFor="chat-input" className="block text-sm font-semibold">{answering ? 'Redirige la respuesta' : 'Tu mensaje'}</label>
        <textarea id="chat-input" value={state.draft} onChange={(event) => dispatch({ event: 'local/draft', text: event.target.value })} rows={2} maxLength={KNOWLEDGE_QUESTION_MAX_LENGTH}
          onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(state.draft) } }}
          placeholder={answering ? 'Ej.: espera, quiero hacer una devolución, no rastrear el pedido' : 'Ej.: ¿cuál es la ventana de devolución estándar?'}
          className={`w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-500 dark:border-slate-600 dark:bg-slate-950 dark:text-slate-100 ${focusRing}`} />
        {warning && <p role="alert" data-testid="chat-warning" className="text-sm text-red-800 dark:text-red-300">{warning}</p>}
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={!state.ready}
            className={`inline-flex min-h-11 items-center gap-2 rounded-md bg-emerald-800 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-900 disabled:opacity-60 dark:bg-emerald-700 ${focusRing}`}>
            <Send size={16} aria-hidden="true" />{answering ? 'Interrumpir y enviar' : 'Enviar'}
          </button>
          {answering && (
            <button type="button" disabled={!state.ready} onClick={() => connection.current?.send('interrupt_requested', { new_input: '' })}
              className={`inline-flex min-h-11 items-center gap-2 rounded-md border border-amber-500 px-4 py-2 text-sm font-semibold text-amber-950 hover:bg-amber-50 dark:text-amber-200 ${focusRing}`}>
              <Square size={14} aria-hidden="true" /> Detener
            </button>
          )}
          <span className="text-xs text-slate-500 dark:text-slate-400">Enter para enviar, Mayús + Enter para otra línea.</span>
        </div>
      </form>

      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Preguntas frecuentes</p>
        <ul className="mt-2 flex flex-wrap gap-2">
          {knowledgeExampleQuestions.map((example) => (
            <li key={example}>
              <button type="button" disabled={!state.ready} onClick={() => send(example)}
                className={`rounded-full border border-slate-300 px-3 py-1.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-60 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800 ${focusRing}`}>
                {example}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
