const sessionKey = 'trackflow.accessToken'

export function getSessionToken(): string | null {
  return localStorage.getItem(sessionKey)
}

export function saveSession(token: string): void {
  localStorage.setItem(sessionKey, token)
}

export function clearSession(): void {
  localStorage.removeItem(sessionKey)
}

export class AuthRequestError extends Error {
  fields: Record<string, string>

  constructor(message: string, fields: Record<string, string> = {}) {
    super(message)
    this.fields = fields
  }
}

const publicRequests = new Set(['/auth/login', '/auth/forgot-password', '/auth/reset-password'])

export async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const base = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')
  const headers = new Headers(options.headers)
  const token = getSessionToken()
  const isPublicRequest = publicRequests.has(path) || (path === '/users' && options.method?.toUpperCase() === 'POST')

  if (!isPublicRequest && !token) {
    window.location.replace('/login?session=expired')
    throw new AuthRequestError('Inicia sesión para continuar.')
  }
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (!(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(`${base}${path}`, { ...options, headers })
  if (response.status === 401 && !isPublicRequest) {
    clearSession()
    window.location.replace('/login?session=expired')
  }
  return response
}

export async function apiRequest<Result>(path: string, options: RequestInit = {}): Promise<Result> {
  const response = await apiFetch(path, options)
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    const fields: Record<string, string> = {}
    if (Array.isArray(detail)) {
      for (const issue of detail) {
        const field = issue.loc?.at(-1)
        if (typeof field === 'string') fields[field] = issue.msg ?? 'Revisa este campo.'
      }
    } else if (response.status === 409 && path === '/users') {
      fields.email = typeof detail === 'string' ? detail : 'Ya existe un usuario con ese email.'
    }
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((issue: { loc?: string[]; msg?: string }) => `${issue.loc?.at(-1) ?? 'Solicitud'}: ${issue.msg ?? 'Revisa este campo.'}`).join('. ')
      : 'No se pudo completar la solicitud. Inténtalo de nuevo.'
    throw new AuthRequestError(message, fields)
  }
  return payload as Result
}

export function authRequest<Result>(path: string, options: RequestInit = {}): Promise<Result> {
  return apiRequest(`/auth${path}`, options)
}