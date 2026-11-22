jest.mock('../utils/auth', () => ({ apiRequest: jest.fn(), apiFetch: jest.fn() }))

import { canGenerate, isPending, parseDraft, validateRfpFile, MAX_RFP_BYTES } from '../utils/rfps'

const file = (name: string, size = 10) => ({ name, size } as File)

describe('recepción de RFP (utils)', () => {
  it('solo acepta PDF de hasta 10 MB', () => {
    expect(validateRfpFile(file('trackflow-request-1.pdf'))).toBeNull()
    expect(validateRfpFile(file('RFP.PDF'))).toBeNull()
    expect(validateRfpFile(file('rfp.docx'))).toMatch('no es un PDF')
    expect(validateRfpFile(file('grande.pdf', MAX_RFP_BYTES + 1))).toMatch('10 MB')
  })

  it('sigue refrescando mientras el ticket está analizando, salvo si falló', () => {
    expect(isPending({ status: 'analizando', pipeline_stage: 'metadatos' })).toBe(true)
    expect(isPending({ status: 'analizando', pipeline_stage: 'fallido' })).toBe(false)
    expect(isPending({ status: 'analisis_completo', pipeline_stage: 'handoff' })).toBe(false)
    expect(isPending({ status: 'descartado', pipeline_stage: 'clasificado' })).toBe(false)
  })

  it('sigue refrescando durante la Parte 2 y deja regenerar solo cuando no hay trabajo en curso', () => {
    const t = (status: string, response_stage: string | null) => ({ status, pipeline_stage: 'handoff', response_stage })
    expect(isPending(t('analisis_completo', 'generando'))).toBe(true)
    expect(isPending(t('en_evaluación', 'evaluando'))).toBe(true)
    expect(isPending(t('needs_human_review', 'handoff'))).toBe(false)
    expect(isPending(t('en_evaluación', 'fallido'))).toBe(false)
    expect(canGenerate(t('analisis_completo', null))).toBe(true)
    expect(canGenerate(t('needs_human_review', 'handoff'))).toBe(true)
    expect(canGenerate(t('en_evaluación', 'fallido'))).toBe(true)
    expect(canGenerate(t('en_evaluación', 'evaluando'))).toBe(false)
    expect(canGenerate(t('descartado', null))).toBe(false)
  })

  it('interpreta el borrador de sección: encabezados, tablas y listas', () => {
    const blocks = parseDraft('## Warehouse — Propuesta\n\nTexto.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n- SLA **90 %**')
    expect(blocks.map((b) => b.kind)).toEqual(['heading', 'paragraph', 'table', 'list'])
    expect(blocks[2]).toEqual({ kind: 'table', header: ['A', 'B'], rows: [['1', '2'], ['3', '4']] })
  })
})
