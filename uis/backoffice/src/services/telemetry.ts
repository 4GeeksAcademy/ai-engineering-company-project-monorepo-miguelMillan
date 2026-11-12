import { getSessionToken, getTelemetrySessionId } from '../utils/session'

export const TELEMETRY_SCHEMA_VERSION = '1.0.0' as const

const BATCH_SIZE = 20
const FLUSH_INTERVAL_MS = 10_000
const MAX_RETRIES = 3
const ENDPOINT = import.meta.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT as string | undefined

type TelemetryEvent = {
  eventId: string
  timestamp: string
  sessionId: string | null
  userId: string | null
  event_type: string
  schemaVersion: typeof TELEMETRY_SCHEMA_VERSION
  requestId: string
  properties: Record<string, unknown>
}

const pendingEvents: TelemetryEvent[] = []
let flushTimer: number | undefined
let flushPromise: Promise<void> | undefined
const reportedWebVitals = new Set<string>()

function createUuid(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = globalThis.crypto?.getRandomValues?.(new Uint8Array(16)) ?? Uint8Array.from({ length: 16 }, () => Math.floor(Math.random() * 256))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function decodeSubject(token: string | null): string | null {
  if (!token) return null
  try {
    const payload = token.split('.')[1]
    if (!payload) return null
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/')
    const decoded = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '='))
    const subject = JSON.parse(decoded).sub
    return typeof subject === 'string' || typeof subject === 'number' ? String(subject) : null
  } catch {
    return null
  }
}

async function hashUserId(subject: string | null): Promise<string | null> {
  if (!subject || !globalThis.crypto?.subtle) return null
  const data = new TextEncoder().encode(subject)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', data)
  const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  return `u_${hash}`
}

function scheduleFlush(): void {
  if (flushTimer !== undefined || pendingEvents.length === 0) return
  flushTimer = window.setTimeout(() => {
    flushTimer = undefined
    void flush()
  }, FLUSH_INTERVAL_MS)
}

function wait(milliseconds: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds))
}

async function sendBatch(batch: TelemetryEvent[]): Promise<void> {
  if (!ENDPOINT) return
  for (let retry = 0; retry <= MAX_RETRIES; retry += 1) {
    try {
      const response = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ events: batch }),
      })
      if (response.ok) return
    } catch {
      // Retry transient transport failures without exposing payloads to the console.
    }
    if (retry < MAX_RETRIES) await wait(2 ** retry * 1000)
  }
}

async function flush(): Promise<void> {
  if (flushPromise || pendingEvents.length === 0) return flushPromise ?? Promise.resolve()
  const batch = pendingEvents.splice(0, pendingEvents.length)
  flushPromise = sendBatch(batch).finally(() => {
    flushPromise = undefined
    if (pendingEvents.length >= BATCH_SIZE) void flush()
    else scheduleFlush()
  })
  return flushPromise
}

function flushWithBeacon(): void {
  if (!ENDPOINT || pendingEvents.length === 0 || !navigator.sendBeacon) return
  const batch = pendingEvents.splice(0, pendingEvents.length)
  const sent = navigator.sendBeacon(
    ENDPOINT,
    new Blob([JSON.stringify({ events: batch })], { type: 'application/json' }),
  )
  if (!sent) pendingEvents.unshift(...batch)
}

async function captureEvent(eventType: string, properties: Record<string, unknown>): Promise<void> {
  const event: TelemetryEvent = {
    eventId: createUuid(),
    timestamp: new Date().toISOString(),
    sessionId: getTelemetrySessionId(),
    userId: await hashUserId(decodeSubject(getSessionToken())),
    event_type: eventType,
    schemaVersion: TELEMETRY_SCHEMA_VERSION,
    requestId: createUuid(),
    properties,
  }
  pendingEvents.push(event)
  if (pendingEvents.length >= BATCH_SIZE) void flush()
  else scheduleFlush()
}

export function track(eventType: string, properties: Record<string, unknown>): void {
  void captureEvent(eventType, properties)
}

function currentSection(): string {
  const path = window.location.pathname
  if (path === '/login' || path === '/register' || path === '/forgot-password' || path === '/reset-password') return 'login'
  if (path === '/account/profile' || path === '/account/change-password') return 'account'
  if (path === '/backoffice/inventory/products') return 'inventory_products'
  if (path === '/backoffice/inventory/orders/inbound') return 'inventory_inbound'
  if (path === '/backoffice/inventory/orders/outbound') return 'inventory_outbound'
  if (path === '/backoffice/inventory/orders') return 'inventory_orders'
  return 'overview'
}

function reportWebVital(metric: string, value: number): void {
  const page = currentSection()
  const key = `${metric}:${page}`
  if (reportedWebVitals.has(key)) return
  reportedWebVitals.add(key)
  track('frontend_web_vital_recorded', { metric, value, page })
}

function observeWebVitals(): void {
  if (typeof PerformanceObserver === 'undefined') return
  const supported = PerformanceObserver.supportedEntryTypes
  if (supported.includes('largest-contentful-paint')) {
    let largestContentfulPaint = 0
    const observer = new PerformanceObserver(list => {
      const entries = list.getEntries()
      const lastEntry = entries.at(-1)
      if (lastEntry) largestContentfulPaint = lastEntry.startTime
    })
    observer.observe({ type: 'largest-contentful-paint', buffered: true })
    window.addEventListener('pagehide', () => {
      if (largestContentfulPaint > 0) reportWebVital('LCP', largestContentfulPaint)
      observer.disconnect()
    }, { once: true })
  }
  if (supported.includes('layout-shift')) {
    let cumulativeLayoutShift = 0
    const observer = new PerformanceObserver(list => {
      for (const entry of list.getEntries() as Array<PerformanceEntry & { value?: number; hadRecentInput?: boolean }>) {
        if (!entry.hadRecentInput) cumulativeLayoutShift += entry.value ?? 0
      }
    })
    observer.observe({ type: 'layout-shift', buffered: true })
    window.addEventListener('pagehide', () => {
      reportWebVital('CLS', cumulativeLayoutShift)
      observer.disconnect()
    }, { once: true })
  }
  if (supported.includes('event')) {
    let interactionToNextPaint = 0
    const observer = new PerformanceObserver(list => {
      for (const entry of list.getEntries()) interactionToNextPaint = Math.max(interactionToNextPaint, entry.duration)
    })
    observer.observe({ type: 'event', buffered: true, durationThreshold: 40 } as PerformanceObserverInit)
    window.addEventListener('pagehide', () => {
      if (interactionToNextPaint > 0) reportWebVital('INP', interactionToNextPaint)
      observer.disconnect()
    }, { once: true })
  }
  window.addEventListener('load', () => {
    const paint = performance.getEntriesByType('paint').find(entry => entry.name === 'first-contentful-paint')
    if (paint) reportWebVital('FCP', paint.startTime)
    const navigation = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    if (navigation) reportWebVital('TTFB', navigation.responseStart)
  }, { once: true })
}

if (typeof window !== 'undefined') {
  window.addEventListener('error', () => track('frontend_unhandled_error', { section: currentSection(), error_kind: 'javascript_error' }))
  window.addEventListener('unhandledrejection', () => track('frontend_unhandled_error', { section: currentSection(), error_kind: 'unhandled_rejection' }))
  observeWebVitals()
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushWithBeacon()
  })
}