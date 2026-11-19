import { BookOpenText, LoaderCircle, Send } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AsyncErrorState } from '../../components/AsyncErrorState'
import {
  askKnowledgeBase,
  KNOWLEDGE_QUESTION_MAX_LENGTH,
  knowledgeExampleQuestions,
  validateKnowledgeQuestion,
} from '../../utils/knowledge'

type AssistantState =
  | { status: 'idle' }
  | { status: 'loading'; question: string }
  | { status: 'success'; question: string; answer: string }
  | { status: 'error'; question: string; message: string }

const focusRing = 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 dark:focus-visible:outline-emerald-400'

export function KnowledgeAssistant() {
  const [question, setQuestion] = useState('')
  const [validationError, setValidationError] = useState('')
  const [state, setState] = useState<AssistantState>({ status: 'idle' })
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => () => controllerRef.current?.abort(), [])

  async function ask(text: string) {
    const problem = validateKnowledgeQuestion(text)
    if (problem) {
      setValidationError(problem)
      return
    }
    setValidationError('')
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const asked = text.trim()
    setState({ status: 'loading', question: asked })
    try {
      const answer = await askKnowledgeBase(asked, controller.signal)
      if (!controller.signal.aborted) setState({ status: 'success', question: asked, answer })
    } catch (cause) {
      if (controller.signal.aborted) return
      setState({
        status: 'error',
        question: asked,
        message: cause instanceof Error && cause.message ? cause.message : 'No se pudo consultar la base de conocimiento.',
      })
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void ask(question)
  }

  const loading = state.status === 'loading'

  return (
    <section aria-labelledby="knowledge-title" className="rounded-2xl border border-slate-200 bg-white p-6 text-slate-900 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
      <div className="flex items-start gap-3">
        <BookOpenText className="mt-1 shrink-0 text-emerald-800 dark:text-emerald-400" size={24} aria-hidden="true" />
        <div>
          <h2 id="knowledge-title" className="font-display text-2xl font-bold">Asistente comercial</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-600 dark:text-slate-300">
            Consulta SLA de entrega, política de devoluciones, cobertura de transportistas y tarifas de almacenamiento.
            Las respuestas salen solo de los acuerdos estándar de TrackFlow y citan su fuente.
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="mt-6 space-y-3" noValidate>
        <label htmlFor="knowledge-question" className="block text-sm font-semibold">Pregunta del cliente</label>
        <textarea
          id="knowledge-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault()
              void ask(question)
            }
          }}
          rows={3}
          maxLength={KNOWLEDGE_QUESTION_MAX_LENGTH}
          aria-invalid={validationError ? true : undefined}
          aria-describedby={validationError ? 'knowledge-question-error' : 'knowledge-question-hint'}
          placeholder="Ej.: ¿cuál es la ventana de devolución estándar?"
          className={`w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-500 dark:border-slate-600 dark:bg-slate-950 dark:text-slate-100 dark:placeholder:text-slate-400 ${focusRing}`}
        />
        {validationError
          ? <p id="knowledge-question-error" role="alert" className="text-sm text-red-800 dark:text-red-300">{validationError}</p>
          : <p id="knowledge-question-hint" className="text-xs text-slate-500 dark:text-slate-400">Ctrl/⌘ + Enter para enviar.</p>}
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={loading}
            className={`inline-flex min-h-11 items-center gap-2 rounded-md bg-emerald-800 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-900 disabled:opacity-60 dark:bg-emerald-700 dark:hover:bg-emerald-600 ${focusRing}`}
          >
            {loading ? <LoaderCircle className="animate-spin" size={16} aria-hidden="true" /> : <Send size={16} aria-hidden="true" />}
            {loading ? 'Consultando…' : 'Preguntar'}
          </button>
        </div>
      </form>

      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Preguntas frecuentes</p>
        <ul className="mt-2 flex flex-wrap gap-2">
          {knowledgeExampleQuestions.map((example) => (
            <li key={example}>
              <button
                type="button"
                disabled={loading}
                onClick={() => { setQuestion(example); void ask(example) }}
                className={`rounded-full border border-slate-300 px-3 py-1.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-60 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800 ${focusRing}`}
              >
                {example}
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-6" aria-live="polite">
        {state.status === 'loading' && (
          <p role="status" className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            <LoaderCircle className="animate-spin" size={16} aria-hidden="true" />
            Buscando en la base de conocimiento y redactando la respuesta…
          </p>
        )}
        {state.status === 'error' && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950/40">
            <p className="mb-2 text-sm font-semibold text-red-900 dark:text-red-200">No se pudo responder: «{state.question}»</p>
            <AsyncErrorState
              message={state.message}
              onRetry={() => void ask(state.question)}
              buttonClassName={`inline-flex min-h-11 items-center gap-2 rounded-md border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-900 hover:bg-red-100 dark:border-red-800 dark:bg-red-950 dark:text-red-100 dark:hover:bg-red-900 ${focusRing}`}
            />
          </div>
        )}
        {state.status === 'success' && (
          <article aria-label="Respuesta del asistente" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/40">
            <p className="text-sm font-semibold text-emerald-900 dark:text-emerald-300">Respuesta a «{state.question}»</p>
            <p className="mt-2 whitespace-pre-line text-sm leading-6 text-slate-900 dark:text-slate-100">{state.answer}</p>
          </article>
        )}
      </div>
    </section>
  )
}
