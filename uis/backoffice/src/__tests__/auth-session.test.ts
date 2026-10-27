import { clearSession, getSessionToken, saveSession } from '../utils/session'

describe('helpers de sesión', () => {
  beforeEach(() => localStorage.clear())

  it('devuelve null si todavía no hay sesión', () => {
    expect(getSessionToken()).toBeNull()
  })

  it('guarda y recupera el token de sesión', () => {
    saveSession('test-access-token')

    expect(getSessionToken()).toBe('test-access-token')
  })

  it('elimina el token de sesión existente', () => {
    saveSession('test-access-token')

    clearSession()

    expect(getSessionToken()).toBeNull()
  })

  it('permite limpiar una sesión vacía', () => {
    expect(() => clearSession()).not.toThrow()
  })
})