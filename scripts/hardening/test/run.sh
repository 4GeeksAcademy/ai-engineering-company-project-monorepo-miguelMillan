#!/usr/bin/env bash
# Antes/después del endurecimiento, medido desde OTRO contenedor («internet»). Todo es desechable; no toca el host.
#   scripts/hardening/test/run.sh | tee docs/security/evidence/server-hardening.txt
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ID="tf-harden-$$"
TMP="$(mktemp -d)"
cleanup() { docker rm -f "$ID-srv" "$ID-atk" "$ID-ctn" &>/dev/null; docker network rm "$ID-wan" "$ID-lan" &>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT
docker build -q -t trackflow-hardening-test "$HERE" >/dev/null || exit 1
docker network create --ipv6 --subnet fd00:7f::/64 "$ID-wan" >/dev/null; docker network create --internal "$ID-lan" >/dev/null
ssh-keygen -q -t ed25519 -N '' -C ops -f "$TMP/ops"; ssh-keygen -q -t ed25519 -N '' -C otra -f "$TMP/otra"; chmod 755 "$TMP"; chmod 644 "$TMP"/*.pub

docker run -d --name "$ID-srv" --network "$ID-wan" --cap-add NET_ADMIN --sysctl net.ipv4.ip_forward=1 \
  -v "$HERE/..:/hardening:ro" -v "$TMP:/keys:ro" trackflow-hardening-test sleep infinity >/dev/null
docker network connect "$ID-lan" "$ID-srv"
docker run -d --name "$ID-atk" --network "$ID-wan" trackflow-hardening-test sleep infinity >/dev/null
docker run -d --name "$ID-ctn" --network "$ID-lan" trackflow-hardening-test bash -c "cd /run; exec python3 -m http.server 6379" >/dev/null   # un «Redis» en Docker
docker cp -q "$TMP" "$ID-atk:/keys"; docker exec "$ID-atk" sh -c 'chown -R root:root /keys; chmod 600 /keys/*'
srv() { docker exec "$ID-srv" bash -c "$1"; }
atk() { docker exec "$ID-atk" bash -c "$1"; }
IP=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$ID-wan\").IPAddress}}" "$ID-srv")
IP6=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$ID-wan\").GlobalIPv6Address}}" "$ID-srv")
CTN=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$ID-lan\").IPAddress}}" "$ID-ctn")

srv "/usr/sbin/sshd; mkdir -p /opt/trackflow/app /etc/trackflow; echo 'SECRET_KEY=valor-secreto' > /etc/trackflow/trackflow.env; echo print > /opt/trackflow/app/main.py; chmod -R 777 /opt/trackflow
for p in 80 443 5555 6333 8080 8081; do (cd /run && nohup python3 -m http.server --bind :: \$p >/dev/null 2>&1 &); done
# Así publica Docker un puerto: DNAT en PREROUTING (no pasa por la cadena INPUT).
iptables -t nat -A PREROUTING -p tcp -d $IP --dport 6379 -j DNAT --to $CTN:6379; iptables -t nat -A POSTROUTING -d $CTN -j MASQUERADE"
sleep 2
SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR"

report() {
  echo "--- $1"
  echo "Puertos TCP abiertos desde otra máquina (los 65535, IPv4):"
  atk "nmap -n -Pn -sT -T4 --min-rate 3000 --max-retries 1 -p- $IP" | awk '/^[0-9]+\/tcp +open/ {print "  " $1}'
  echo "Puertos TCP abiertos por IPv6 (los 8 con servicio):"
  atk "nmap -6 -n -Pn -sT --max-retries 1 -p 22,80,443,5555,6333,6379,8080,8081 $IP6" | awk '/^[0-9]+\/tcp +open/ {print "  " $1}'
  echo "SSH:"
  printf '  root con contraseña:            %s\n' "$(atk "sshpass -p toor $SSH root@$IP id -un 2>/dev/null || echo denegado" | tail -1)"
  printf '  root con clave autorizada:      %s\n' "$(atk "$SSH -i /keys/ops -o BatchMode=yes root@$IP id -un 2>/dev/null || echo denegado" | tail -1)"
  printf '  trackflow-ops con su clave:     %s\n' "$(atk "$SSH -i /keys/ops -o BatchMode=yes trackflow-ops@$IP id -un 2>/dev/null || echo denegado" | tail -1)"
  printf '  trackflow-ops con otra clave:   %s\n' "$(atk "$SSH -i /keys/otra -o IdentitiesOnly=yes -o BatchMode=yes trackflow-ops@$IP id -un 2>/dev/null || echo denegado" | tail -1)"
  echo "Lo que puede hacer el usuario de operaciones:"
  srv "id trackflow-ops" &>/dev/null || { echo "  (no existe: todo se hace como root)"; return; }
  for c in "cat /etc/trackflow/trackflow.env" "sh -c 'echo x >> /opt/trackflow/app/main.py'" "ls /var/lib/trackflow" "ls /var/log/trackflow" "sudo -n cat /etc/shadow" "sudo -n /usr/local/sbin/trackflow-ctl logs '../../etc'" ; do
    printf '  %-62s %s\n' "$c" "$(srv "su -s /bin/bash trackflow-ops -c \"$c\" >/dev/null 2>&1 && echo permitido || echo denegado")"; done
  printf '  %-62s %s\n' "la app modifica su propio código" "$(srv "su -s /bin/bash trackflow -c 'echo x >> /opt/trackflow/app/main.py' 2>/dev/null && echo permitido || echo denegado")"
}

srv "mkdir -p /root/.ssh && cp /keys/ops.pub /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys"
report "ANTES"; srv /hardening/check-hardening.sh | tail -1
echo; echo "--- Sin clave válida, el guion no toca nada:"
srv "/hardening/harden-server.sh" 2>&1 | tail -1
srv "OPS_SSH_KEY_FILE=/keys/ops /hardening/harden-server.sh" 2>&1 | tail -1
echo; echo "--- Se aplica (dos veces: idempotente)"
srv "OPS_SSH_KEY_FILE=/keys/ops.pub WAN_IFACE=$(srv "ip -o -4 addr show | awk -v ip=$IP '\$4 ~ ip\"/\" {print \$2}'") /hardening/harden-server.sh"
srv "OPS_SSH_KEY_FILE=/keys/ops.pub WAN_IFACE=$(srv "ip -o -4 addr show | awk -v ip=$IP '\$4 ~ ip\"/\" {print \$2}'") /hardening/harden-server.sh" | grep -c OK | sed 's/$/ comprobaciones OK en la segunda pasada/'
echo; report "DESPUÉS"
