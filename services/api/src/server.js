import http from 'node:http'

const port = Number(process.env.PORT ?? 8080)

const server = http.createServer((req, res) => {
  if (req.url === '/health' && req.method === 'GET') {
    const body = JSON.stringify({
      status: 'ok',
      service: 'trackflow-api',
      timestamp: new Date().toISOString(),
    })

    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(body)
    return
  }

  res.writeHead(404, { 'content-type': 'application/json' })
  res.end(JSON.stringify({ error: 'Not Found' }))
})

server.listen(port, '0.0.0.0', () => {
  console.log(`TrackFlow API running on http://0.0.0.0:${port}`)
})
