export type SseMessage = { id: string; event: string; data: string }

/** Parser incremental de `text/event-stream`: tolera trozos cortados en cualquier punto, CRLF y comentarios keep-alive. */
export function createSseParser(onMessage: (message: SseMessage) => void) {
  let buffer = ''
  let id = ''
  let event = ''
  let data: string[] = []

  function line(text: string) {
    if (text === '') {
      if (data.length > 0) onMessage({ id, event: event || 'message', data: data.join('\n') })
      event = ''
      data = []
      return
    }
    if (text.startsWith(':')) return
    const colon = text.indexOf(':')
    const name = colon === -1 ? text : text.slice(0, colon)
    const value = colon === -1 ? '' : text.slice(colon + 1).replace(/^ /, '')
    if (name === 'id') id = value
    else if (name === 'event') event = value
    else if (name === 'data') data.push(value)
  }

  return {
    feed(chunk: string) {
      buffer += chunk
      // Un CR al final puede ser la mitad de un CRLF: espera al siguiente trozo.
      for (let match = /\r\n|\n|\r(?!$)/.exec(buffer); match; match = /\r\n|\n|\r(?!$)/.exec(buffer)) {
        const text = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        line(text)
      }
    },
    /** Último `id:` recibido, aunque llegara en un bloque sin datos (el cursor inicial). */
    lastEventId: () => id,
  }
}
