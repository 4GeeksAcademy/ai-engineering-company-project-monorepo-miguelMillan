const apiRequest = jest.fn()

jest.mock('../utils/auth', () => ({ apiRequest: (...args: unknown[]) => apiRequest(...args) }))

import { askKnowledgeBase, KnowledgeAnswerError, validateKnowledgeQuestion } from '../utils/knowledge'

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

describe('askKnowledgeBase', () => {
  beforeEach(() => apiRequest.mockReset())

  it('envía solo la pregunta recortada y devuelve el texto generado', async () => {
    apiRequest.mockResolvedValue({ answer: ' La ventana estándar es de 30 días. ' })

    await expect(askKnowledgeBase('  ¿Ventana de devolución?  ')).resolves.toBe('La ventana estándar es de 30 días.')
    expect(apiRequest).toHaveBeenCalledWith('/knowledge/query', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ question: '¿Ventana de devolución?' }),
    }))
  })

  it.each([{ answer: '' }, { answer: '   ' }, {}, null])('trata la respuesta vacía %p como error, no como respuesta', async (payload) => {
    apiRequest.mockResolvedValue(payload)

    await expect(askKnowledgeBase('¿SLA estándar?')).rejects.toBeInstanceOf(KnowledgeAnswerError)
  })

  it('propaga el error del API (p. ej. 503) para mostrarlo en la UI', async () => {
    apiRequest.mockRejectedValue(new Error('El asistente de conocimiento no está disponible en este momento.'))

    await expect(askKnowledgeBase('¿SLA estándar?')).rejects.toThrow('no está disponible')
  })
})
