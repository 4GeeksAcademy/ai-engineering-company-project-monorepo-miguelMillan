import { RefreshCw } from 'lucide-react'
import { track } from '../services/telemetry'

interface AsyncErrorStateProps {
  message: string
  onRetry: () => void
  retryLabel?: string
  disabled?: boolean
  className?: string
  buttonClassName?: string
  section?: 'overview' | 'inventory_products' | 'inventory_inbound' | 'inventory_outbound' | 'inventory_orders' | 'incidents' | 'incident_registration' | 'incident_analysis' | 'suppliers' | 'account' | 'login' | 'other'
}

const defaultButtonClassName = 'inline-flex min-h-11 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800 disabled:opacity-50'

export function AsyncErrorState({
  message,
  onRetry,
  retryLabel = 'Reintentar',
  disabled = false,
  className = 'space-y-2',
  buttonClassName = defaultButtonClassName,
  section = 'other',
}: AsyncErrorStateProps) {
  return (
    <div className={className}>
      <p role="alert" className="text-sm text-red-800">{message}</p>
      <button type="button" disabled={disabled} onClick={() => { track('frontend_retry_requested', { section }); onRetry() }} className={buttonClassName}>
        <RefreshCw size={16} aria-hidden="true" />{retryLabel}
      </button>
    </div>
  )
}