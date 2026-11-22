import { apiFetch, apiRequest } from './auth'

export type RfpStatus = 'analizando' | 'descartado' | 'analisis_completo' | string

export type RfpTicketSummary = {
  ticket_id: string
  rfp_id: string
  status: RfpStatus
  pipeline_stage: string
  original_filename: string
  created_at: string
  updated_at: string
  attempts: number
  last_error: string | null
  discard_reason: string | null
  client_name: string | null
  client_country: string | null
  departments_needed: string[]
}

export type KeyAspect = { aspecto: string; detalle: string; cita: string | null; origen: string }

export type RfpSection = {
  department_id: string
  key_aspects: KeyAspect[]
  open_questions: string[]
  approver: string | null
  approval_status: string
}

export type RfpTicketDetail = RfpTicketSummary & {
  metadata: null | {
    client_name: string | null
    client_country: string | null
    currency: string | null
    services_requested: string[]
    monthly_volume: number | null
    sku_count: number | null
    deadline: string | null
    deadline_text: string | null
    budget_range: string | null
    departments_needed: string[]
    readability: Record<string, number | string | null>
    details: { excluded_services?: string[]; out_of_scope_requests?: string[]; contact?: string | null; rfp_reference?: string | null }
    classification: null | { is_rfp: boolean; document_type: string; reason: string; decision_rule: string; needs_sales_review: boolean }
  }
  sections: RfpSection[]
  handoff: null | {
    contract: string
    sales_summary: string
    conflicts: { metric: string; values: Record<string, number>; resolution: string }[]
    general_open_questions: string[]
    workstreams: { department_id: string; department: string; owner: string; ask: string[] }[]
  }
}

export const STATUS_LABELS: Record<string, string> = {
  analizando: 'Analizando',
  descartado: 'Descartado',
  analisis_completo: 'Análisis completo',
}

export const DEPARTMENT_LABELS: Record<string, string> = {
  warehouse: 'Warehouse Operations · Ana Whitfield',
  lastmile: 'Last Mile and Carrier Management · Carlos Vega',
  reverse: 'Reverse Logistics · Sofía Ramos',
}

export const MAX_RFP_BYTES = 10 * 1024 * 1024

export function validateRfpFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.pdf')) return `«${file.name}» no es un PDF.`
  if (file.size > MAX_RFP_BYTES) return `«${file.name}» supera los 10 MB.`
  return null
}

export function isPending(ticket: Pick<RfpTicketSummary, 'status' | 'pipeline_stage'>): boolean {
  return ticket.status === 'analizando' && ticket.pipeline_stage !== 'fallido'
}

export async function uploadRfp(file: File): Promise<{ ticket_id: string; rfp_id: string; status: string }> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await apiFetch('/rfp-intake/tickets', { method: 'POST', body: formData })
  const payload = await response.json().catch(() => null)
  if (!response.ok) throw new Error(payload?.detail ?? 'No se pudo subir la RFP.')
  return payload
}

export function listRfpTickets(): Promise<{ tickets: RfpTicketSummary[] }> {
  return apiRequest('/rfp-intake/tickets')
}

export function getRfpTicket(ticketId: string): Promise<RfpTicketDetail> {
  return apiRequest(`/rfp-intake/tickets/${ticketId}`)
}

export function reprocessRfpTicket(ticketId: string, forceAccept = false): Promise<unknown> {
  return apiRequest(`/rfp-intake/tickets/${ticketId}/reprocess?force_accept=${forceAccept}`, { method: 'POST' })
}
