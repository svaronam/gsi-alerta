# GSI-ALERTA

Vigila **cada minuto** la sede electrónica del INAP y avisa en el móvil en cuanto se publican las notas del **2.º ejercicio** de la oposición al Cuerpo de Gestión de Sistemas e Informática (GSI, A2, ingreso libre, convocatoria 2025). El aviso dice si **apareces en el listado de aprobados**, con qué nota y en qué **puesto final** quedas.

- `server/`: servicio en Python para una Raspberry Pi (systemd). Solo usa `requests` y `pdftotext`.
- `android/`: APK propia en Kotlin. Mantiene una conexión abierta con la Pi (*long-poll*), así que el aviso llega en segundos y no cada 15 minutos.
- Integración con [Pi Monitor](https://github.com/svaronam/pi-monitor): los avisos y la señal de vida llegan también a su app, y si el servicio se cuelga, Pi Monitor lo reinicia.
- `.github/workflows/`: tests del servidor y compilación de la APK en cada push.

## Cómo decide

1. **Página**: descarga la página de la convocatoria y busca, bajo el título «Segundo ejercicio», los listados *Acceso general* y *Cupo Norma Común 13*, o un PDF llamado `2-GSI-L-…SUP_GEN…`. Los enlaces «Segundo ejercicio» de la distribución por aulas no cuentan.
2. **PDF**: lo pasa a texto con `pdftotext -layout`, o con `pypdf` si no está instalado, y saca todas las filas (DNI enmascarado, nombre y nota).
3. **Veredicto**:
   - ✅ **Apruebas**: con tu nota y tu puesto final.
   - ❌ **No apareces**: solo si el PDF se ha leído bien (tiene decenas de DNI). Si no se puede leer, avisa de que **ya está publicado** y reintenta cada minuto. Nunca da un «no apareces» por un PDF mal leído.
   - 📢 **Novedad**: cualquier PDF nuevo que aparezca en la página.
   - ⚠️ **Web caída**: si la web del INAP falla más de 20 minutos seguidos.
4. **Puesto final**, según las bases (BOE-A-2025-26262, anexo IX y normas comunes 13.5 y 13.6):
   - **Nota final**: calificación del 1.er ejercicio + calificación del 2.º. Las dos son notas transformadas de 0 a 50, que es lo que publican los listados.
   - **Desempate**: más nota en el 2.º y después en el 1.º. Los aciertos y errores del 1.º no se publican, así que esos empates se indican como tales.
   - **Cupo de discapacidad**: los primeros del cupo, hasta sus 46 plazas, se quedan su plaza. Los que aprueben y no quepan compiten en el general con su nota. Las plazas del cupo que queden vacías **no** pasan al general.
   - **Resultado**: puesto en acceso general, si estás dentro de las **634 plazas** y puesto global.

## Instalación en la Pi

```bash
scp -r gsi-alerta usuario@homelab2:~/
ssh usuario@homelab2
cd ~/gsi-alerta/server
nano config.json        # pon tu nombre tal como sale en los listados: "APELLIDOS, NOMBRE"
bash install.sh
```

`install.sh` hace todo esto:
- instala `poppler-utils` y crea el entorno virtual;
- hace una **barrida de prueba** contra la web real, que debe mostrar tu nota y tu puesto del 1.er ejercicio;
- instala el servicio `gsialerta`;
- lo enlaza en Pi Monitor, si está instalado;
- imprime el servidor y la clave para la app.

```bash
journalctl -u gsialerta -f                           # qué está haciendo
venv/bin/python gsialerta.py --probar                # barrida en seco
sudo systemctl disable --now gsialerta               # cuando ya no haga falta
```

### `config.json`

| campo | qué es |
|---|---|
| `nombre` | `APELLIDOS, NOMBRE`, como en los listados |
| `url` | página de la convocatoria |
| `puerto` | 8806 |
| `api_key` | se genera sola la primera vez |
| `intervalo_segundos` | 60 (mínimo 30) |
| `aviso_caida_minutos` | minutos de fallos seguidos antes de avisar |
| `plazas_general`, `plazas_cupo` | 634 y 46 |
| `pi_agent`, `pi_agent_bot` | dirección de pi-agent en la misma Pi (`http://127.0.0.1:8790`); vacío para desactivarlo |

## APK

1. Descarga `GSI-ALERTA.apk` de **Releases** e instálala.
2. En Ajustes pon el servidor (`http://homelab2:8806` o la IP de Tailscale) y la clave.
3. Acepta las notificaciones y quita la optimización de batería.
4. Pulsa **Probar aviso**.

El resultado llega por un canal con sonido de alarma. Al tocarlo se abre el PDF.

## API

Todas las llamadas llevan la cabecera `X-API-Key`.

| | |
|---|---|
| `GET /api/ping` | sin clave |
| `GET /api/estado` | resumen: última barrida, nota y puesto del 1.º, resultado y puesto final |
| `GET /api/eventos?desde=N&espera=S` | avisos con id > N; espera hasta S segundos (máx. 60) a que llegue alguno |
| `POST /api/check` | barrida inmediata |
| `POST /api/prueba` | aviso de prueba |

## Tests

```bash
cd server && python tests/test_gsialerta.py
```

Usan el HTML real de la sede del INAP. Los PDF de prueba tienen el mismo formato que los listados oficiales pero con nombres inventados, y se generan con `tests/generar_pdfs.py`. Los listados reales no se suben porque llevan datos de otras personas: si los pones en `tests/fixtures/reales/`, `tests/test_pdf_reales.py` los usa.
