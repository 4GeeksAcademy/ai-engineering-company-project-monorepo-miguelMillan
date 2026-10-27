export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'No se pudo conectar con el directorio de proveedores.'
}

export function positiveRate(value: string): number {
  const rate = Number(value)
  if (!Number.isFinite(rate) || rate <= 0) throw new Error('La tarifa debe ser un número mayor que cero.')
  return rate
}