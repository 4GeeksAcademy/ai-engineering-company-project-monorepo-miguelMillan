# Endurecimiento del servidor de TrackFlow

Aplica a Debian 12 o Ubuntu 22.04+. Este entorno (Codespaces) **no es el servidor**: el guion se niega a ejecutarse aquí y se ha probado en contenedores desechables. Todavía no se ha aplicado a ningún servidor real.

## Aplicarlo

```bash
sudo OPS_SSH_KEY_FILE=/root/ops.pub scripts/hardening/harden-server.sh   # idempotente; opcional SSH_PORT, WAN_IFACE
sudo scripts/hardening/check-hardening.sh                                # 16 comprobaciones, no cambia nada
```

Antes de cerrar la sesión, entra desde otra como `trackflow-ops`. Sin una clave pública válida no toca nada, y si la configuración SSH no valida (`sshd -t`) restaura la anterior.

## Qué hace

**1. Usuario de operaciones.** `trackflow-ops` entra por SSH solo con clave y sin contraseña. No está en `docker` ni en `sudo`, porque ambos equivalen a root. Su único `sudo` es `/usr/local/sbin/trackflow-ctl` (`status`, `up`, `logs`, `restart`, `accounts`), con acciones y servicios cerrados, y cada uso queda registrado. La aplicación corre con `trackflow`, sin login.

**2. SSH.** `PermitRootLogin no`, sin contraseñas, `AllowGroups trackflow-ssh`, 3 intentos, sin reenvío de agente ni X11. El archivo empieza por `00-` porque en sshd manda el primer valor, y así gana a `50-cloud-init.conf`.

**3. Carpetas.**

| Ruta | Dueño y permisos | Para qué |
|---|---|---|
| `/opt/trackflow/app` | `root:trackflow` 750 | Código. La app lo lee y no puede modificarlo |
| `/etc/trackflow/trackflow.env` | `root:root` 600 | Secretos. Ni operaciones ni la app leen el archivo; Docker los inyecta |
| `/var/log/trackflow` | `trackflow:trackflow-logs` 2750 | Logs. Operaciones solo lee |
| `/var/lib/trackflow` | `trackflow:trackflow` 700 | Datos (cuentas, PDF de RFP) |

**4. Firewall (nftables).** Entrada denegada salvo SSH, 80 y 443. Docker publica puertos con DNAT y **se salta la cadena de entrada, y por tanto también `ufw`**; por eso hay una regla en `forward` que los corta. Probado quitándola: el puerto 6379 vuelve a quedar abierto. También se bloquea el servicio de metadatos de la nube desde los contenedores.

| Puerto | Servicio | Público |
|---|---|---|
| 22 | SSH de operaciones | Sí, solo clave |
| 80, 443 | Caddy: frontend y API por HTTPS | Sí |
| 8080, 8081 | Gateway y API | No, los sirve Caddy |
| 6379, 6333, 5555, 8090 | Redis, Qdrant, Flower, MCP | No. Desde fuera: `ssh -L 5555:127.0.0.1:5555 trackflow-ops@servidor` |

**5. TLS.** `scripts/hardening/Caddyfile.example` (validado con `caddy validate`): HTTPS automático, HSTS, CSP, sin exponer `/internal/*` ni `/docs`. Para leer el build: `usermod -aG trackflow caddy`.

**6. Contenedores** (`docker-compose.yml`). `backend`, `mcp`, `celery-worker` y `flower` sin root, sin capacidades y con `no-new-privileges`. Probado con la imagen real: la API y el worker arrancan con el código en solo lectura. `interfaces` es el entorno de desarrollo (Vite) y no se usa en el servidor.

## Variables en el servidor (`/etc/trackflow/trackflow.env`)

`BIND_ADDRESS=127.0.0.1`, `APP_ENV=production`, `APP_UID` y `APP_GID` de `trackflow` (`id trackflow`), `SUPPLIERS_DB_PATH=/var/lib/trackflow/suppliers.json` (dentro del volumen), `RFP_RAW_HOST_DIR=/var/lib/trackflow/rfp`, `RFP_APPROVERS`, y las de siempre. `docker-compose.yml` lee además `.env` en la raíz: `ln -s /etc/trackflow/trackflow.env /opt/trackflow/app/.env`.

Si ya existía el volumen `backend_data` (creado por root), una vez:

```bash
docker compose run --rm --user root --cap-add CHOWN --entrypoint chown backend -R "$APP_UID:$APP_GID" /var/lib/trackflow
```

## Evidencia (antes y después, medido desde otro contenedor)

`scripts/hardening/test/run.sh`; salida completa en [evidence/server-hardening.txt](evidence/server-hardening.txt).

| | Antes | Después |
|---|---|---|
| Puertos TCP abiertos, los 65535 por IPv4 (nmap) | 8 | 3 (22, 80, 443) |
| Puertos TCP abiertos por IPv6, los 8 con servicio | 7 | 3 (22, 80, 443) |
| root por SSH con contraseña o con clave | Entra | Denegado |
| `trackflow-ops` con su clave / con otra | — | Entra / denegado |
| Operaciones lee los secretos o `/etc/shadow`, o cambia el código | — | Denegado |
| La app modifica su propio código | — | Denegado |
| Comprobaciones | 16 fallan | 16 correctas |

## Límites

`trackflow-ctl up` y `restart` ejecutan Docker como root sobre código que solo root puede escribir: el despliegue sigue siendo tarea de root. Redis y Qdrant no tienen contraseña en loopback: un proceso local del servidor podría usarlos. No hay fail2ban; el firewall limita conexiones SSH nuevas por IP (solo IPv4).
