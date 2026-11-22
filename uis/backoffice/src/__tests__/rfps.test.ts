jest.mock('../utils/auth', () => ({ apiRequest: jest.fn(), apiFetch: jest.fn() }))

import { isPending, validateRfpFile, MAX_RFP_BYTES } from '../utils/rfps'

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
})
