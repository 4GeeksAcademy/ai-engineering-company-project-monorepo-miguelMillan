import { apiRequest } from './auth'

export const KNOWLEDGE_QUESTION_MIN_LENGTH = 3
export const KNOWLEDGE_QUESTION_MAX_LENGTH = 1000

export const knowledgeExampleQuestions = [
  '¿Cuál es la ventana de devolución estándar?',
  '¿Qué transportista cubre mejor Aragón rural?',
  '¿Podemos garantizar el SLA de entrega durante Black Friday?',
  '¿Cuánto cuesta el almacenamiento en Zaragoza y hay descuentos por volumen?',
]

export function validateKnowledgeQuestion(question: string): string | null {
  const trimmed = question.trim()
  if (trimmed.length < KNOWLEDGE_QUESTION_MIN_LENGTH) return 'Escribe una pregunta de al menos 3 caracteres.'
  if (trimmed.length > KNOWLEDGE_QUESTION_MAX_LENGTH) return 'La pregunta no puede superar los 1000 caracteres.'
  return null
}

export class KnowledgeAnswerError extends Error {}

/** Envía la pregunta a POST /knowledge/query y devuelve solo el texto generado. */
export async function askKnowledgeBase(question: string, signal?: AbortSignal): Promise<string> {
  const result = await apiRequest<{ answer?: unknown }>('/knowledge/query', {
    method: 'POST',
    body: JSON.stringify({ question: question.trim() }),
    signal,
  })
  if (typeof result?.answer !== 'string' || !result.answer.trim()) {
    // Una respuesta vacía nunca se muestra como si fuera una respuesta válida.
    throw new KnowledgeAnswerError('El asistente no devolvió ninguna respuesta. Inténtalo de nuevo.')
  }
  return result.answer.trim()
}
