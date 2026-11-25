import http from 'node:http'
import { randomUUID } from 'node:crypto'

const port = Number(process.env.PORT ?? 8080)
// Solo los orígenes de las UI de TrackFlow (nunca '*'): la misma variable que usa FastAPI.
const corsOrigins = (process.env.CORS_ORIGINS ?? 'http://localhost:5174,http://127.0.0.1:5174').split(',').map((o) => o.trim())
const maxUploadBytes = 10 * 1024 * 1024

function corsHeaders(req) {
  const origin = req.headers.origin
  return origin && corsOrigins.includes(origin) ? { 'access-control-allow-origin': origin, vary: 'Origin' } : {}
}

function sendJson(res, statusCode, body) {
  res.writeHead(statusCode, {
    ...corsHeaders(res.req),
    'x-content-type-options': 'nosniff',
    'content-type': 'application/json; charset=utf-8',
  })
  res.end(JSON.stringify(body))
}

async function readRequestBody(req) {
  const chunks = []
  let size = 0

  for await (const chunk of req) {
    size += chunk.length
    if (size > maxUploadBytes) {
      throw Object.assign(new Error('El archivo supera el límite de 10 MB.'), { statusCode: 413 })
    }
    chunks.push(chunk)
  }

  return Buffer.concat(chunks)
}

function parseMultipart(req, body) {
  const request = new Request(`http://localhost${req.url}`, {
    method: req.method,
    headers: { 'content-type': req.headers['content-type'] ?? '' },
    body,
    duplex: 'half',
  })
  return request.formData()
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      ...corsHeaders(req),
      'access-control-allow-methods': 'GET, POST, PATCH, DELETE, OPTIONS',
      'access-control-allow-headers': 'content-type, authorization',
    })
    res.end()
    return
  }

  const incidentManagement = /^\/api\/incidents(?:\/|\?|$)/.test(req.url)
    && !/^\/api\/incidents\/(?:analyze|results\/export)(?:\?|$)/.test(req.url)
  const taskPath = /^\/tasks\/[^/?]+(?:\?|$)/.test(req.url)
  const incidentAnalysis = /^\/api\/incidents\/(?:analyze|results\/export)(?:\?|$)/.test(req.url)
  let bufferedBody
  if (/^\/suppliers(?:\/|\?|$)/.test(req.url) || incidentManagement || taskPath || incidentAnalysis) {
    if (incidentAnalysis && req.method === 'POST') {
      if (!/^bearer \S/i.test(req.headers.authorization ?? '')) {
        // Antes de leer 10 MB de alguien sin sesión. Quién es y si puede lo decide el API (require_staff).
        sendJson(res, 401, { detail: 'Inicia sesión para analizar incidencias.' })
        return
      }
      try {
        bufferedBody = await readRequestBody(req)
        const form = await parseMultipart(req, bufferedBody)
        const file = form.get('file')
        if (!file || typeof file === 'string' || typeof file.arrayBuffer !== 'function') {
          sendJson(res, 400, { detail: 'Falta el archivo CSV en el campo "file".' })
          return
        }
        if (!file.name.toLowerCase().endsWith('.csv')) {
          sendJson(res, 400, { detail: 'El archivo debe tener extensión .csv.' })
          return
        }
        new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
        const taskId = randomUUID()
        req.headers['x-task-id'] = taskId
      } catch (error) {
        const status = error.statusCode ?? (error instanceof TypeError ? 422 : 400)
        sendJson(res, status, { detail: error.message || 'La solicitud debe usar multipart/form-data válido.' })
        return
      }
    }
    const upstreamHeaders = { ...req.headers }
    if (incidentAnalysis && req.method === 'POST') {
      upstreamHeaders['content-length'] = String(bufferedBody.length)
      upstreamHeaders['x-task-id'] = req.headers['x-task-id']
      upstreamHeaders['x-internal-task-token'] = process.env.TASKS_INTERNAL_TOKEN ?? ''
    }
    const upstream = http.request({
      hostname: process.env.SUPPLIERS_HOST ?? '127.0.0.1',
      port: Number(process.env.SUPPLIERS_PORT ?? 8081),
      path: incidentAnalysis && req.method === 'POST' ? '/internal/tasks/analyze' : req.url,
      method: req.method,
      headers: upstreamHeaders,
    }, (response) => {
      res.writeHead(response.statusCode, {
        ...response.headers,
        ...corsHeaders(req),
      })
      response.pipe(res)
    })
    upstream.on('error', () => {
      if (!res.headersSent) sendJson(res, 503, { error: incidentManagement || taskPath || incidentAnalysis ? 'El gestor de incidencias no está disponible.' : 'El directorio de proveedores no está disponible.' })
      else res.destroy()
    })
    req.on('aborted', () => upstream.destroy())
    if (bufferedBody) upstream.end(bufferedBody)
    else req.pipe(upstream)
    return
  }

  if (req.url === '/health' && req.method === 'GET') {
    sendJson(res, 200, {
      status: 'ok',
      service: 'trackflow-api',
      timestamp: new Date().toISOString(),
    })
    return
  }

  sendJson(res, 404, { error: 'Ruta no encontrada.' })
})

server.listen(port, '0.0.0.0', () => {
  console.log(`TrackFlow API running on http://0.0.0.0:${port}`)
})
