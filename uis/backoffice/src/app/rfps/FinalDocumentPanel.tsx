import { useEffect, useState } from 'react'
import { getFinalDocument, type FinalDocument } from '../../utils/rfps'
import { DraftView } from './DraftView'

/** Documento final (BRIEFING §2.3): secciones aprobadas, moneda y fecha de generación, descargable en Markdown. */
export function FinalDocumentPanel({ ticketId }: { ticketId: string }) {
  const [document, setDocument] = useState<FinalDocument | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    getFinalDocument(ticketId).then(setDocument).catch((e: Error) => setError(e.message))
  }, [ticketId])

  function download() {
    if (!document) return
    const url = URL.createObjectURL(new Blob([document.content], { type: 'text/markdown;charset=utf-8' }))
    const link = Object.assign(window.document.createElement('a'), { href: url, download: `propuesta-${document.rfp_id}.md` })
    link.click()
    URL.revokeObjectURL(url)
  }

  if (error) return <p role="alert" className="text-sm text-rose-800">{error}</p>
  if (!document) return <p role="status" className="text-sm text-slate-600">Cargando documento final…</p>
  return (
    <section data-testid="rfp-final-document" aria-label="Documento final" className="space-y-3 rounded-md border border-emerald-300 bg-emerald-50/40 p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-semibold text-emerald-950">Documento final generado</h3>
          <p className="text-xs text-emerald-900">{document.currency} · {new Date(document.generated_at).toLocaleString('es-ES')} ·{' '}
            {document.approvals.map((a) => a.approver).join(', ')}</p>
        </div>
        <button type="button" onClick={download} className="rounded-md bg-emerald-800 px-3 py-1.5 text-xs font-semibold text-white">Descargar (.md)</button>
      </header>
      <div className="max-h-[32rem] overflow-y-auto rounded bg-white p-3"><DraftView markdown={document.content} /></div>
    </section>
  )
}
