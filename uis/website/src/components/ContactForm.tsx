import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'

type FormData = {
  fullName: string
  email: string
  company: string
  country: string
  message: string
}

type FormErrors = Partial<Record<keyof FormData, string>>

const initialFormData: FormData = {
  fullName: '',
  email: '',
  company: '',
  country: '',
  message: '',
}

function validate(formData: FormData): FormErrors {
  const errors: FormErrors = {}

  if (!formData.fullName.trim()) {
    errors.fullName = 'El nombre completo es obligatorio.'
  }

  if (!formData.email.trim()) {
    errors.email = 'El email de contacto es obligatorio.'
  } else if (!/^\S+@\S+\.\S+$/.test(formData.email)) {
    errors.email = 'El email no tiene un formato valido.'
  }

  if (!formData.company.trim()) {
    errors.company = 'El nombre de la empresa es obligatorio.'
  }

  if (!formData.country.trim()) {
    errors.country = 'Selecciona el pais de operacion principal.'
  }

  if (!formData.message.trim()) {
    errors.message = 'Describe brevemente tu reto logistico.'
  } else if (formData.message.trim().length < 20) {
    errors.message = 'El mensaje debe tener al menos 20 caracteres.'
  }

  return errors
}

export function ContactForm() {
  const [formData, setFormData] = useState<FormData>(initialFormData)
  const [errors, setErrors] = useState<FormErrors>({})
  const [statusMessage, setStatusMessage] = useState('')

  const isValid = useMemo(() => Object.keys(validate(formData)).length === 0, [formData])

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    const nextErrors = validate(formData)
    setErrors(nextErrors)

    if (Object.keys(nextErrors).length > 0) {
      setStatusMessage('Revisa los campos marcados para poder enviar la solicitud.')
      return
    }

    setStatusMessage('Solicitud enviada. El equipo de TrackFlow te contactara en menos de 24 horas habiles.')
    setFormData(initialFormData)
    setErrors({})
  }

  function handleReset() {
    setFormData(initialFormData)
    setErrors({})
    setStatusMessage('Formulario restablecido.')
  }

  return (
    <form className="space-y-5" onSubmit={handleSubmit} onReset={handleReset} noValidate>
      <div className="grid gap-5 md:grid-cols-2">
        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-800" htmlFor="fullName">
            Nombre completo
          </label>
          <input
            id="fullName"
            type="text"
            value={formData.fullName}
            onChange={(event) => setFormData((prev) => ({ ...prev, fullName: event.target.value }))}
            className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 shadow-sm outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
            aria-invalid={Boolean(errors.fullName)}
            aria-describedby={errors.fullName ? 'fullName-error' : undefined}
            required
          />
          {errors.fullName ? (
            <p id="fullName-error" className="mt-2 text-sm text-rose-700">
              {errors.fullName}
            </p>
          ) : null}
        </div>

        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-800" htmlFor="email">
            Email corporativo
          </label>
          <input
            id="email"
            type="email"
            value={formData.email}
            onChange={(event) => setFormData((prev) => ({ ...prev, email: event.target.value }))}
            className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 shadow-sm outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
            aria-invalid={Boolean(errors.email)}
            aria-describedby={errors.email ? 'email-error' : undefined}
            required
          />
          {errors.email ? (
            <p id="email-error" className="mt-2 text-sm text-rose-700">
              {errors.email}
            </p>
          ) : null}
        </div>

        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-800" htmlFor="company">
            Empresa
          </label>
          <input
            id="company"
            type="text"
            value={formData.company}
            onChange={(event) => setFormData((prev) => ({ ...prev, company: event.target.value }))}
            className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 shadow-sm outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
            aria-invalid={Boolean(errors.company)}
            aria-describedby={errors.company ? 'company-error' : undefined}
            required
          />
          {errors.company ? (
            <p id="company-error" className="mt-2 text-sm text-rose-700">
              {errors.company}
            </p>
          ) : null}
        </div>

        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-800" htmlFor="country">
            Pais de operacion
          </label>
          <select
            id="country"
            value={formData.country}
            onChange={(event) => setFormData((prev) => ({ ...prev, country: event.target.value }))}
            className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 shadow-sm outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
            aria-invalid={Boolean(errors.country)}
            aria-describedby={errors.country ? 'country-error' : undefined}
            required
          >
            <option value="">Selecciona una opcion</option>
            <option value="United States">United States</option>
            <option value="Spain">Spain</option>
          </select>
          {errors.country ? (
            <p id="country-error" className="mt-2 text-sm text-rose-700">
              {errors.country}
            </p>
          ) : null}
        </div>
      </div>

      <div>
        <label className="mb-2 block text-sm font-semibold text-slate-800" htmlFor="message">
          Reto logistica actual
        </label>
        <textarea
          id="message"
          value={formData.message}
          onChange={(event) => setFormData((prev) => ({ ...prev, message: event.target.value }))}
          className="min-h-32 w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 shadow-sm outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
          aria-invalid={Boolean(errors.message)}
          aria-describedby={errors.message ? 'message-error' : undefined}
          required
        />
        {errors.message ? (
          <p id="message-error" className="mt-2 text-sm text-rose-700">
            {errors.message}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-3">
        <button
          type="submit"
          disabled={!isValid}
          className="rounded-xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:bg-slate-400"
        >
          Solicitar evaluacion logistica
        </button>
        <button
          type="reset"
          className="rounded-xl border border-slate-300 bg-white px-5 py-3 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          Limpiar formulario
        </button>
      </div>

      <p aria-live="polite" className="text-sm text-slate-700">
        {statusMessage}
      </p>
    </form>
  )
}
