#!/usr/bin/env bash
# Instala GSI-ALERTA en la Pi (pensado para homelab2, usuario sergio).
#   cd ~/gsi-alerta/server && bash install.sh
set -e
cd "$(dirname "$0")"

if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "Creado config.json: pon tu nombre (\"APELLIDOS, NOMBRE\") y vuelve a lanzar install.sh"
  exit 1
fi
if grep -q '"APELLIDOS, NOMBRE"' config.json; then
  echo "Falta tu nombre en config.json (\"APELLIDOS, NOMBRE\")"; exit 1
fi

echo "== Paquetes del sistema (pdftotext)"
sudo apt-get install -y python3-venv poppler-utils

echo "== Entorno Python"
python3 -m venv venv
venv/bin/pip install -q -r requirements.txt

echo "== Prueba en seco contra la web del INAP"
venv/bin/python gsialerta.py --probar

echo "== Servicio systemd"
sudo cp gsialerta.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gsialerta
sudo systemctl restart gsialerta   # restart: si ya estaba en marcha, coge el código nuevo
sleep 2
systemctl --no-pager --lines=5 status gsialerta || true

echo "== Pi Monitor"
if [ -f /etc/pi-agent/config.json ]; then
  # Enlaza gsialerta.service en pi-agent (categoría Oposiciones) si no lo estaba
  sudo python3 - <<'PY'
import json, os
ruta = "/etc/pi-agent/config.json"
st = os.stat(ruta)
cfg = json.load(open(ruta))
bots = cfg.setdefault("bots", [])
if any(b.get("unit") == "gsialerta.service" for b in bots):
    print("  Ya estaba enlazado en Pi Monitor")
else:
    bots.append({"unit": "gsialerta.service", "name": "gsi-alerta", "cat": "opos", "managed": False,
                 "autorestart": True, "max_restarts": 3, "hang_after": 15})
    tmp = ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, ruta)
    print("  Enlazado en Pi Monitor como gsi-alerta (Oposiciones)")
PY
  sudo systemctl restart pi-agent && echo "  pi-agent reiniciado"
else
  echo "  pi-agent no está instalado en esta Pi; los avisos irán solo a la APK de GSI-ALERTA"
fi

echo
echo "Listo. Datos para la app:"
echo "  Servidor: http://$(hostname):$(python3 -c 'import json;print(json.load(open("config.json"))["puerto"])')"
echo "  Clave:    $(python3 -c 'import json;print(json.load(open("config.json"))["api_key"])')"
echo "Logs: journalctl -u gsialerta -f"
