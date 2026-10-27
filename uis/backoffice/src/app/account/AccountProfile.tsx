import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Save } from 'lucide-react'
import { apiRequest, authRequest } from '../../utils/auth'

interface ProfileValues {
  name: string
  phone: string
  address: string
}

interface ProfileResponse {
  name: string | null
  phone: string | null
  address: string | null
}

interface AuthProfileResponse {
  email: string
  profile: ProfileResponse | null
}

const inputClass = 'mt-2 block min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:bg-slate-100'

export function AccountProfile() {
  const [email, setEmail] = useState('')
  const [values, setValues] = useState<ProfileValues>({ name: '', phone: '', address: '' })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    authRequest<AuthProfileResponse>('/me', { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return
        setEmail(result.email)
        setValues({
          name: result.profile?.name ?? '',
          phone: result.profile?.phone ?? '',
          address: result.profile?.address ?? '',
        })
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'No se pudo cargar el perfil.')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [])

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const payload = Object.fromEntries(Object.entries(values).map(([field, value]) => [field, value.trim() || null]))
      const profile = await apiRequest<ProfileResponse>('/profiles/me', {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      setValues({ name: profile.name ?? '', phone: profile.phone ?? '', address: profile.address ?? '' })
      setNotice('Los datos del perfil se guardaron correctamente.')
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : 'No se pudo guardar el perfil.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="mx-auto w-full max-w-2xl space-y-6" aria-labelledby="profile-title">
      <header>
        <p className="text-sm font-semibold text-emerald-800">Cuenta</p>
        <h2 id="profile-title" className="mt-1 font-display text-2xl font-bold text-slate-950">Perfil personal</h2>
      </header>
      {loading ? <p role="status">Cargando perfil...</p> : <>
        <dl className="border-y border-slate-200 py-4">
          <dt className="text-sm font-semibold text-slate-600">Email</dt>
          <dd className="mt-1 text-slate-950">{email}</dd>
        </dl>
        <form onSubmit={saveProfile} className="space-y-5" aria-busy={saving}>
          <fieldset disabled={saving} className="space-y-5 disabled:opacity-70">
            <legend className="sr-only">Datos de contacto</legend>
            <div>
              <label htmlFor="profile-name" className="text-sm font-semibold">Nombre</label>
              <input id="profile-name" autoComplete="name" value={values.name} onChange={(event) => setValues({ ...values, name: event.target.value })} className={inputClass} />
            </div>
            <div>
              <label htmlFor="profile-phone" className="text-sm font-semibold">Teléfono</label>
              <input id="profile-phone" type="tel" autoComplete="tel" value={values.phone} onChange={(event) => setValues({ ...values, phone: event.target.value })} className={inputClass} />
            </div>
            <div>
              <label htmlFor="profile-address" className="text-sm font-semibold">Dirección</label>
              <input id="profile-address" autoComplete="street-address" value={values.address} onChange={(event) => setValues({ ...values, address: event.target.value })} className={inputClass} />
            </div>
            {error && <p role="alert" className="border-l-4 border-red-700 bg-red-50 p-3 text-sm text-red-900">{error}</p>}
            {notice && <p role="status" className="border-l-4 border-emerald-700 bg-emerald-50 p-3 text-sm text-emerald-950">{notice}</p>}
            <button type="submit" className="inline-flex min-h-11 items-center gap-2 rounded-md bg-emerald-800 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:cursor-not-allowed">
              <Save size={18} aria-hidden="true" />{saving ? 'Guardando...' : 'Guardar cambios'}
            </button>
          </fieldset>
        </form>
      </>}
    </section>
  )
}