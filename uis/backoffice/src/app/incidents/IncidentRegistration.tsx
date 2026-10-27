import { useRef, useState, type FormEvent } from 'react'
import { Check, LoaderCircle, Plus, RotateCcw } from 'lucide-react'
import { branchLabels, categoryLabels, fieldMessages, IncidentApiError, incidentButton, incidentControl, incidentError, incidentRequest, originLabels, statusLabels } from '../../utils/incidents'
import type { Incident, IncidentInput, IncidentOrigin } from '../../utils/incidents'

export function IncidentRegistration() {
  const [origin, setOrigin] = useState<IncidentOrigin | ''>('')
  const [pending, setPending] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const submitting = useRef(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return
    const form = event.currentTarget
    const data = new FormData(form)
    const values = Object.fromEntries(data.entries())
    const invalid: Record<string, string> = {}
    for (const field of ['title', 'description', 'category', 'status', 'origin', 'branch']) {
      if (!String(values[field] ?? '').trim()) invalid[field] = fieldMessages[field]
    }
    setErrors(invalid)
    setError('')
    setNotice('')
    if (Object.keys(invalid).length) {
      form.querySelector<HTMLElement>(`[name="${Object.keys(invalid)[0]}"]`)?.focus()
      return
    }
    const payload: IncidentInput = {
      title: String(values.title).trim(), description: String(values.description),
      category: values.category as IncidentInput['category'], status: values.status as IncidentInput['status'],
      origin: values.origin as IncidentInput['origin'], branch: values.branch as IncidentInput['branch'],
    }
    submitting.current = true
    setPending(true)
    try {
      await incidentRequest<Incident>('', { method: 'POST', body: JSON.stringify(payload) })
      form.reset()
      setOrigin('')
      setErrors({})
      setNotice('Incidencia registrada correctamente.')
    } catch (cause) {
      setError(incidentError(cause))
      if (cause instanceof IncidentApiError) setErrors(cause.fields)
    } finally {
      submitting.current = false
      setPending(false)
    }
  }

  return (
    <section aria-labelledby="incident-registration-title" className="space-y-6">
      <h2 id="incident-registration-title" className="font-display text-2xl font-bold">Registrar incidencia</h2>
      {notice && <p role="status" className="flex items-center gap-2 border-l-4 border-emerald-700 bg-emerald-50 p-4 text-emerald-950"><Check size={18} aria-hidden="true" />{notice}</p>}
      <form noValidate onSubmit={submit} onReset={() => { setOrigin(''); setErrors({}); setError(''); setNotice('') }} aria-busy={pending} className="space-y-6">
        <fieldset disabled={pending} className="grid min-w-0 gap-5 md:grid-cols-2">
          <legend className="sr-only">Datos de la incidencia</legend>
          <div className="md:col-span-2"><label htmlFor="incident-title" className="mb-2 block text-sm font-semibold">Título *</label><input id="incident-title" name="title" required aria-invalid={!!errors.title} aria-describedby={errors.title ? 'incident-title-error' : undefined} className={incidentControl} />{errors.title && <p id="incident-title-error" role="alert" className="mt-2 text-sm text-red-800">{errors.title}</p>}</div>
          <div className="md:col-span-2"><label htmlFor="incident-description" className="mb-2 block text-sm font-semibold">Descripción *</label><textarea id="incident-description" name="description" required rows={4} aria-invalid={!!errors.description} aria-describedby={errors.description ? 'incident-description-error' : undefined} className={incidentControl} />{errors.description && <p id="incident-description-error" role="alert" className="mt-2 text-sm text-red-800">{errors.description}</p>}</div>
          {([
            ['category', 'Categoría', categoryLabels], ['status', 'Estado', statusLabels],
            ['origin', 'Origen', originLabels], ['branch', 'Sede', branchLabels],
          ] as const).map(([field, label, labels]) => <div key={field} className={field === 'branch' && origin === 'branch' ? 'border-l-4 border-amber-600 bg-amber-50 p-4' : ''}>
            <label htmlFor={`incident-${field}`} className="mb-2 block text-sm font-semibold">{label} *{field === 'branch' && origin === 'branch' ? ' · Reporte desde sede' : ''}</label>
            <select id={`incident-${field}`} name={field} required defaultValue={field === 'status' ? 'open' : ''} onChange={field === 'origin' ? (event) => setOrigin(event.target.value as IncidentOrigin | '') : undefined} aria-invalid={!!errors[field]} aria-describedby={errors[field] ? `incident-${field}-error` : undefined} className={incidentControl}>
              {field !== 'status' && <option value="">Seleccionar</option>}{Object.entries(labels).map(([value, text]) => <option value={value} key={value}>{text}</option>)}
            </select>
            {errors[field] && <p id={`incident-${field}-error`} role="alert" className="mt-2 text-sm text-red-800">{errors[field]}</p>}
          </div>)}
        </fieldset>
        <fieldset className="grid min-w-0 gap-4 border-t border-slate-200 pt-4 sm:grid-cols-3">
          <legend className="sr-only">Datos generados por el sistema</legend>
          {['ID', 'Fecha de creación (UTC)', 'Última modificación (UTC)'].map((label, index) => <div key={label}><label htmlFor={`incident-metadata-${index}`} className="mb-2 block text-sm font-semibold">{label}</label><input id={`incident-metadata-${index}`} readOnly value="Automático" className={`${incidentControl} bg-slate-50 text-slate-600`} /></div>)}
        </fieldset>
        {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
        <div className="flex flex-wrap gap-3">
          <button type="submit" disabled={pending} className={`${incidentButton} bg-emerald-800 text-white hover:bg-emerald-900`}>{pending ? <LoaderCircle size={18} className="animate-spin" aria-hidden="true" /> : <Plus size={18} aria-hidden="true" />}{pending ? 'Registrando...' : 'Registrar incidencia'}</button>
          <button type="reset" disabled={pending} className={`${incidentButton} border border-slate-300 bg-white`}><RotateCcw size={18} aria-hidden="true" />Limpiar</button>
        </div>
      </form>
    </section>
  )
}