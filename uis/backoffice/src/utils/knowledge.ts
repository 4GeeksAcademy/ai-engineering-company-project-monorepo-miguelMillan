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
