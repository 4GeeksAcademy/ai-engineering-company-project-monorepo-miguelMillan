#!/usr/bin/env bash
# Los ataques críticos contra un API y un gateway EN MARCHA (de pruebas: crea dos cuentas y, si es vulnerable, un proveedor).
#   scripts/security/live_attacks.sh http://localhost:8081 http://localhost:8080
# Ejecútalo contra main@316530f y contra esta rama. Salida guardada: docs/security/evidence/live-attacks.txt
set -uo pipefail
API="${1:?URL del API}"; GATEWAY="${2:?URL del gateway}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT; printf 'id\n1\n' > "$TMP/a.csv"
J='content-type: application/json'; MAIL="atacante-$RANDOM$RANDOM@example.com"; BODY="{\"email\":\"$MAIL\",\"password\":\"cualquiera-123\"}"
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
row() { printf '%-62s %s\n' "$1" "$2"; }
row 'subida de CSV sin sesión por el gateway' "$(code -F "file=@$TMP/a.csv" "$GATEWAY/api/incidents/analyze")"
row 'informe de telemetría sin sesión' "$(code "$API/telemetry/report")"
code -H "$J" -d "$BODY" "$API/users" >/dev/null
TOKEN=$(curl -s -H "$J" -d "$BODY" "$API/auth/login" | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])') || exit 1
row 'cuenta recién registrada: POST /suppliers' "$(code -H "Authorization: Bearer $TOKEN" -H "$J" -d '{"name":"Falso","country":"USA","categories":["carrier_last_mile"],"rate_per_shipment":0.01,"currency":"USD","status":"active"}' "$API/suppliers")"
for path in /suppliers /api/incidents /inventory/orders /rfp-intake/tickets; do
  row "cuenta recién registrada: GET $path" "$(code -H "Authorization: Bearer $TOKEN" "$API$path")"; done
row '20 contraseñas seguidas contra una cuenta' "$(for i in $(seq 20); do code -H "$J" -d "{\"email\":\"victima-$MAIL\",\"password\":\"p$i\"}" "$API/auth/login"; echo; done | sort | uniq -c | awk '{printf "%s×%s ", $1, $2}')"
row 'CORS del gateway ante Origin: https://evil.example' "$(curl -s -D- -o /dev/null -H 'Origin: https://evil.example' "$GATEWAY/health" | grep -i allow-origin | tr -d '\r' | grep . || echo 'no concedido')"
row 'cabeceras de seguridad (de 3) / openapi.json público' "$(curl -s -D- -o /dev/null "$API/health" | grep -ciE 'x-frame|nosniff|security-policy') / $(code "$API/openapi.json")"
