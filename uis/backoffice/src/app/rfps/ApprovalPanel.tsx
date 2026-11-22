import { useState } from 'react'
import {
  APPROVAL_STATUS_LABELS,
  DECISION_LABELS,
  decideSection,
  type Decision,
  type RfpSection,
} from '../../utils/rfps'

const tones: Record<string, string> = {
  aprobada: 'bg-emerald-100 text-emerald-900',
  rechazada: 'bg-rose-100 text-rose-900',
  'esperando_aprobación': 'bg-sky-100 text-sky-950',
  procesando: 'bg-amber-100 text-amber-950',
  en_revision: 'bg-amber-100 text-amber-950',
}

/** Punto de aprobación humana de UN departamento: lo mínimo para decidir y el formulario del responsable. */
export function ApprovalPanel({ ticketId, section, onDecided }: { ticketId: string; section: RfpSection; onDecided: () => void }) {
  const request = section.approval_request
  const [decision, setDecision] = useState<Decision>('approve')
  const [comment, setComment] = useState('')
  const [commitments, setCommitments] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  if (section.approval_status === 'pendiente') return null

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!request) return
    setSending(true); setError('')
    try {
      const numeric = Object.fromEntries(Object.entries(commitments).filter(([, v]) => v.trim()).map(([k, v]) => [k, Number(v)]))
      await decideSection(ticketId, section.department_id, {
        decision, approver: request.approver, section_version: request.version, comment, commitments: numeric,
      })
      setComment(''); setCommitments({})
      onDecided()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSending(false)
    }
  }

  return (
    <div data-testid="rfp-approval" className="mt-4 space-y-3 rounded-md border border-sky-200 bg-sky-50/40 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-semibold text-slate-900">Aprobación del responsable · {section.approver}</h4>
        <span data-testid="rfp-approval-status" className={`rounded-full px-2.5 py-1 text-xs font-semibold ${tones[section.approval_status] ?? 'bg-slate-100'}`}>
          {APPROVAL_STATUS_LABELS[section.approval_status] ?? section.approval_status}
          {section.approval_status === 'aprobada' && section.approved_at && ` · ${new Date(section.approved_at).toLocaleString('es-ES')}`}
        </span>
      </div>

      {section.approval_status === 'esperando_aprobación' && request && (
        <>
          <dl className="grid gap-2 text-sm sm:grid-cols-3">
            <div><dt className="text-slate-500">Versión</dt><dd className="font-medium">v{request.version} · ronda {request.round}/{request.max_rounds}</dd></div>
            <div><dt className="text-slate-500">Moneda</dt><dd className="font-medium">{request.client.currency} ({request.client.country})</dd></div>
            <div><dt className="text-slate-500">Evaluación automática</dt>
              <dd className={`font-medium ${request.evaluation.overall_pass ? 'text-emerald-800' : 'text-rose-800'}`}>
                {request.evaluation.overall_pass ? 'Superada' : `No superada${request.evaluation.compliance.rule_ids.length ? ` (${request.evaluation.compliance.rule_ids.join(', ')})` : ''}`}
              </dd></div>
          </dl>
          {request.provisional && <p role="note" className="rounded bg-amber-100 px-3 py-2 text-sm text-amber-950">Borrador PROVISIONAL: no superó la evaluación automática. Revisa lo marcado antes de aprobar.</p>}
          {request.why_this_version && <p className="text-sm text-slate-700"><strong>Por qué hay una nueva versión:</strong> {request.why_this_version}</p>}
          {request.arbitration.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-orange-950">
              {request.arbitration.map((a, i) => <li key={i}><strong>Arbitraje {a.trigger}</strong> (árbitro {a.arbiter}): {a.resolution}</li>)}
            </ul>
          )}
          {request.pending_items.length > 0 && (
            <div className="text-sm"><p className="font-medium text-slate-800">Importes pendientes en esta sección</p>
              <ul className="list-disc pl-5">{request.pending_items.map((item) => <li key={item.concept}>{item.concept}: {item.value}</li>)}</ul></div>
          )}

          <form onSubmit={submit} className="space-y-3" aria-label={`Decisión de ${section.approver}`}>
            <fieldset className="flex flex-wrap gap-4 text-sm">
              <legend className="sr-only">Decisión</legend>
              {request.allowed_decisions.map((option) => (
                <label key={option} className="inline-flex items-center gap-1.5">
                  <input type="radio" name={`decision-${section.department_id}`} value={option} checked={decision === option}
                    onChange={() => setDecision(option)} /> {DECISION_LABELS[option]}
                </label>
              ))}
            </fieldset>
            {Object.entries(request.commitment_fields).length > 0 && decision === 'approve' && (
              <div className="flex flex-wrap gap-3">
                {Object.entries(request.commitment_fields).map(([key, label]) => (
                  <label key={key} className="flex flex-col text-xs font-medium text-slate-700">{label} (opcional)
                    <input type="number" min="1" step="1" name={key} value={commitments[key] ?? ''}
                      onChange={(e) => setCommitments({ ...commitments, [key]: e.target.value })}
                      className="mt-1 w-48 rounded border border-slate-300 px-2 py-1 text-sm" />
                  </label>
                ))}
              </div>
            )}
            <label className="flex flex-col text-xs font-medium text-slate-700">
              Comentario {decision === 'approve' ? '(opcional)' : '(obligatorio: es el feedback que recibirá el generador)'}
              <textarea name="comment" value={comment} onChange={(e) => setComment(e.target.value)} rows={2}
                className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm" />
            </label>
            <button type="submit" disabled={sending}
              className="rounded-md bg-sky-800 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60">
              {sending ? 'Enviando…' : `${DECISION_LABELS[decision]} como ${request.approver}`}
            </button>
            {error && <p role="alert" className="text-sm text-rose-800">{error}</p>}
          </form>
        </>
      )}

      {section.decision_history.length > 0 && (
        <ol className="text-xs text-slate-600">
          {section.decision_history.map((d, i) => (
            <li key={i}>v{d.version}: {DECISION_LABELS[d.decision]} por {d.approver}{d.decided_by ? ` (registrado por ${d.decided_by})` : ''} · {new Date(d.decided_at).toLocaleString('es-ES')}{d.comment ? ` — «${d.comment}»` : ''}</li>
          ))}
        </ol>
      )}
    </div>
  )
}
