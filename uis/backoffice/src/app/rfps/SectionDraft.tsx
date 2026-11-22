import { SECTION_STATUS_LABELS, type EvaluationResult, type RfpSection } from '../../utils/rfps'
import { DraftView } from './DraftView'

function Check({ label, pass, detail }: { label: string; pass: boolean; detail: string }) {
  return (
    <div className={`rounded-md px-3 py-2 text-xs ${pass ? 'bg-emerald-50 text-emerald-950' : 'bg-rose-50 text-rose-950'}`}>
      <p className="font-semibold">{pass ? '✓' : '✗'} {label}</p>
      <p>{detail}</p>
    </div>
  )
}

function EvaluationPanel({ result }: { result: EvaluationResult }) {
  const details = result.readability.details as { metric?: string; threshold?: number; max_sentence_words?: number; long_sentences?: { words: number }[] }
  const longest = Math.max(0, ...(details.long_sentences ?? []).map((s) => s.words))
  const readabilityDetail = `${details.metric ?? 'puntuación'} ${result.readability.score} (mínimo ${details.threshold ?? '—'})`
    + (longest ? ` · oración de ${longest} palabras (máximo ${details.max_sentence_words ?? 30})` : '')
  const relevanceIssues = result.relevance.missing_aspects.length + (result.relevance.unsupported_figures?.length ?? 0)
    + (result.relevance.off_scope?.length ?? 0)
  return (
    <div className="space-y-2" data-testid="rfp-evaluation">
      <div className="grid gap-2 sm:grid-cols-3">
        <Check label="Legibilidad" pass={result.readability.pass} detail={readabilityDetail} />
        <Check label="Pertinencia" pass={result.relevance.pass} detail={result.relevance.pass ? 'Responde a lo que pide la RFP' : `${relevanceIssues} aspecto(s) por corregir`} />
        <Check label="Cumplimiento (BRIEFING §5)" pass={result.compliance.pass} detail={result.compliance.pass ? 'Cumple los 5 lineamientos' : result.compliance.rule_ids.join(', ')} />
      </div>
      {result.compliance.violations.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-rose-900">
          {result.compliance.violations.map((v, i) => <li key={i}><strong>{v.rule_id}</strong>: {v.message} «{v.evidence}»</li>)}
        </ul>
      )}
      {!result.overall_pass && result.feedback_for_generator && (
        <details className="text-xs text-slate-700">
          <summary className="cursor-pointer font-semibold">Feedback enviado al generador</summary>
          <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2">{result.feedback_for_generator}</pre>
        </details>
      )}
    </div>
  )
}

export function SectionDraft({ section, maxIterations }: { section: RfpSection; maxIterations: number | null }) {
  if (!section.draft_content && section.section_status === 'pendiente') return null
  const provisional = section.section_status === 'needs_human_review'
  const tone = provisional ? 'bg-amber-100 text-amber-950' : section.section_status === 'evaluacion_superada'
    ? 'bg-emerald-100 text-emerald-900' : 'bg-slate-100 text-slate-800'
  return (
    <div className={`mt-4 space-y-3 rounded-md border p-4 ${provisional ? 'border-amber-400 bg-amber-50/40' : 'border-slate-200'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-semibold text-slate-900">Borrador de propuesta económica</h4>
        <span data-testid="rfp-section-status" className={`rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>
          {SECTION_STATUS_LABELS[section.section_status] ?? section.section_status}
          {section.iterations > 0 && ` · iteración ${section.iterations}${maxIterations ? `/${maxIterations}` : ''}`}
        </span>
      </div>
      {provisional && (
        <p role="note" className="rounded-md bg-amber-100 px-3 py-2 text-sm font-medium text-amber-950">
          PROVISIONAL: este borrador no superó la evaluación tras {section.iterations} iteraciones. Es el último intento del
          generador; revísalo y corrige lo señalado abajo antes de enviarlo al cliente.
        </p>
      )}
      {section.draft_content && <DraftView markdown={section.draft_content} />}
      {section.evaluation_result && <EvaluationPanel result={section.evaluation_result} />}
      {section.evaluation_history.length > 1 && (
        <ol className="text-xs text-slate-600">
          {section.evaluation_history.map((item) => (
            <li key={item.iteration}>
              Iteración {item.iteration}: {item.overall_pass ? 'superada' : `no superada (${[
                !item.readability_pass && 'legibilidad', !item.relevance_pass && 'pertinencia',
                !item.compliance_pass && `cumplimiento ${item.rule_ids.join(', ')}`].filter(Boolean).join('; ')})`}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
