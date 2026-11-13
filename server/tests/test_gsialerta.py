"""Pruebas con el HTML real de la sede del INAP y PDF de listados con su mismo formato
(nombres inventados, generados con tests/generar_pdfs.py).

    cd server && python -m pytest -q tests      (o: python tests/test_gsialerta.py)
"""
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
FIX = AQUI / "fixtures"
sys.path.insert(0, str(AQUI.parent))

import gsialerta as g  # noqa: E402

PDF_2_INTERNA = (FIX / "2-GSI-P-SUP_GEN.pdf").read_bytes()   # 2.º ejercicio (interna)
PDF_1_LIBRE = (FIX / "1-GSI-L-DM-SUP_GEN.pdf").read_bytes()  # 1.er ejercicio (libre)


def bot_falso(paginas, pdfs, nombre="OPOSITORA PRUEBA, ANA"):
    """Bot con la red sustituida: paginas = lista de HTML que irá devolviendo."""
    g.ESTADO = Path(tempfile.mkdtemp()) / "estado.json"
    cfg = dict(g.CONFIG_DEFECTO, nombre=nombre, api_key="x", pi_agent="")
    bot = g.Bot(cfg)
    cola = list(paginas)

    def descargar(url, binario=False):
        if not binario:
            return cola.pop(0) if len(cola) > 1 else cola[0]
        nombre_pdf = url.rsplit("/", 1)[-1]
        for clave, datos in pdfs.items():
            if clave in nombre_pdf:
                return datos
        raise RuntimeError(f"404 {url}")

    bot.descargar = descargar
    return bot


ANTES = (FIX / "libre_antes.html").read_text(encoding="utf-8")
DESPUES = (FIX / "libre_despues.html").read_text(encoding="utf-8")


def test_pagina_actual_sin_notas():
    p = g.analizar_pagina(ANTES, g.URL_LIBRE)
    assert [e["texto"] for e in p["segundo"]] == ["Enunciado del ejercicio"]
    assert p["listados_2"] == {}
    # el enlace "Segundo ejercicio" de las aulas de Madrid NO cuenta
    assert set(p["listados_1"]) == {"general", "cupo"}


def test_pagina_con_notas():
    p = g.analizar_pagina(DESPUES, g.URL_LIBRE)
    assert p["listados_2"]["general"]["href"].endswith("2-GSI-L-SUP_GEN_ABCDEF_154AB89SD658.pdf")
    assert p["listados_2"]["general"]["href"].startswith("https://sede.inap.gob.es/sites/")
    assert "cupo" in p["listados_2"]


def test_lee_pdf_1er_ejercicio():
    r = g.buscar_en_listado(g.texto_pdf(PDF_1_LIBRE), "OPOSITORA PRUEBA, ANA")
    assert r["legible"] and r["encontrado"] and r["nota"] == "28,45"
    assert r["personas"] > 400


def test_nombre_con_tildes_y_espacios():
    r = g.buscar_en_listado(g.texto_pdf(PDF_1_LIBRE), "  opositora   prueba ,ana ")
    assert r["nota"] == "28,45"


def test_lee_pdf_2o_ejercicio():
    t = g.texto_pdf(PDF_2_INTERNA)
    assert g.buscar_en_listado(t, "LANZA HERRERO, JUAN JOSE")["nota"] == "40,08"
    assert g.buscar_en_listado(t, "GARCIA MERCADO, JAVIER")["nota"] == "26"
    no = g.buscar_en_listado(t, "OPOSITORA PRUEBA, ANA")
    assert no["legible"] and not no["encontrado"]


def test_flujo_completo_apruebas():
    bot = bot_falso([ANTES, ANTES, DESPUES],
                    {"1-GSI-L-DM-SUP_GEN": PDF_1_LIBRE, "2-GSI-L-SUP_GEN": PDF_2_INTERNA},
                    nombre="LANZA HERRERO, JUAN JOSE")
    bot.barrer()
    bot.barrer()
    assert bot.estado["eventos"] == []          # primera pasada: solo línea base
    bot.barrer()
    tipos = [e["tipo"] for e in bot.estado["eventos"]]
    assert tipos.count("resultado") == 1
    assert tipos.count("novedad") == 2         # resolución + puntuaciones mínimas
    res = bot.estado["resultado"]
    assert res["veredicto"] == "aprobado" and res["nota"] == "40,08"
    bot.barrer()                                # no repite avisos
    assert [e["tipo"] for e in bot.estado["eventos"]] == tipos


