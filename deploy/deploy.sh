#!/usr/bin/env bash
# Corre EN la EC2 (vía SSH desde GitHub Actions, o a mano) después de que el código
# ya está actualizado en el working tree. Instala dependencias y reinicia el servicio.
set -euo pipefail

cd "$(dirname "$0")/.."

.venv/bin/pip install -q -r requirements.txt

sudo systemctl restart rentafy-backend

sleep 2
if sudo systemctl is-active --quiet rentafy-backend; then
  echo "rentafy-backend: active"
else
  echo "rentafy-backend: FAILED to start" >&2
  sudo journalctl -u rentafy-backend -n 50 --no-pager
  exit 1
fi
