import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const cwd = fileURLToPath(new URL('..', import.meta.url))
const port = process.env.SUPPLIERS_PORT ?? '8081'
const uvicorn = ['main:app', '--host', '0.0.0.0', '--port', port, '--timeout-graceful-shutdown', '5']
// En producción, el uvicorn de la imagen: `uv run` crearía un .venv dentro del código, que allí es de solo lectura.
const [suppliersCommand, suppliersArgs] = process.env.FASTAPI_RELOAD === '1' ? ['uvicorn', [...uvicorn, '--reload']]
  : process.env.APP_ENV === 'production' ? ['uvicorn', uvicorn]
    : ['uv', ['run', 'serve']]
const suppliers = spawn(suppliersCommand, suppliersArgs, { cwd, stdio: 'inherit', env: process.env })
const api = spawn(process.execPath, ['src/server.js'], { cwd, stdio: 'inherit', env: process.env })
let stopping = false

function stop(code = 0) {
  if (stopping) return
  stopping = true
  process.exitCode = code
  suppliers.kill('SIGTERM')
  api.kill('SIGTERM')
}

for (const child of [suppliers, api]) {
  child.on('error', (error) => {
    console.error(error.message)
    stop(1)
  })
  child.on('exit', (code) => stop(code ?? 1))
}
process.on('SIGINT', () => stop())
process.on('SIGTERM', () => stop())