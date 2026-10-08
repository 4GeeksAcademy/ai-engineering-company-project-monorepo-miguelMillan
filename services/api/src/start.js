import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const cwd = fileURLToPath(new URL('..', import.meta.url))
const suppliers = spawn('uv', ['run', 'serve'], { cwd, stdio: 'inherit', env: process.env })
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