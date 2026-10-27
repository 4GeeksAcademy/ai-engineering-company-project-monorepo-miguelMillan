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