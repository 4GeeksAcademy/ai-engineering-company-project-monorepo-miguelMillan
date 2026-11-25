import { useCallback, useEffect, useState } from 'react'
import { ACTION_LABELS, decideAction, listPendingActions, type PendingAction } from '../utils/actions'

/** Acciones irreversibles que esperan a que otra persona las confirme (CONTEXT §5). */
export function PendingActions() {
  const [actions, setActions] = useState<PendingAction[] | null>(null)
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const load = useCallback((signal?: AbortSignal) => listPendingActions(signal)
    .then(setActions)
    .catch((cause: Error) => { if (!signal?.aborted) setError(cause.message) }), [])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  async function decide(id: string, decision: 'confirm' | 'reject') {
    setBusy(id)
    setError('')
    try {
      await decideAction(id, decision, reasons[id].trim())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo registrar la decisión.')
    }
    await load()
    setBusy('')
  }

  return (
    <section aria-labelledby="pending-actions-title" data-testid="pending-actions" className="rounded-md border border-slate-200 bg-white p-5">
      <h2 id="pending-actions-title" className="font-display text-xl font-semibold">Acciones pendientes de confirmación</h2>
      <p className="mt-1 text-sm text-slate-600">No se ejecutan hasta que otra persona las confirme. Lo que decidas queda registrado.</p>
      {error && <p role="alert" className="mt-3 text-sm font-medium text-red-800">{error}</p>}
      {actions?.length === 0 && <p className="mt-3 text-sm">No hay ninguna.</p>}
      <ul className="mt-3 space-y-3">
        {actions?.map((action) => {
          const ready = (reasons[action.id] ?? '').trim().length >= 3 && busy === ''
          return (
            <li key={action.id} className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm">
              <p className="font-semibold">{ACTION_LABELS[action.kind] ?? action.kind}</p>
              <p>{action.summary}</p>
              <p className="text-slate-600">Solicitada por {action.requested_by} ({action.requested_role}); caduca el {new Date(action.expires_at).toLocaleString('es-ES')}</p>
              <label className="mt-2 block font-medium" htmlFor={`reason-${action.id}`}>Motivo</label>
              <input id={`reason-${action.id}`} type="text" maxLength={500} value={reasons[action.id] ?? ''} onChange={(event) => setReasons({ ...reasons, [action.id]: event.target.value })} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" />
              <div className="mt-2 flex gap-2">
                <button type="button" disabled={!ready} onClick={() => decide(action.id, 'confirm')} className="min-h-10 rounded-md bg-emerald-800 px-3 py-2 font-semibold text-white disabled:opacity-50">Confirmar</button>
                <button type="button" disabled={!ready} onClick={() => decide(action.id, 'reject')} className="min-h-10 rounded-md border border-slate-400 px-3 py-2 font-semibold disabled:opacity-50">Rechazar</button>
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
