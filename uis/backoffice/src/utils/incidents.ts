import { apiFetch, AuthRequestError } from './auth'
import { IncidentApiError } from './incidentErrors'
export { IncidentApiError, incidentError } from './incidentErrors'

export const statusLabels = { open: 'Abierta', in_progress: 'En curso', resolved: 'Resuelta', discarded: 'Descartada' }
export const originLabels = { customer: 'Cliente', branch: 'Sede', internal: 'Interno' }
export const branchLabels = {
  central: 'Central',
  la_warehouse: 'Los Ángeles — Almacén',
  la_office: 'Los Ángeles — Oficina',
  zaragoza_warehouse: 'Zaragoza — Almacén',
  zaragoza_office: 'Zaragoza — Oficina',
}
export const categoryLabels = {
  lost_parcel: 'Paquete extraviado', delivery_failure: 'Fallo de entrega',
  inventory_discrepancy: 'Discrepancia de inventario', carrier_issue: 'Problema de transportista',
  returns_issue: 'Problema de devolución', warehouse_incident: 'Incidente en almacén',
  system_failure: 'Fallo de sistema', client_complaint: 'Queja de cliente', other: 'Otra incidencia',
}
export type IncidentStatus = keyof typeof statusLabels
export type IncidentOrigin = keyof typeof originLabels
export type IncidentBranch = keyof typeof branchLabels
export type IncidentCategory = keyof typeof categoryLabels
export interface IncidentInput {
  title: string
  description: string
  category: IncidentCategory
  status: IncidentStatus
  origin: IncidentOrigin
  branch: IncidentBranch
}
export interface Incident extends IncidentInput { id: string; created_at: string; updated_at: string }
export interface IncidentSummary {
  total: number
  by_status: Record<IncidentStatus, number>
  by_category: Record<IncidentCategory, number>
  by_origin: Record<IncidentOrigin, number>
  by_branch: Record<IncidentBranch, number>
}
export const transitions: Record<IncidentStatus, IncidentStatus[]> = {
  open: ['in_progress', 'discarded'], in_progress: ['resolved', 'discarded'], resolved: [], discarded: [],
}
export const fieldMessages: Record<string, string> = {
  title: 'Introduce un título que no esté vacío.', description: 'Introduce una descripción que no esté vacía.',
  category: 'Selecciona una categoría de la lista.', status: 'Selecciona un estado permitido.',
  origin: 'Selecciona un origen de la lista.', branch: 'Selecciona una sede de la lista.',
}
export async function incidentRequest<Result>(path = '', options: RequestInit = {}): Promise<Result> {
  let response: Response
  try {
    response = await apiFetch(`/api/incidents${path}`, options)
  } catch (cause: unknown) {
    if (cause instanceof AuthRequestError) throw new IncidentApiError(cause.message)
    throw new IncidentApiError('No se pudo conectar con el servidor. Comprueba la conexión y vuelve a intentarlo.')
  }
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 400) {
      const fields: Record<string, string> = {}
      const items = Array.isArray(payload?.errors) ? payload.errors : [payload?.detail]
      for (const item of items) {
        if (typeof item?.field === 'string' && fieldMessages[item.field]) fields[item.field] = fieldMessages[item.field]
      }
      throw new IncidentApiError(options.method === 'PATCH' ? 'No se pudo cambiar el estado. Actualiza el listado antes de intentarlo de nuevo.' : 'Revisa los campos señalados e inténtalo de nuevo.', fields)
    }
    throw new IncidentApiError(response.status === 404 ? 'La incidencia ya no está disponible. Actualiza el listado.' : 'El servicio no está disponible en este momento. Inténtalo de nuevo en unos instantes.')
  }
  return payload as Result
}
export const incidentControl = 'block min-h-11 w-full min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:opacity-60'
export const incidentButton = 'inline-flex min-h-11 items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:cursor-not-allowed disabled:opacity-60'