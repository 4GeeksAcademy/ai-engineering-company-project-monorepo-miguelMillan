import { validateKnowledgeQuestion } from '../utils/knowledge'

describe('validateKnowledgeQuestion', () => {
  it('acepta una pregunta normal', () => {
    expect(validateKnowledgeQuestion('¿Cuál es la ventana de devolución estándar?')).toBeNull()
  })

  it.each(['', '  ', 'ab'])('rechaza la pregunta demasiado corta %p', (value) => {
    expect(validateKnowledgeQuestion(value)).toBe('Escribe una pregunta de al menos 3 caracteres.')
  })

  it('rechaza preguntas de más de 1000 caracteres', () => {
    expect(validateKnowledgeQuestion('x'.repeat(1001))).toBe('La pregunta no puede superar los 1000 caracteres.')
  })
})
