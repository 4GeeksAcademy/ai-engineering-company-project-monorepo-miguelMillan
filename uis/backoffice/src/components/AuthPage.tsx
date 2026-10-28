import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowLeft, KeyRound, LogIn, Mail } from 'lucide-react'
import { apiRequest, AuthRequestError, authRequest, getSessionToken, saveSession } from '../utils/auth'

type AuthPath = '/login' | '/register' | '/forgot-password' | '/reset-password' | '/account/change-password'

const titles: Record<AuthPath, string> = {
  '/login': 'Iniciar sesión',
  '/register': 'Crear cuenta',
  '/forgot-password': 'Recuperar contraseña',
  '/reset-password': 'Restablecer contraseña',
  '/account/change-password': 'Cambiar contraseña',
}

const inputClass = 'mt-2 block min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:bg-slate-100'
const linkClass = 'inline-flex items-center gap-2 rounded-sm text-sm font-semibold text-emerald-800 underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800'

export function AuthPage({ path }: { path: AuthPath }) {
  const isLogin = path === '/login'
  const isRegister = path === '/register'
  const isForgot = path === '/forgot-password'
  const isReset = path === '/reset-password'
  const isChange = path === '/account/change-password'
  const token = new URLSearchParams(window.location.search).get('token') ?? ''
  const [pending, setPending] = useState(false)
  const [complete, setComplete] = useState(false)
  const [checking, setChecking] = useState(isChange)
  const [sessionReady, setSessionReady] = useState(!isChange)
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    if (isLogin && params.get('password_reset') === 'success') return 'Contraseña restablecida. Ya puedes iniciar sesión con tu nueva contraseña.'
    if (isLogin && params.get('session') === 'expired') return 'Tu sesión ha caducado. Inicia sesión de nuevo.'
    return ''
  })
  const submitting = useRef(false)

  useEffect(() => {
    if (!isChange) return
    const session = getSessionToken()
    if (!session) {
      window.location.replace('/login?session=expired')
      return
    }
    const controller = new AbortController()
    authRequest('/me', { headers: { Authorization: `Bearer ${session}` }, signal: controller.signal })
      .then(() => { if (!controller.signal.aborted) setSessionReady(true) })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(cause instanceof AuthRequestError ? cause.message : 'No se pudo verificar la sesión. Comprueba la conexión e inténtalo de nuevo.')
      })
      .finally(() => { if (!controller.signal.aborted) setChecking(false) })
    return () => controller.abort()
  }, [isChange])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current || complete || checking) return
    const data = new FormData(event.currentTarget)
    const newPassword = String(data.get('new_password') ?? '')
    setError('')
    setNotice('')
    setFieldErrors({})
    if (!isLogin && !isForgot) {
      if (newPassword !== data.get('confirm_password')) {
        setError('La nueva contraseña y su confirmación no coinciden.')
        return
      }
      if (newPassword.length < 8 || new TextEncoder().encode(newPassword).length > 72) {
        setError('La nueva contraseña debe tener al menos 8 caracteres y no superar 72 bytes UTF-8.')
        return
      }
    }
    submitting.current = true
    setPending(true)
    try {
      if (isRegister) {
        const values = Object.fromEntries(new FormData(event.currentTarget).entries())
        const registration = Object.fromEntries(
          ['name', 'phone', 'address'].reduce<[string, FormDataEntryValue][]>((fields, field) => {
            const value = values[field]
            if (typeof value === 'string' && value.trim()) fields.push([field, value.trim()])
            return fields
          }, []),
        )
        await apiRequest('/users', {
          method: 'POST',
          body: JSON.stringify({ email: values.email, password: values.password, ...registration }),
        })
        const result = await authRequest<{ access_token: string }>('/login', {
          method: 'POST', body: JSON.stringify({ email: values.email, password: values.password }),
        })
        saveSession(result.access_token)
        window.location.assign('/')
      } else if (isLogin) {
        const result = await authRequest<{ access_token: string }>('/login', {
          method: 'POST', body: JSON.stringify({ email: data.get('email'), password: data.get('password') }),
        })
        saveSession(result.access_token)
        window.location.assign('/')
      } else if (isForgot) {
        await authRequest('/forgot-password', { method: 'POST', body: JSON.stringify({ email: data.get('email') }) })
        setNotice('Si esa dirección está registrada, recibirás un enlace en breve.')
        setComplete(true)
      } else if (isReset) {
        await authRequest('/reset-password', { method: 'POST', body: JSON.stringify({ token, new_password: newPassword }) })
        window.location.replace('/login?password_reset=success')
      } else {
        await authRequest('/change-password', {
          method: 'POST',
          headers: { Authorization: `Bearer ${getSessionToken() ?? ''}` },
          body: JSON.stringify({ current_password: data.get('current_password'), new_password: newPassword }),
        })
        setNotice('Contraseña actualizada correctamente.')
        setComplete(true)
      }
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : 'No se pudo conectar con el servidor. Inténtalo de nuevo.')
      if (cause instanceof AuthRequestError) setFieldErrors(cause.fields)
    } finally {
      submitting.current = false
      setPending(false)
    }
  }

  const missingToken = isReset && !token
  const Icon = isLogin ? LogIn : isForgot ? Mail : KeyRound

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-6 py-5">
        <a href="/" className="font-display text-xl font-bold focus-visible:outline-2 focus-visible:outline-emerald-800">TrackFlow Backoffice</a>
      </header>
      <main className="mx-auto w-full max-w-md px-6 py-12 sm:py-16">
        <h1 className="font-display text-2xl font-bold">{titles[path]}</h1>
        {checking && !error && <p role="status" className="mt-4 text-sm">Verificando sesión...</p>}
        {notice && <p role="status" className="mt-5 border-l-4 border-emerald-700 bg-emerald-50 p-4 text-sm text-emerald-950">{notice}</p>}
        {(error || missingToken) && <p id="auth-error" role="alert" className="mt-5 border-l-4 border-red-700 bg-red-50 p-4 text-sm text-red-900">{missingToken ? 'El enlace no contiene un token válido. Solicita un nuevo enlace de recuperación.' : error}</p>}
        {isChange && error && !sessionReady && <a href="/login" className={`${linkClass} mt-3`}>Inicia sesión para continuar</a>}
        <form onSubmit={submit} className="mt-6" aria-busy={pending} aria-describedby={error || missingToken ? 'auth-error' : undefined}>
          <fieldset disabled={pending || complete || checking || missingToken || !sessionReady} className="min-w-0 space-y-5 disabled:opacity-70">
            <legend className="sr-only">{titles[path]}</legend>
            {(isLogin || isRegister || isForgot) && <div>
              <label htmlFor="auth-email" className="text-sm font-semibold">Email</label>
              <input id="auth-email" name="email" type="email" autoComplete="email" required aria-invalid={Boolean(fieldErrors.email)} aria-describedby={fieldErrors.email ? 'auth-email-error' : undefined} className={inputClass} />
              {fieldErrors.email && <p id="auth-email-error" className="mt-1 text-sm text-red-800">{fieldErrors.email}</p>}
            </div>}
            {(isLogin || isRegister) && <div>
              <label htmlFor="auth-password" className="text-sm font-semibold">Contraseña</label>
              <input id="auth-password" name="password" type="password" autoComplete={isRegister ? 'new-password' : 'current-password'} minLength={isRegister ? 8 : undefined} required aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password ? 'auth-password-error' : undefined} className={inputClass} />
              {fieldErrors.password && <p id="auth-password-error" className="mt-1 text-sm text-red-800">{fieldErrors.password}</p>}
            </div>}
            {isRegister && <>
              <div><label htmlFor="register-name" className="text-sm font-semibold">Nombre <span className="font-normal text-slate-500">(opcional)</span></label><input id="register-name" name="name" autoComplete="name" aria-invalid={Boolean(fieldErrors.name)} aria-describedby={fieldErrors.name ? 'register-name-error' : undefined} className={inputClass} />{fieldErrors.name && <p id="register-name-error" className="mt-1 text-sm text-red-800">{fieldErrors.name}</p>}</div>
              <div><label htmlFor="register-phone" className="text-sm font-semibold">Teléfono <span className="font-normal text-slate-500">(opcional)</span></label><input id="register-phone" name="phone" type="tel" autoComplete="tel" className={inputClass} /></div>
              <div><label htmlFor="register-address" className="text-sm font-semibold">Dirección <span className="font-normal text-slate-500">(opcional)</span></label><input id="register-address" name="address" autoComplete="street-address" className={inputClass} /></div>
            </>}
            {isChange && <div>
              <label htmlFor="current-password" className="text-sm font-semibold">Contraseña actual</label>
              <input id="current-password" name="current_password" type="password" autoComplete="current-password" required className={inputClass} />
            </div>}
            {(isChange || isReset) && <>
              <div>
                <label htmlFor="new-password" className="text-sm font-semibold">Nueva contraseña</label>
                <input id="new-password" name="new_password" type="password" autoComplete="new-password" minLength={8} maxLength={72} required className={inputClass} />
              </div>
              <div>
                <label htmlFor="confirm-password" className="text-sm font-semibold">Confirmar nueva contraseña</label>
                <input id="confirm-password" name="confirm_password" type="password" autoComplete="new-password" minLength={8} maxLength={72} required className={inputClass} />
              </div>
            </>}
            <button type="submit" className="flex min-h-11 w-full items-center justify-center gap-2 rounded-md bg-emerald-800 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:cursor-not-allowed">
              <Icon size={18} aria-hidden="true" />{pending ? 'Enviando...' : isForgot ? 'Enviar enlace' : titles[path]}
            </button>
          </fieldset>
        </form>
        <nav aria-label="Acceso y recuperación" className="mt-6 flex flex-col items-start gap-4">
          {isLogin && <a href="/forgot-password" className={linkClass}>¿Olvidaste tu contraseña?</a>}
          {isLogin && <a href="/register" className={linkClass}>Crear una cuenta</a>}
          {isReset && (error || missingToken) && <a href="/forgot-password" className={linkClass}>Solicitar un nuevo enlace</a>}
          {!isLogin && <a href={isChange ? '/' : '/login'} className={linkClass}><ArrowLeft size={16} aria-hidden="true" />{isChange ? 'Volver al backoffice' : 'Volver a iniciar sesión'}</a>}
        </nav>
      </main>
    </div>
  )
}