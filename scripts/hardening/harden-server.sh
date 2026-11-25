#!/usr/bin/env bash
# Endurecimiento del servidor de TrackFlow (Debian 12 / Ubuntu 22.04+). Idempotente. Guía: docs/security/server-hardening.md
#
#   sudo OPS_SSH_KEY_FILE=/ruta/ops.pub ./harden-server.sh            # SSH_PORT=22  WAN_IFACE=<interfaz pública>
#
# No cierra las sesiones SSH abiertas: deja la actual abierta y comprueba desde otra que entras antes de cerrarla.
# NO ejecutar en un portátil ni en un Codespace: cambia SSH y el firewall de la máquina.
set -euo pipefail
umask 027

OPS_USER=trackflow-ops; APP_USER=trackflow; SSH_GROUP=trackflow-ssh; LOG_GROUP=trackflow-logs
SSH_PORT="${SSH_PORT:-22}"
WAN_IFACE="${WAN_IFACE:-$(ip -o route show default 2>/dev/null | awk '{print $5; exit}')}"
HERE="$(cd "$(dirname "$0")" && pwd)"

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "ejecútalo con sudo"
[[ -z "${CODESPACES:-}" ]] || die "esto es un Codespace, no el servidor"
[[ -n "$WAN_IFACE" ]] || die "no encuentro la interfaz pública; indica WAN_IFACE"
[[ "$WAN_IFACE" =~ ^[A-Za-z0-9_.@-]{1,15}$ && "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] || die "WAN_IFACE o SSH_PORT no válidos"
# Sin una clave válida no se toca nada: apagar las contraseñas sin ella dejaría el servidor inaccesible.
[[ -r "${OPS_SSH_KEY_FILE:-}" ]] || die "indica OPS_SSH_KEY_FILE con la clave PÚBLICA de operaciones"
grep -q 'PRIVATE KEY' "$OPS_SSH_KEY_FILE" && die "eso es una clave privada; hace falta la pública (.pub)"
ssh-keygen -l -f "$OPS_SSH_KEY_FILE" >/dev/null || die "OPS_SSH_KEY_FILE no es una clave pública válida"
for tool in sshd nft visudo useradd; do command -v "$tool" >/dev/null || die "falta $tool (apt install openssh-server nftables sudo)"; done
grep -Eqi '^\s*Include\s+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config || die "sshd_config no incluye /etc/ssh/sshd_config.d/*.conf"

step "Usuarios: $OPS_USER (operaciones, entra por SSH) y $APP_USER (servicio, sin login)"
for group in "$SSH_GROUP" "$LOG_GROUP"; do getent group "$group" >/dev/null || groupadd --system "$group"; done
id "$APP_USER" &>/dev/null || useradd --system --user-group --no-create-home --home-dir /var/lib/trackflow --shell /usr/sbin/nologin "$APP_USER"
id "$OPS_USER" &>/dev/null || useradd --create-home --shell /bin/bash "$OPS_USER"
usermod --password '*' "$OPS_USER"                       # sin contraseña: solo clave SSH
# Nunca en el grupo docker (equivale a root) ni en sudo: solo trackflow-ctl.
usermod --groups "$SSH_GROUP,$LOG_GROUP" "$OPS_USER"
install -d -m 0700 -o "$OPS_USER" -g "$OPS_USER" "/home/$OPS_USER" "/home/$OPS_USER/.ssh"
install -m 0600 -o "$OPS_USER" -g "$OPS_USER" "$OPS_SSH_KEY_FILE" "/home/$OPS_USER/.ssh/authorized_keys"

step "sudo mínimo: solo trackflow-ctl"
install -m 0755 -o root -g root "$HERE/trackflow-ctl" /usr/local/sbin/trackflow-ctl
sudoers=$(mktemp)
printf 'Defaults:%s env_reset, use_pty, logfile=/var/log/sudo-trackflow.log\n%s ALL=(root) NOPASSWD: /usr/local/sbin/trackflow-ctl\n' "$OPS_USER" "$OPS_USER" > "$sudoers"
visudo -cqf "$sudoers" || die "sudoers no válido"
install -m 0440 -o root -g root "$sudoers" /etc/sudoers.d/trackflow-ops
rm -f "$sudoers"

step "Carpetas: código, configuración sensible, logs y datos separados"
install -d -m 0750 -o root -g "$APP_USER" /opt/trackflow /opt/trackflow/app
install -d -m 0750 -o root -g "$APP_USER" /etc/trackflow
install -d -m 2750 -o "$APP_USER" -g "$LOG_GROUP" /var/log/trackflow
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" /var/lib/trackflow /var/lib/trackflow/rfp
# El código lo despliega root y la aplicación solo lo lee: si la comprometen, no puede modificarse a sí misma.
chown -R "root:$APP_USER" /opt/trackflow/app
chmod -R u=rwX,g=rX,o= /opt/trackflow/app
[[ -e /etc/trackflow/trackflow.env ]] || install -m 0600 -o root -g root /dev/null /etc/trackflow/trackflow.env
chown root:root /etc/trackflow/trackflow.env; chmod 0600 /etc/trackflow/trackflow.env   # ni operaciones ni la app lo leen

step "SSH: sin root y sin contraseñas"
dropin=/etc/ssh/sshd_config.d/00-trackflow.conf     # 00-: en sshd manda el primer valor (gana a 50-cloud-init.conf)
backup=$(mktemp); [[ ! -e $dropin ]] || cp "$dropin" "$backup"
cat > "$dropin" <<CONF
Port $SSH_PORT
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
AllowGroups $SSH_GROUP
MaxAuthTries 3
LoginGraceTime 20
MaxStartups 10:30:60
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding local
PermitTunnel no
ClientAliveInterval 300
ClientAliveCountMax 2
LogLevel VERBOSE
CONF
chmod 0644 "$dropin"
mkdir -p /run/sshd
if ! sshd -t; then
  [[ -s $backup ]] && cp "$backup" "$dropin" || rm -f "$dropin"
  die "la configuración SSH no es válida; se ha restaurado la anterior"
fi
rm -f "$backup"

step "Firewall: entrada denegada salvo SSH ($SSH_PORT), 80 y 443 en $WAN_IFACE"
install -d -m 0755 /etc/nftables.d
cat > /etc/nftables.d/trackflow.nft <<NFT
# Solo toca su propia tabla (sin «flush ruleset», que borraría las reglas de Docker).
table inet trackflow
delete table inet trackflow
table inet trackflow {
  chain input {
    type filter hook input priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    iifname "lo" accept
    meta l4proto ipv6-icmp accept
    ip protocol icmp limit rate 10/second accept
    tcp dport $SSH_PORT ct state new meter ssh_new { ip saddr limit rate over 12/minute burst 12 packets } drop
    tcp dport { $SSH_PORT, 80, 443 } accept
    iifname != "$WAN_IFACE" accept comment "redes internas de Docker hacia el host"
  }
  chain forward {
    type filter hook forward priority filter - 1; policy accept;
    ct state established,related accept
    # Docker publica puertos con DNAT y se salta la cadena input (y ufw): aquí se cortan, salvo HTTP/HTTPS.
    iifname "$WAN_IFACE" ct status dnat meta l4proto { tcp, udp } ct original proto-dst != { 80, 443 } drop
    # Un contenedor comprometido no puede pedir credenciales al servicio de metadatos de la nube (SSRF).
    ip daddr 169.254.169.254 drop
  }
}
NFT
chmod 0644 /etc/nftables.d/trackflow.nft
nft -c -f /etc/nftables.d/trackflow.nft || die "reglas de firewall no válidas"
cat > /etc/systemd/system/trackflow-firewall.service <<'UNIT'
[Unit]
Description=Firewall de TrackFlow
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables.d/trackflow.nft

[Install]
WantedBy=multi-user.target
UNIT
nft -f /etc/nftables.d/trackflow.nft
if [[ -d /run/systemd/system ]]; then
  systemctl daemon-reload
  systemctl enable -q trackflow-firewall.service
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
else
  pkill -HUP -o -x sshd || true   # sin systemd (contenedor de pruebas)
fi

step "Comprobación"
"$HERE/check-hardening.sh"
echo "Abre AHORA otra sesión como $OPS_USER antes de cerrar esta."
