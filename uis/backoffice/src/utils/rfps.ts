import { apiFetch, apiRequest } from './auth'

export type RfpStatus =
  | 'analizando' | 'descartado' | 'analisis_completo'
  | 'generando_borrador' | 'en_evaluación' | 'needs_human_review' | 'esperando_aprobación' | 'terminado' | string

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
  approval_stage: string | null
  approval_thread_id: string | null
  approval_note: string | null
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
  approval_round: number
  approval_request: ApprovalRequest | null
  decision_history: DecisionRecord[]
  commitments: Record<string, number>
  approved_at: string | null
}

export type ApprovalRequest = {
  department_id: string
  department: string
  approver: string
  version: number
  round: number
  max_rounds: number
  allowed_decisions: Decision[]
  client: { name: string | null; country: string | null; currency: string | null }
  provisional: boolean
  why_this_version: string | null
  evaluation: {
    overall_pass: boolean | null
    readability: { pass: boolean | null; score: number | null }
    relevance: { pass: boolean | null; missing_aspects: string[] }
    compliance: { pass: boolean | null; rule_ids: string[]; violations: { rule_id: string; evidence: string }[] }
  }
  pending_items: { concept: string; value: string }[]
  key_figures: Record<string, string | number | null>
  open_questions: string[]
  arbitration: ArbitrationRecord[]
  commitment_fields: Record<string, string>
  validation_errors?: string[]
}

export type Decision = 'approve' | 'reject' | 'request_changes'

export type DecisionRecord = {
  version: number
  round: number
  decision: Decision
  approver: string
  decided_by: string | null
  comment: string
  commitments: Record<string, number>
  decided_at: string
}

export type ArbitrationRecord = {
  trigger: string
  arbiter: string
  affected_departments: string[]
  resolution: string
  round: number
  decided_at: string
  evidence?: unknown
}

export type TraceEvent = {
  id: number
  part: string
  node: string
  agent: string
  department_id: string | null
  status: string
  started_at: string
  finished_at: string
}

export type FinalDocument = {
  ticket_id: string
  rfp_id: string
  currency: string
  generated_at: string
  approvals: { department_id: string; approver: string; approved_at: string; version: number }[]
  arbitration: ArbitrationRecord[]
  content: string
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
  arbitration: ArbitrationRecord[]
  final_document: null | { currency: string; generated_at: string; approvals: FinalDocument['approvals']; url: string }
}

export const STATUS_LABELS: Record<string, string> = {
  analizando: 'Analizando',
  descartado: 'Descartado',
  analisis_completo: 'Análisis completo',
  generando_borrador: 'Generando borrador',
  'en_evaluación': 'En evaluación',
  needs_human_review: 'Revisión humana',
  'esperando_aprobación': 'Esperando aprobación',
  terminado: 'Terminado',
}

export const APPROVAL_STATUS_LABELS: Record<string, string> = {
  pendiente: 'Pendiente',
  'esperando_aprobación': 'Esperando tu decisión',
  procesando: 'Procesando decisión',
  en_revision: 'En revisión (generador)',
  aprobada: 'Aprobada',
  rechazada: 'Rechazada',
}

export const DECISION_LABELS: Record<Decision, string> = {
  approve: 'Aprobar',
  request_changes: 'Solicitar cambios',
  reject: 'Rechazar',
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

type PendingFields = Pick<RfpTicketSummary, 'status' | 'pipeline_stage'>
  & Partial<Pick<RfpTicketSummary, 'response_stage' | 'approval_stage'>>

/** Mientras haya trabajo en el worker (Partes 1, 2 o 3) la UI sigue refrescando; un fallo detiene el sondeo. */
export function isPending(ticket: PendingFields): boolean {
  if (ticket.status === 'analizando') return ticket.pipeline_stage !== 'fallido'
  if (ticket.response_stage === 'generando' || ticket.response_stage === 'evaluando') return true
  return ticket.approval_stage === 'iniciando' || ticket.approval_stage === 'en_curso'
}

export function generationFailed(ticket: PendingFields): boolean {
  return ticket.response_stage === 'fallido'
}

/** Se puede (re)generar desde el análisis completo o tras un fallo, siempre antes de que empiece la aprobación. */
export function canGenerate(ticket: PendingFields): boolean {
  if (isPending(ticket) || ticket.approval_stage) return false
  return ['analisis_completo', 'needs_human_review'].includes(ticket.status) || generationFailed(ticket)
}

/** La aprobación se puede (re)arrancar si falló o quedó bloqueada (hilo nuevo; nunca reinicia uno vivo). */
export function canStartApproval(ticket: PendingFields & { response_handoff?: unknown }): boolean {
  return ['fallido', 'bloqueado'].includes(ticket.approval_stage ?? '') && Boolean(ticket.response_handoff)
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

/** Con `ticketIds` pide solo esos tickets (el refresco de los que están en curso). */
export function listRfpTickets(ticketIds: string[] = []): Promise<{ tickets: RfpTicketSummary[] }> {
  const query = new URLSearchParams(ticketIds.map((id) => ['ticket_id', id]))
  return apiRequest(`/rfp-intake/tickets${ticketIds.length ? `?${query}` : ''}`)
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

export type DecisionInput = {
  decision: Decision
  approver: string
  section_version: number
  comment: string
  commitments: Record<string, number>
}

export async function decideSection(ticketId: string, departmentId: string, input: DecisionInput): Promise<unknown> {
  const response = await apiFetch(`/rfp-intake/tickets/${ticketId}/sections/${departmentId}/decision`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    const errors: string[] = Array.isArray(detail?.errors) ? detail.errors : [typeof detail === 'string' ? detail : 'No se pudo registrar la decisión.']
    throw new Error(errors.join(' '))
  }
  return payload
}

export function startApproval(ticketId: string): Promise<unknown> {
  return apiRequest(`/rfp-intake/tickets/${ticketId}/approval/start`, { method: 'POST' })
}

export function getFinalDocument(ticketId: string): Promise<FinalDocument> {
  return apiRequest(`/rfp-intake/tickets/${ticketId}/final-document`)
}

export function getTrace(ticketId: string): Promise<{ events: TraceEvent[] }> {
  return apiRequest(`/rfp-intake/tickets/${ticketId}/trace`)
}

/** Progreso de aprobación: secciones aprobadas sobre activas. */
export function approvalProgress(sections: Pick<RfpSection, 'approval_status'>[]): { approved: number; total: number } {
  return { approved: sections.filter((s) => s.approval_status === 'aprobada').length, total: sections.length }
}