def test_flujo_completo_no_apareces_y_nota1():
    bot = bot_falso([ANTES, DESPUES],
                    {"1-GSI-L-DM-SUP_GEN": PDF_1_LIBRE, "2-GSI-L-SUP_GEN": PDF_2_INTERNA,
                     "2-GSI-L-SUP_CRD": PDF_2_INTERNA})
    bot.barrer()
    assert bot.estado["nota1"] == "28,45"
    bot.barrer()
    res = bot.estado["resultado"]
    assert res["veredicto"] == "no"
    ev = [e for e in bot.estado["eventos"] if e["tipo"] == "resultado"][0]
    assert "No apareces" in ev["titulo"] and ev["url"].endswith(".pdf")


def test_pdf_ilegible_no_da_falso_suspenso():
    bot = bot_falso([ANTES, DESPUES, DESPUES],
                    {"1-GSI-L-DM-SUP_GEN": PDF_1_LIBRE, "2-GSI-L-SUP_GEN": b"<html>error</html>"})
    bot.barrer()
    bot.barrer()
    tipos = [e["tipo"] for e in bot.estado["eventos"]]
    assert "publicado" in tipos and "resultado" not in tipos
    assert not bot.estado["resultado"].get("veredicto")
    bot.barrer()                                # no repite el aviso de publicado
    assert [e["tipo"] for e in bot.estado["eventos"]].count("publicado") == 1


def test_pagina_rota_es_error():
    bot = bot_falso(["<html><body>Mantenimiento</body></html>"], {})
    try:
        bot.barrer()
        assert False, "debería fallar"
    except RuntimeError:
        pass


# ------------------------------------------------------------ puesto final
import random  # noqa: E402

L1 = g.parsear_listado(g.texto_pdf(PDF_1_LIBRE))


def texto_listado(filas):
    """Imita el PDF del INAP pasado por pdftotext -layout (dos columnas)."""
    lineas = []
    for i in range(0, len(filas), 2):
        trozo = [f"{f['nif']}    {f['nombre']:<45}{g.fmt_nota(f['nota']):>8}" for f in filas[i:i + 2]]
        lineas.append("   " + "      ".join(trozo))
    return "CABECERA\n" + "\n\n".join(lineas)


def test_parsea_todas_las_filas():
    assert L1["completo"] and len(L1["filas"]) == 520
    l2 = g.parsear_listado(g.texto_pdf(PDF_2_INTERNA))
    assert l2["completo"] and len(l2["filas"]) == 64
    assert {"nif": "***3684**", "nombre": "LANZA HERRERO, JUAN JOSE", "nota": 40.08} in l2["filas"]


def test_puesto_1er_ejercicio():
    p = g.puesto_1er_ejercicio("OPOSITORA PRUEBA, ANA", L1["filas"])
    mejores = sum(1 for f in L1["filas"] if f["nota"] > 28.45)
    assert p["puesto"] == mejores + 1 and p["de"] == 520


def test_puesto_final_simulado():
    rnd = random.Random(7)
    aprobados = rnd.sample(L1["filas"], 400)
    if not any(f["nombre"] == "OPOSITORA PRUEBA, ANA" for f in aprobados):
        aprobados.append(next(f for f in L1["filas"] if f["nombre"] == "OPOSITORA PRUEBA, ANA"))
    l2 = [{**f, "nota": round(rnd.uniform(25, 45), 2)} for f in aprobados]
    yo2 = next(f for f in l2 if f["nombre"] == "OPOSITORA PRUEBA, ANA")
    yo2["nota"] = 33.5
    # el PDF va ordenado alfabéticamente; lo paso por el parser como el real
    parseado = g.parsear_listado(texto_listado(sorted(l2, key=lambda f: f["nombre"])))
    assert parseado["completo"]
    p = g.calcular_puesto("OPOSITORA PRUEBA, ANA", L1["filas"], parseado["filas"], [], 300, 46)
    n1 = {f["nif"] + f["nombre"]: f["nota"] for f in L1["filas"]}
    totales = [round(n1[f["nif"] + f["nombre"]] + f["nota"], 2) for f in l2]
    esperado = 1 + sum(1 for t in totales if t > 28.45 + 33.5)
    assert p["calculado"] and p["total"] == 61.95
    assert p["nota1"] == 28.45 and p["nota2"] == 33.5
    assert esperado <= p["puesto"] <= esperado + p["empatados"]
    assert p["aprobados_general"] == len(l2)
    assert p["dentro_de_plazas"] == (p["puesto"] <= 300)
    assert "puesto" in g.texto_puesto(p)


