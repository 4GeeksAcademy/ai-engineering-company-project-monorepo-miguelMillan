import { FileUp, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { ApprovalPanel } from './ApprovalPanel'
import { FinalDocumentPanel } from './FinalDocumentPanel'
import { SectionDraft } from './SectionDraft'
import { TracePanel } from './TracePanel'
import { mergeTicketEvent, mergeTicketList, patchTickets } from '../../utils/rfpEvents'
import { listenRfpLive, rfpLiveStore } from '../../utils/rfpLive'
import {
  DEPARTMENT_LABELS,
  STATUS_LABELS,
  approvalProgress,
  canGenerate,
  canStartApproval,
  startApproval,
  generateRfpResponse,
  generationFailed,
  getRfpTicket,
  isPending,
  listRfpTickets,
  reprocessRfpTicket,
  uploadRfp,
  validateRfpFile,
  type RfpTicketDetail,
  type RfpTicketSummary,
} from '../../utils/rfps'

const POLL_MS = 3000

function StatusBadge({ ticket }: { ticket: RfpTicketSummary }) {
  const failed = ticket.status === 'analizando' && ticket.pipeline_stage === 'fallido'
  const genFailed = generationFailed(ticket)
  const approvalFailed = ticket.approval_stage === 'fallido'
  const tone = failed || genFailed || approvalFailed ? 'bg-rose-100 text-rose-900'
    : ticket.status === 'terminado' ? 'bg-emerald-700 text-white'
    : ['analisis_completo', 'esperando_aprobación'].includes(ticket.status) ? 'bg-emerald-100 text-emerald-900'
      : ticket.status === 'needs_human_review' ? 'bg-orange-100 text-orange-950'
        : ticket.status === 'descartado' ? 'bg-slate-200 text-slate-800' : 'bg-amber-100 text-amber-900'
  const stage = ticket.status === 'analizando' ? ticket.pipeline_stage
    : ticket.approval_stage && ['iniciando', 'en_curso'].includes(ticket.approval_stage) ? `aprobación ${ticket.approval_stage}` : ticket.response_stage
  return (
    <span data-testid="rfp-status" className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>
      {failed ? 'Análisis fallido' : genFailed ? 'Generación fallida' : approvalFailed ? 'Aprobación fallida' : STATUS_LABELS[ticket.status] ?? ticket.status}
      {isPending(ticket) && stage && <span className="ml-1 font-normal">· {stage}</span>}
    </span>
  )
}

function TicketDetail({ ticketId, onReprocess, refreshKey }: { ticketId: string; onReprocess: () => void; refreshKey?: string }) {
  const [detail, setDetail] = useState<RfpTicketDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    const load = () => getRfpTicket(ticketId).then((d) => { if (!cancelled) setDetail(d) }).catch((e: Error) => setError(e.message))
    load()
    const timer = window.setInterval(() => { if (!detail || isPending(detail)) load() }, POLL_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [ticketId, detail?.status, detail?.pipeline_stage, detail?.response_stage, detail?.approval_stage, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <p role="alert" className="text-sm text-rose-800">{error}</p>
  if (!detail) return <p role="status" className="text-sm text-slate-600">Cargando ticket…</p>
  const meta = detail.metadata
  return (
    <section aria-label={`Ticket ${detail.rfp_id}`} className="space-y-5 rounded-lg border border-slate-200 bg-white p-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">{meta?.client_name ?? detail.original_filename}</h2>
          <p className="text-sm text-slate-600">{detail.rfp_id} · {detail.original_filename}</p>
        </div>
        <StatusBadge ticket={detail} />
      </header>

      {detail.status === 'descartado' && (
        <div className="rounded-md bg-slate-100 p-3 text-sm text-slate-800">
          <p><strong>Descartado por el clasificador:</strong> {detail.discard_reason}</p>
          <button type="button" onClick={() => reprocessRfpTicket(detail.ticket_id, true).then(onReprocess)}
            className="mt-2 rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-800">Es una RFP: aceptar y analizar</button>
        </div>
      )}
      {detail.last_error && !generationFailed(detail) && detail.approval_stage !== 'fallido' && (
        <div role="alert" className="rounded-md bg-rose-50 p-3 text-sm text-rose-900">
          <p><strong>El análisis falló</strong> (etapa {detail.pipeline_stage}): {detail.last_error}</p>
          <button type="button" onClick={() => reprocessRfpTicket(detail.ticket_id).then(onReprocess)}
            className="mt-2 rounded-md border border-rose-300 px-3 py-1.5 text-xs font-semibold">Reintentar análisis</button>
        </div>
      )}
      {generationFailed(detail) && (
        <p role="alert" className="rounded-md bg-rose-50 p-3 text-sm text-rose-900"><strong>La generación de borradores falló:</strong> {detail.last_error}</p>
      )}
      {detail.response_handoff?.needs_human_review && detail.status !== 'terminado' && (
        <div role="alert" data-testid="rfp-needs-human-review" className="rounded-md border border-orange-300 bg-orange-50 p-3 text-sm text-orange-950">
          <p className="font-semibold">Revisión humana necesaria antes de enviar la propuesta</p>
          <p>Borradores PROVISIONALES (no superaron la evaluación tras {detail.response_handoff.max_iterations} iteraciones):{' '}
            {detail.response_handoff.needs_human_review_sections.map((id) => DEPARTMENT_LABELS[id] ?? id).join(', ')}.
            Pasan igual a aprobación con su último borrador y su evaluación.</p>
        </div>
      )}
      {detail.approval_thread_id && (
        <p data-testid="rfp-approval-progress" className="text-sm text-slate-700">
          <strong>Aprobación por departamento:</strong> {approvalProgress(detail.sections).approved}/{approvalProgress(detail.sections).total} aprobadas
          {detail.status === 'esperando_aprobación' && ' · el documento final se genera solo cuando aprueben todos los responsables'}
        </p>
      )}
      {detail.approval_stage === 'bloqueado' && detail.approval_note && (
        <p role="alert" className="rounded-md border border-orange-300 bg-orange-50 p-3 text-sm text-orange-950">{detail.approval_note}</p>
      )}
      {detail.approval_stage === 'fallido' && (
        <p role="alert" className="rounded-md bg-rose-50 p-3 text-sm text-rose-900"><strong>La aprobación falló:</strong> {detail.last_error}</p>
      )}
      {canStartApproval(detail) && (
        <button type="button" onClick={() => startApproval(detail.ticket_id).then(onReprocess).catch((e: Error) => setError(e.message))}
          className="rounded-md bg-sky-800 px-3 py-1.5 text-xs font-semibold text-white">Reiniciar aprobación (hilo nuevo)</button>
      )}
      {detail.arbitration.length > 0 && (
        <div data-testid="rfp-arbitration" className="rounded-md border border-orange-200 bg-orange-50/60 p-3 text-sm">
          <h3 className="font-semibold text-orange-950">Arbitraje (BRIEFING §7)</h3>
          <ul className="list-disc pl-5">
            {detail.arbitration.map((a, i) => (
              <li key={i}><strong>{a.trigger}</strong> · ronda {a.round} · árbitro {a.arbiter} · afecta a {a.affected_departments.join(', ')}: {a.resolution}</li>
            ))}
          </ul>
        </div>
      )}
      {detail.final_document && <FinalDocumentPanel ticketId={detail.ticket_id} />}
      {canGenerate(detail) && (
        <button type="button" onClick={() => generateRfpResponse(detail.ticket_id).then(onReprocess).catch((e: Error) => setError(e.message))}
          className="rounded-md bg-emerald-800 px-3 py-1.5 text-xs font-semibold text-white">
          {detail.response_handoff || generationFailed(detail) ? 'Regenerar borradores' : 'Generar borradores'}
        </button>
      )}

      {meta && (
        <dl className="grid gap-3 text-sm sm:grid-cols-3">
          <div><dt className="text-slate-500">País · moneda</dt><dd className="font-medium">{meta.client_country ?? '—'} · {meta.currency ?? '—'}</dd></div>
          <div><dt className="text-slate-500">Servicios</dt><dd className="font-medium">{meta.services_requested.join(', ') || '—'}</dd></div>
          <div><dt className="text-slate-500">Volumen mensual</dt><dd className="font-medium">{meta.monthly_volume ?? 'No indicado'}</dd></div>
          <div><dt className="text-slate-500">SKU</dt><dd className="font-medium">{meta.sku_count ?? 'No indicado'}</dd></div>
          <div><dt className="text-slate-500">Fecha límite</dt><dd className="font-medium">{meta.deadline ?? meta.deadline_text ?? 'No indicada'}</dd></div>
          <div><dt className="text-slate-500">Presupuesto</dt><dd className="font-medium">{meta.budget_range ?? 'No indicado'}</dd></div>
          <div><dt className="text-slate-500">Legibilidad</dt><dd className="font-medium">{meta.readability.words} palabras · costo {String(meta.readability.processing_cost)}</dd></div>
          {meta.classification && <div className="sm:col-span-2"><dt className="text-slate-500">Clasificación</dt><dd className="font-medium">{meta.classification.document_type} · {meta.classification.reason}{meta.classification.needs_sales_review ? ' (revisar)' : ''}</dd></div>}
        </dl>
      )}

      {detail.handoff && (
        <div className="space-y-2 rounded-md bg-emerald-50 p-4">
          <h3 className="font-semibold text-emerald-900">Resumen para Ventas</h3>
          <p data-testid="rfp-sales-summary" className="text-sm text-emerald-950">{detail.handoff.sales_summary}</p>
          {detail.handoff.conflicts.length > 0 && <p className="text-sm font-semibold text-rose-800">Contradicciones: {detail.handoff.conflicts.map((c) => c.metric).join(', ')}</p>}
          {detail.handoff.general_open_questions.length > 0 && <ul className="list-disc pl-5 text-sm">{detail.handoff.general_open_questions.map((q) => <li key={q}>{q}</li>)}</ul>}
        </div>
      )}

      {detail.sections.map((section) => (
        <article key={section.department_id} className="rounded-md border border-slate-200 p-4">
          <h3 className="font-semibold text-slate-900">{DEPARTMENT_LABELS[section.department_id] ?? section.department_id}</h3>
          <ul className="mt-2 space-y-1 text-sm">
            {section.key_aspects.map((aspect, index) => (
              <li key={index}><strong>{aspect.aspecto}:</strong> {aspect.detalle}{aspect.cita && <span className="text-slate-500"> — «{aspect.cita}»</span>}</li>
            ))}
          </ul>
          {section.open_questions.length > 0 && (
            <div className="mt-2 text-sm"><p className="font-medium text-amber-900">Preguntas abiertas al cliente</p>
              <ul className="list-disc pl-5">{section.open_questions.map((q) => <li key={q}>{q}</li>)}</ul></div>
          )}
          <SectionDraft section={section} maxIterations={detail.response_handoff?.max_iterations ?? null} />
          <ApprovalPanel ticketId={detail.ticket_id} section={section} onDecided={onReprocess} />
        </article>
      ))}
      <TracePanel key={`${detail.status}-${detail.approval_stage}`} ticketId={detail.ticket_id} />
    </section>
  )
}

export function RfpIntake() {
  const [tickets, setTickets] = useState<RfpTicketSummary[]>([])
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(window.location.search).get('ticket'))
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [refreshError, setRefreshError] = useState('') // se borra solo en cuanto un refresco vuelve a funcionar
  const [uploading, setUploading] = useState(false)

  const { notifications } = useSyncExternalStore(rfpLiveStore.subscribe, rfpLiveStore.getSnapshot)
  const unread = new Set(notifications.map((n) => n.ticket_id))

  const refresh = useCallback(() => listRfpTickets()
    .then((r) => { setTickets((current) => mergeTicketList(current, r.tickets)); setRefreshError('') })
    .catch((e: Error) => setRefreshError(e.message)), [])

  useEffect(() => { refresh() }, [refresh])
  // Tickets nuevos en tiempo real: cada evento añade su fila con su propio payload, sin volver a pedir la lista.
  useEffect(() => listenRfpLive({
    onTicket: (ticket) => setTickets((current) => mergeTicketEvent(current, ticket)),
    onResync: refresh,
    onOpen: setSelected,
  }), [refresh])
  // Los tickets en curso se refrescan por su id, no con toda la lista.
  useEffect(() => {
    const pending = tickets.filter(isPending).slice(0, 50).map((t) => t.ticket_id)
    if (pending.length === 0) return
    const timer = window.setInterval(() => listRfpTickets(pending)
      .then((r) => { setTickets((current) => patchTickets(current, r.tickets)); setRefreshError('') })
      .catch((e: Error) => setRefreshError(e.message)), POLL_MS)
    return () => window.clearInterval(timer)
  }, [tickets])

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input = event.currentTarget.elements.namedItem('rfp') as HTMLInputElement
    const files = Array.from(input.files ?? [])
    if (files.length === 0) { setError('Selecciona al menos un PDF.'); return }
    const invalid = files.map(validateRfpFile).find(Boolean)
    if (invalid) { setError(invalid); return }
    setError(''); setUploading(true)
    try {
      const created = []
      for (const file of files) created.push(await uploadRfp(file))
      setMessage(`${created.length} RFP subida(s): un ticket por documento, ya en análisis.`)
      input.value = ''
      setSelected(created[created.length - 1].ticket_id)
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <main className="mx-auto w-full max-w-6xl space-y-6 px-6 py-8">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Recepción de RFP</h1>
        <p className="text-sm text-slate-600">Sube las RFP en PDF: cada documento crea un ticket que se analiza en segundo plano (analizando → análisis completo o descartado) y, si es una RFP, genera y evalúa un borrador por departamento (generando borrador → en evaluación); después cada responsable aprueba su sección (esperando aprobación) y el documento final se genera solo (terminado).</p>
      </header>

      <form onSubmit={handleUpload} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <label className="flex flex-col text-sm font-medium text-slate-800">
          RFP en PDF
          <input name="rfp" type="file" accept="application/pdf,.pdf" multiple className="mt-1 text-sm" />
        </label>
        <button type="submit" disabled={uploading}
          className="inline-flex min-h-11 items-center gap-2 rounded-md bg-emerald-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">
          <FileUp size={16} aria-hidden="true" /> {uploading ? 'Subiendo…' : 'Subir y crear ticket'}
        </button>
        <button type="button" onClick={refresh} className="inline-flex min-h-11 items-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-800">
          <RefreshCw size={16} aria-hidden="true" /> Actualizar
        </button>
        {message && <p role="status" className="w-full text-sm text-emerald-800">{message}</p>}
        {(error || refreshError) && <p role="alert" data-testid="rfp-error" className="w-full text-sm text-rose-800">{error || refreshError}</p>}
      </form>

      <section aria-label="Tickets RFP" className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-slate-600">
            <tr><th className="px-4 py-2">Documento</th><th className="px-4 py-2">Cliente</th><th className="px-4 py-2">Departamentos</th><th className="px-4 py-2">Estado</th><th className="px-4 py-2">Recibido</th></tr>
          </thead>
          <tbody>
            {tickets.length === 0 && <tr><td colSpan={5} className="px-4 py-6 text-center text-slate-500">Todavía no hay RFP.</td></tr>}
            {tickets.map((ticket) => (
              <tr key={ticket.ticket_id} data-testid="rfp-row" data-ticket={ticket.ticket_id} onClick={() => setSelected(ticket.ticket_id)}
                className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === ticket.ticket_id ? 'bg-emerald-50' : ''}`}>
                <td className="px-4 py-2"><span className="font-medium">{ticket.original_filename || ticket.rfp_id}</span>
                  {unread.has(ticket.ticket_id) && <span data-testid="rfp-row-new" className="ml-2 rounded bg-violet-700 px-1.5 py-0.5 text-xs font-semibold text-white">Nueva</span>}<br /><span className="text-xs text-slate-500">{ticket.rfp_id}</span></td>
                <td className="px-4 py-2">{ticket.client_name ?? '—'} {ticket.client_country && <span className="text-slate-500">({ticket.client_country})</span>}</td>
                <td className="px-4 py-2">{ticket.departments_needed.join(', ') || '—'}</td>
                <td className="px-4 py-2"><StatusBadge ticket={ticket} /></td>
                <td className="px-4 py-2 text-slate-600">{new Date(ticket.created_at).toLocaleString('es-ES')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {selected && <TicketDetail key={selected} ticketId={selected} onReprocess={refresh} refreshKey={tickets.find((t) => t.ticket_id === selected)?.updated_at} />}
    </main>
  )
}
