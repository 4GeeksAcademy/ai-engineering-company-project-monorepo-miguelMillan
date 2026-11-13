import { apiFetch } from './auth'
import { track } from '../services/telemetry'

export type Warehouse = 'LA' | 'ZGZ'
export type StockExitType = 'dispatch' | 'loss'

export interface SKU {
  id: number
  name: string
  sku: string
  client_name: string
  client_id: string
  category: 'fashion' | 'electronics' | 'cosmetics'
  warehouse: Warehouse
  current_stock: number
  minimum_stock: number | null
}

export interface InventoryTelemetrySignal {
  event_type: string
  properties: Record<string, unknown>
}

export interface StockEntryInput {
  sku_id: number
  quantity: number
  reference: string
  warehouse: Warehouse
}

export interface StockExitInput {
  sku_id: number
  quantity: number
  exit_type: StockExitType
  tracking_number: string | null
  warehouse: Warehouse
}

export interface InventoryOrder {
  id: number
  order_type: 'inbound' | 'outbound'
  sku_id: number
  product_name: string
  quantity: number
  warehouse: Warehouse
  created_at: string
  user_uuid: string
  reference: string | null
  exit_type: StockExitType | null
  tracking_number: string | null
}

export interface InventoryAudit {
  id: number
  sku_id: number
  warehouse: Warehouse
  system_stock: number
  physical_stock: number
  discrepancy_quantity: number
  discrepancy_direction: 'physical_lower' | 'physical_higher' | null
  created_at: string
  telemetry_event: InventoryTelemetrySignal | null
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function errorMessage(payload: unknown, status: number): string {
  if (status >= 500) return 'El servicio de inventario no está disponible temporalmente. Inténtalo de nuevo más tarde.'
  if (typeof payload === 'string' && payload.trim()) return payload.trim()
  if (payload && typeof payload === 'object') {
    const body = payload as Record<string, unknown>
    const detail = body.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const message = (detail as Record<string, unknown>).message
      if (typeof message === 'string' && message.trim()) return message
    }
    if (Array.isArray(detail)) {
      return detail.map((issue) => {
        if (!issue || typeof issue !== 'object') return String(issue)
        const item = issue as { loc?: unknown[]; msg?: unknown }
        const field = item.loc?.at(-1)
        return `${typeof field === 'string' ? `${field}: ` : ''}${typeof item.msg === 'string' ? item.msg : 'Solicitud no válida.'}`
      }).join(' ')
    }
    for (const key of ['error', 'message']) {
      const message = body[key]
      if (typeof message === 'string' && message.trim()) return message
    }
  }
  return 'No se pudo completar la operación de inventario. Inténtalo de nuevo.'
}

export async function inventoryRequest<Result>(path: string, options: RequestInit = {}): Promise<Result> {
  const response = await apiFetch(`/inventory${path}`, options)
  const body = await response.text().catch(() => '')
  let payload: unknown = null
  if (body) {
    try {
      payload = JSON.parse(body)
    } catch {
      if (!response.headers.get('content-type')?.toLowerCase().includes('text/html')) payload = body
    }
  }
  if (!response.ok) {
    const detail = objectRecord(objectRecord(payload)?.detail)
    const signal = objectRecord(detail?.telemetry_event)
    const properties = objectRecord(signal?.properties)
    if (signal?.event_type === 'direct_stock_edit_rejected' && properties) {
      track('direct_stock_edit_rejected', properties)
    }
    throw new Error(errorMessage(payload, response.status))
  }
  return payload as Result
}