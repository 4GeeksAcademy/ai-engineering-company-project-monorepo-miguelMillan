const BASE_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000

/** 1 s, 2 s, 4 s… hasta 30 s, con jitter entre la mitad y el total para que las pestañas no reconecten a la vez. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** (attempt - 1))
  return Math.round(ceiling / 2 + random() * (ceiling / 2))
}

export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => { clearTimeout(timer); resolve() }, { once: true })
  })
}
