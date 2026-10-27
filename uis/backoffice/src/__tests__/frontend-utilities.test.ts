import { incidentError, IncidentApiError } from '../utils/incidentErrors'
import { errorMessage, positiveRate } from '../utils/supplierUtils'

describe('positiveRate', () => {
  it('acepta una tarifa positiva finita', () => {
    expect(positiveRate('7.25')).toBe(7.25)
  })

  it.each(['0', '-1', 'Infinity', 'NaN', 'invalid'])('rechaza la tarifa %s', (value) => {
    expect(() => positiveRate(value)).toThrow('La tarifa debe ser un número mayor que cero.')
  })
})

describe('errorMessage', () => {
  it('conserva el mensaje de un Error', () => {
    expect(errorMessage(new Error('Proveedor no disponible'))).toBe('Proveedor no disponible')
  })

  it('usa un mensaje seguro para valores que no son Error', () => {
    expect(errorMessage({ reason: 'network' })).toBe('No se pudo conectar con el directorio de proveedores.')
  })
})

describe('incidentError', () => {
  it('conserva el mensaje de IncidentApiError', () => {
    expect(incidentError(new IncidentApiError('No se pudo guardar.'))).toBe('No se pudo guardar.')
  })

  it('usa un mensaje genérico para errores desconocidos', () => {
    expect(incidentError(new Error('respuesta interna'))).toBe('No se pudo completar la operación. Inténtalo de nuevo.')
  })
})