def test_desempate_y_cupo():
    l1 = [{"nif": f"***{i:04d}**", "nombre": n, "nota": v} for i, (n, v) in enumerate(
        [("YO, SERGIO", 30.0), ("A, A", 32.0), ("B, B", 28.0), ("C, C", 30.0),
         ("D, D", 40.0), ("E, E", 40.0)])]
    gen2 = [{**l1[0], "nota": 30.0}, {**l1[1], "nota": 28.0},   # A: 60 pero menos en el 2.º
            {**l1[2], "nota": 33.0}, {**l1[3], "nota": 29.0}]
    cupo2 = [{**l1[4], "nota": 35.0}, {**l1[5], "nota": 26.0}]  # D 75 y E 66, cupo de 1 plaza
    p = g.calcular_puesto("YO, SERGIO", l1, gen2, cupo2, plazas_general=3, plazas_cupo=1)
    # ranking general: E 66 (cupo sin plaza) > B 61 > YO 60 (2.º 30) > A 60 (2.º 28) > C 59
    assert p["puesto"] == 3 and p["aprobados_general"] == 5
    assert p["cupo_compitiendo_en_general"] == 1 and p["dentro_de_plazas"]
    assert p["puesto_global"] == 4 and p["empatados"] == 0


def test_flujo_con_puesto():
    yo = next(f for f in L1["filas"] if f["nombre"] == "OPOSITORA PRUEBA, ANA")
    otros = [f for f in L1["filas"] if f is not yo][:99]
    l2 = [{**f, "nota": 30.0} for f in otros] + [{**yo, "nota": 31.0}]
    pdf_falso = texto_listado(sorted(l2, key=lambda f: f["nombre"]))
    viejo = g.texto_pdf
    g.texto_pdf = lambda d: pdf_falso if d == b"%PDF-2" else viejo(d)
    try:
        bot = bot_falso([ANTES, DESPUES], {"1-GSI-L-DM-SUP_GEN": PDF_1_LIBRE,
                                           "2-GSI-L-SUP_GEN": b"%PDF-2"})
        bot.barrer()
        assert bot.estado["l1_ok"] and len(bot.estado["l1"]) == 520
        bot.barrer()
    finally:
        g.texto_pdf = viejo
    res = bot.estado["resultado"]
    assert res["veredicto"] == "aprobado" and res["puesto"]["calculado"]
    assert res["puesto"]["total"] == 59.45
    ev = [e for e in bot.estado["eventos"] if e["tipo"] == "resultado"][0]
    assert "puesto" in ev["texto"] and "59,45" in ev["texto"]
    print("   ", ev["texto"])



def test_avisa_a_pi_monitor():
    """Los avisos y el latido llegan a pi-agent como /api/evento y /api/latido."""
    import json
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    recibido = []

    class FalsoAgente(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers["Content-Length"])
            recibido.append((self.path, json.loads(self.rfile.read(n))))
            self.send_response(200)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), FalsoAgente)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bot = bot_falso([ANTES], {})
    bot.cfg["pi_agent"] = f"http://127.0.0.1:{srv.server_port}"
    bot.evento("resultado", "✅ ¡APRUEBAS EL 2.º EJERCICIO!", "texto", "https://x/y.pdf")
    bot.pimonitor("/api/latido")
    time.sleep(0.5)
    srv.shutdown()
    rutas = dict(recibido)
    ev = rutas["/api/evento"]
    assert ev["bot"] == "gsialerta.service" and ev["prio"] == 5 and ev["cat"] == "opos"
    assert ev["titulo"].startswith("✅") and "https://x/y.pdf" in ev["msg"]
    assert rutas["/api/latido"]["bot"] == "gsialerta.service"


def test_pi_monitor_caido_no_rompe_nada():
    bot = bot_falso([ANTES], {})
    bot.cfg["pi_agent"] = "http://127.0.0.1:9"   # nadie escucha
    bot.pimonitor("/api/latido")
    bot.evento("info", "x", "y")


if __name__ == "__main__":
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_"):
            f()
            print("OK", nombre)
