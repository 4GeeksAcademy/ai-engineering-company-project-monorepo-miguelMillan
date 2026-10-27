import { apiFetch } from './auth'

export type Warehouse = 'LA' | 'ZGZ'
export type StockExitType = 'dispatch' | 'loss'

export interface SKU {
  id: number
  name: string
  sku: string
  client_name: string
  category: 'fashion' | 'electronics' | 'cosmetics'
  warehouse: Warehouse
  current_stock: number
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
  sku_id: number
  quantity: number
  warehouse: Warehouse
  created_at: string
  user_uuid: string
  type?: string
  order_type?: string
  movement_type?: string
  kind?: string
  sku?: string | Pick<SKU, 'name'>
  product?: Pick<SKU, 'name'>
  sku_name?: string
  name?: string
  reference?: string
  exit_type?: StockExitType
  tracking_number?: string | null
}

function errorMessage(payload: unknown, status: number): string {
  if (typeof payload === 'string' && payload.trim()) return payload.trim()
  if (payload && typeof payload === 'object') {
    const body = payload as Record<string, unknown>
    const detail = body.detail
    if (typeof detail === 'string' && detail.trim()) return detail
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
  return `La API de inventario respondió con el estado ${status}.`
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
  if (!response.ok) throw new Error(errorMessage(payload, response.status))
  return payload as Result
}