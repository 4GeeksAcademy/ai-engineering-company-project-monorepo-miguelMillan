export class IncidentApiError extends Error {
  fields: Record<string, string>

  constructor(message: string, fields: Record<string, string> = {}) {
    super(message)
    this.fields = fields
  }
}

export function incidentError(cause: unknown): string {
  return cause instanceof IncidentApiError ? cause.message : 'No se pudo completar la operación. Inténtalo de nuevo.'
}