import { apiRequest } from './auth'
import { getSessionToken } from './session'

/** ChatSession (BRIEFING de tiempo real, Parte 2, §3). */
export type ChatSession = {
  session_id: string
  agent_id: string
  user_id: string
  client_id: string | null
  status: 'active' | 'interrupted' | 'closed'
  created_at: string
}

export type ChatMessage = {
  message_id: string | null // null mientras el servidor no lo ha comunicado
  role: 'user' | 'assistant'
  text: string
  status: 'streaming' | 'completed' | 'interrupted' | 'failed'
}

type SnapshotMessage = ChatMessage & { sequence?: number }

export type ChatEvent =
  | { event: 'session_snapshot'; data: { session_id: string; messages: SnapshotMessage[] } }
  | { event: 'user_message'; data: { session_id: string; text: string } }
  | { event: 'token_chunk'; data: { session_id: string; token: string; sequence: number } }
  | { event: 'generation_interrupted'; data: { session_id: string; message_id: string; status: 'interrupted' } }
  | { event: 'generation_completed'; data: { session_id: string; message_id: string } }
  | { event: 'error'; data: { session_id: string; code: string; message: string; message_id?: string } }
  | { event: 'pong'; data: { session_id: string } }

export type ChatState = {
  /** El hilo está rehidratado en esta conexión: solo entonces se aceptan tokens y se puede escribir. */
  ready: boolean
  messages: ChatMessage[]
  lastSequence: number
  /** Enviado y aún sin confirmar por el servidor. */
  pending: { text: string; after: number } | null
  /** Cuadro de texto. Lo que se cortó antes de llegar vuelve aquí en lugar de perderse. */
  draft: string
  notice: string
}

export const initialChatState: ChatState = { ready: false, messages: [], lastSequence: 0, pending: null, draft: '', notice: '' }

export type ChatAction =
  | ChatEvent
  | { event: 'local/disconnected' }
  | { event: 'local/sent'; text: string }
  | { event: 'local/draft'; text: string }
  | { event: 'local/reset' }

export function isAnswering(state: ChatState): boolean {
  return state.messages.at(-1)?.status === 'streaming'
}

/** Cierra la respuesta en curso. Las anteriores no se tocan: una interrumpida se queda como se vio. */
function closeAnswer(messages: ChatMessage[], status: ChatMessage['status'], messageId: string | null): ChatMessage[] {
  const last = messages.at(-1)
  return last?.status === 'streaming' ? [...messages.slice(0, -1), { ...last, status, message_id: messageId ?? last.message_id }] : messages
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.event) {
    case 'session_snapshot': {
      const messages = action.data.messages.map(({ message_id, role, text, status }) => ({ message_id, role, text, status }))
      const last = action.data.messages.at(-1)
      const lost = state.pending
        && !messages.filter((m) => m.role === 'user').slice(state.pending.after).some((m) => m.text === state.pending?.text)
      return {
        ...state, ready: true, messages, lastSequence: last?.status === 'streaming' ? last.sequence ?? 0 : 0, pending: null,
        draft: lost && state.pending ? state.draft || state.pending.text : state.draft,
        notice: lost ? 'Se cortó la conexión antes de enviar tu último mensaje: lo tienes de nuevo en el cuadro de texto.' : '',
      }
    }
    case 'user_message':
      return {
        ...state, lastSequence: 0, pending: null, notice: '',
        messages: [...state.messages,
          { message_id: null, role: 'user', text: action.data.text, status: 'completed' },
          { message_id: null, role: 'assistant', text: '', status: 'streaming' }],
      }
    case 'token_chunk': {
      const last = state.messages.at(-1)
      if (!state.ready || last?.status !== 'streaming' || action.data.sequence <= state.lastSequence) return state
      return { ...state, lastSequence: action.data.sequence, messages: [...state.messages.slice(0, -1), { ...last, text: last.text + action.data.token }] }
    }
    case 'generation_interrupted':
      return { ...state, messages: closeAnswer(state.messages, 'interrupted', action.data.message_id) }
    case 'generation_completed':
      return { ...state, messages: closeAnswer(state.messages, 'completed', action.data.message_id) }
    case 'error':
      return {
        ...state, pending: null, notice: action.data.message,
        messages: action.data.message_id ? closeAnswer(state.messages, 'failed', action.data.message_id) : state.messages,
        draft: !action.data.message_id && state.pending ? state.draft || state.pending.text : state.draft,
      }
    case 'local/disconnected':
      return { ...state, ready: false }
    case 'local/sent':
      return { ...state, notice: '', draft: '', pending: { text: action.text.trim(), after: state.messages.filter((m) => m.role === 'user').length } }
    case 'local/draft':
      return { ...state, draft: action.text }
    case 'local/reset':
      return initialChatState
    default:
      return state
  }
}

// ---------------------------------------------------------------- sesión: la misma al recargar o reconectar
const storageKey = 'trackflow.chatSession'

function currentUser(): string {
  try {
    return String(JSON.parse(atob((getSessionToken() ?? '').split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).sub)
  } catch {
    return ''
  }
}

export function storedChatSession(): string | null {
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) ?? 'null') as { user: string; session_id: string } | null
    return stored && stored.user === currentUser() && typeof stored.session_id === 'string' ? stored.session_id : null
  } catch {
    return null
  }
}

let creating: Promise<string> | null = null

/** Crea la sesión. Las llamadas simultáneas (StrictMode monta dos veces) comparten una: si no, la guardada podría no ser
 *  la del socket y recargar perdería el hilo. */
export function createChatSession(): Promise<string> {
  creating ??= apiRequest<ChatSession>('/chat/sessions', { method: 'POST' }).then((session) => {
    localStorage.setItem(storageKey, JSON.stringify({ user: currentUser(), session_id: session.session_id }))
    return session.session_id
  }).finally(() => { creating = null })
  return creating
}

export async function closeChatSession(sessionId: string): Promise<void> {
  await apiRequest(`/chat/sessions/${encodeURIComponent(sessionId)}/close`, { method: 'POST' })
}
