#!/usr/bin/env bash
# Comprueba (sin cambiar nada) el estado de endurecimiento del servidor. Sale con 1 si algo falla.
# El puerto por puerto se mide DESDE FUERA: ver test/run.sh.
set -u
fails=0
check() { if eval "$2" &>/dev/null; then echo "  OK    $1"; else echo "  FALLA $1"; fails=$((fails + 1)); fi; }
sshd_value() { [[ "$(sshd -T -C user=x,host=x,addr=127.0.0.1 2>/dev/null | awk -v k="$1" '$1==k{print $2; exit}')" == "$2" ]]; }
perms() { [[ "$(stat -c '%U:%G %a' "$1" 2>/dev/null)" == "$2" ]]; }

echo "Usuarios"
check "existe trackflow-ops, sin contraseña"            "id trackflow-ops && passwd -S trackflow-ops | grep -Eq '^trackflow-ops L'"
check "trackflow-ops no es root-equivalente (docker/sudo/adm)" "id trackflow-ops && ! id -nG trackflow-ops | grep -Eqw 'docker|sudo|wheel|adm|root|lxd'"
check "su único sudo es trackflow-ctl"                  "[[ \"\$(grep -c NOPASSWD /etc/sudoers.d/trackflow-ops)\" == 1 ]] && grep -Eq 'NOPASSWD: /usr/local/sbin/trackflow-ctl$' /etc/sudoers.d/trackflow-ops && perms /usr/local/sbin/trackflow-ctl 'root:root 755'"
check "la aplicación corre con un usuario sin login"    "getent passwd trackflow | grep -q nologin"
echo "SSH"
check "root no puede entrar"                            "sshd_value permitrootlogin no"
check "sin contraseñas"                                 "sshd_value passwordauthentication no && sshd_value kbdinteractiveauthentication no"
check "solo el grupo trackflow-ssh"                     "sshd_value allowgroups trackflow-ssh"
check "máximo 3 intentos por conexión"                  "sshd_value maxauthtries 3"
echo "Carpetas"
check "código: root lo escribe, la app solo lo lee"     "perms /opt/trackflow/app 'root:trackflow 750' && [[ -z \"\$(find /opt/trackflow/app ! -type l \( -perm /022 -o -perm /007 -o ! -user root \) -print -quit)\" ]]"
check "secretos: solo root"                             "perms /etc/trackflow/trackflow.env 'root:root 600' && perms /etc/trackflow 'root:trackflow 750'"
check "logs: la app escribe, operaciones lee"           "perms /var/log/trackflow 'trackflow:trackflow-logs 2750'"
check "datos: solo la app"                              "perms /var/lib/trackflow 'trackflow:trackflow 700'"
echo "Firewall"
check "entrada denegada por defecto"                    "nft list chain inet trackflow input | grep -q 'policy drop'"
check "los puertos publicados por Docker también"       "nft list chain inet trackflow forward | grep -q 'ct status dnat'"
check "sin acceso a los metadatos de la nube"           "nft list chain inet trackflow forward | grep -q 169.254.169.254"
check "se carga al arrancar"                            "[[ -e /etc/systemd/system/trackflow-firewall.service && -e /etc/nftables.d/trackflow.nft ]]"
echo; [[ $fails -eq 0 ]] && echo "Todo correcto." || echo "$fails comprobaciones fallan."
exit $((fails > 0))
