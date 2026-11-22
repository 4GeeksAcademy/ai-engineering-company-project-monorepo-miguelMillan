import { apiFetch, apiRequest } from './auth'

export type RfpStatus =
  | 'analizando' | 'descartado' | 'analisis_completo'
  | 'generando_borrador' | 'en_evaluación' | 'needs_human_review' | 'esperando_aprobación' | string

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
  response_stage: string | null
  response_attempts: number
  client_name: string | null
  client_country: string | null
  departments_needed: string[]
}

export type KeyAspect = { aspecto: string; detalle: string; cita: string | null; origen: string }

export type ComplianceViolation = { rule_id: string; message: string; evidence: string; fix: string }

export type EvaluationResult = {
  section_id: string
  department_id: string
  iteration: number
  readability: { pass: boolean; score: number; details: Record<string, unknown> }
  relevance: { pass: boolean; missing_aspects: string[]; unsupported_figures?: string[]; off_scope?: { service: string; sentence: string }[] }
  compliance: { pass: boolean; rule_ids: string[]; violations: ComplianceViolation[] }
  overall_pass: boolean
  feedback_for_generator: string
}

export type EvaluationHistoryItem = {
  iteration: number
  overall_pass: boolean
  readability_pass: boolean
  relevance_pass: boolean
  compliance_pass: boolean
  rule_ids: string[]
  feedback_for_generator: string
}

export type RfpSection = {
  department_id: string
  key_aspects: KeyAspect[]
  open_questions: string[]
  approver: string | null
  approval_status: string
  section_status: string
  iterations: number
  needs_human_review: boolean
  draft_content: string | null
  evaluation_result: EvaluationResult | null
  evaluation_history: EvaluationHistoryItem[]
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
  response_handoff: null | {
    contract: string
    needs_human_review: boolean
    needs_human_review_sections: string[]
    max_iterations: number
    avg_iterations_per_section: number
    currency: string | null
  }
}

export const STATUS_LABELS: Record<string, string> = {
  analizando: 'Analizando',
  descartado: 'Descartado',
  analisis_completo: 'Análisis completo',
  generando_borrador: 'Generando borrador',
  'en_evaluación': 'En evaluación',
  needs_human_review: 'Revisión humana',
  'esperando_aprobación': 'Esperando aprobación',
}

export const SECTION_STATUS_LABELS: Record<string, string> = {
  pendiente: 'Pendiente',
  generando: 'Generando',
  en_evaluacion: 'En evaluación',
  evaluacion_superada: 'Evaluación superada',
  needs_human_review: 'Borrador provisional · revisión humana',
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

type PendingFields = Pick<RfpTicketSummary, 'status' | 'pipeline_stage'> & Partial<Pick<RfpTicketSummary, 'response_stage'>>

/** Mientras haya trabajo en el worker (Parte 1 o Parte 2) la UI sigue refrescando; un fallo detiene el sondeo. */
export function isPending(ticket: PendingFields): boolean {
  if (ticket.status === 'analizando') return ticket.pipeline_stage !== 'fallido'
  return ticket.response_stage === 'generando' || ticket.response_stage === 'evaluando'
}

export function generationFailed(ticket: PendingFields): boolean {
  return ticket.response_stage === 'fallido'
}

/** Se puede (re)generar desde el análisis completo, tras un fallo o tras una revisión humana. */
export function canGenerate(ticket: PendingFields): boolean {
  if (isPending(ticket)) return false
  return ['analisis_completo', 'needs_human_review', 'esperando_aprobación'].includes(ticket.status) || generationFailed(ticket)
}

export type DraftBlock =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'table'; header: string[]; rows: string[][] }

const cells = (line: string) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim())

/** Markdown mínimo de un borrador de sección (encabezados, párrafos, listas y tablas), sin HTML. */
export function parseDraft(markdown: string): DraftBlock[] {
  const blocks: DraftBlock[] = []
  const lines = markdown.split('\n')
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) { blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2] }); continue }
    if (line.startsWith('|')) {
      const table: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) table.push(lines[i++].trim())
      i--
      const body = table.slice(1).filter((row) => !/^\|[\s|:-]+\|$/.test(row))
      blocks.push({ kind: 'table', header: cells(table[0]), rows: body.map(cells) })
      continue
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) items.push(lines[i++].trim().replace(/^[-*]\s+/, ''))
      i--
      blocks.push({ kind: 'list', items })
      continue
    }
    blocks.push({ kind: 'paragraph', text: line })
  }
  return blocks
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

export function generateRfpResponse(ticketId: string): Promise<unknown> {
  return apiRequest(`/rfp-intake/tickets/${ticketId}/generate`, { method: 'POST' })
}
