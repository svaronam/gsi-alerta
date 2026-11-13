#!/usr/bin/env python3
"""
GSI-ALERTA - Vigila la sede del INAP y avisa en cuanto publiquen las notas
del 2.º ejercicio de GSI (ingreso libre, convocatoria 2025), diciéndote si
estás o no en el listado de aprobados y con qué nota.

- Cada minuto descarga la página de la convocatoria y busca, bajo el título
  "Segundo ejercicio", los listados ("Acceso general" / "Cupo Norma Común 13").
- Cuando aparecen, descarga el PDF, busca tu nombre y genera el aviso.
- Cualquier PDF nuevo en la página (resolución, notas informativas...) también
  genera un aviso "Novedad".
- API HTTP para la APK (por Tailscale). Todas llevan X-API-Key:
    GET  /api/estado                    -> resumen
    GET  /api/eventos?desde=N&espera=S  -> avisos con id > N (long-poll hasta S s)
    POST /api/check                     -> barrida inmediata
    POST /api/prueba                    -> genera un aviso de prueba
    POST /api/simular?caso=aprobado|suspenso -> simulacro completo con PDF inventado
  GET /prueba/<clave>/<fichero>.pdf (sin clave, ruta aleatoria) -> PDF del simulacro
  GET /api/ping no necesita clave.

Uso:
    python gsialerta.py            # servidor (lo que ejecuta el servicio)
    python gsialerta.py --probar   # una barrida en seco, imprime lo que ve
"""
import json
import logging
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests

VERSION = "1.0"
BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
ESTADO = BASE / "estado.json"

URL_LIBRE = ("https://sede.inap.gob.es/es/procedimientos-y-servicios/seleccion/"
             "procesos-selectivos-de-cuerpos-y-escalas-generales/"
             "cuerpo-de-gestion-de-sistemas-e-informatica-de-la-administracion-"
             "del-estado-ingreso-libre-convocatoria-2025")

CONFIG_DEFECTO = {
    "nombre": "",            # "APELLIDOS, NOMBRE", tal como sale en los listados
    "url": URL_LIBRE,
    "puerto": 8806,          # homelab2 usa el 880x (8790 es de pi-agent)
    "api_key": "",
    "intervalo_segundos": 60,
    "aviso_caida_minutos": 20,
    "plazas_general": 634,   # Anexo IX: 680 plazas, 46 de cupo discapacidad
    "plazas_cupo": 46,
    # Pi Monitor: avisos y señal de vida a pi-agent (vacío = desactivado)
    "pi_agent": "http://127.0.0.1:8790",
    "pi_agent_bot": "gsialerta.service",
}
PRIO_PIMONITOR = {"resultado": 5, "publicado": 5, "prueba_resultado": 5, "error": 4, "novedad": 3,
                  "prueba": 3, "info": 2}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")
MAX_EVENTOS = 300
MIN_NIFS_PDF = 5  # menos NIFs que esto = el PDF no se ha leído bien

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gsialerta")

LOCK = threading.RLock()
PDFS_PRUEBA = {}   # clave aleatoria -> PDF de simulacro (solo en memoria)
NUEVO_EVENTO = threading.Condition(LOCK)
DESPERTAR = threading.Event()


# ---------------------------------------------------------------- utilidades
def cargar(ruta: Path, defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def guardar(ruta: Path, datos) -> None:
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ruta)


def norm(texto: str) -> str:
    """Mayúsculas, sin tildes (Ñ se conserva), espacios colapsados."""
    texto = (texto or "").replace("\xa0", " ").upper()
    texto = texto.replace("Ñ", "\0")
    texto = "".join(c for c in unicodedata.normalize("NFD", texto)
                    if unicodedata.category(c) != "Mn")
    texto = texto.replace("\0", "Ñ")
    texto = re.sub(r"(?<!\d)\s*,\s*(?!\d)", ", ", texto)  # no toca 28,45
    return re.sub(r"\s+", " ", texto).strip()


def hora(ts=None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%d/%m %H:%M:%S")


# ------------------------------------------------------------ página del INAP
class Enlaces(HTMLParser):
    """Saca todos los <a href> con el título <h1-h6> bajo el que están."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.seccion = ""
        self.enlaces = []          # dicts: seccion, texto, href
        self._en_h = False
        self._h_txt = []
        self._a = None

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r"h[1-6]", tag):
            self._en_h, self._h_txt = True, []
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._a = {"seccion": self.seccion, "href": urljoin(self.base, href),
                           "texto": []}

    def handle_endtag(self, tag):
        if re.fullmatch(r"h[1-6]", tag) and self._en_h:
            self._en_h = False
            self.seccion = " ".join("".join(self._h_txt).split())
        elif tag == "a" and self._a is not None:
            self._a["texto"] = " ".join("".join(self._a["texto"]).split())
            self.enlaces.append(self._a)
            self._a = None

    def handle_data(self, data):
        if self._en_h:
            self._h_txt.append(data)
        if self._a is not None:
            self._a["texto"].append(data)


def analizar_pagina(html: str, url: str) -> dict:
    p = Enlaces(url)
    p.feed(html)
    pdfs = [e for e in p.enlaces if urlparse(e["href"]).path.lower().endswith(".pdf")]

    def de_seccion(nombre):
        return [e for e in pdfs if norm(e["seccion"]) == nombre]

    def listados(enlaces, numero):
        """Listados de aprobados: por texto del enlace o por nombre del PDF."""
        res = {}
        for e in enlaces:
            t = norm(e["texto"])
            fichero = urlparse(e["href"]).path.rsplit("/", 1)[-1].upper()
            if "ACCESO GENERAL" in t or re.match(rf"{numero}-GSI-L.*SUP_GEN", fichero):
                res.setdefault("general", e)
            elif "CUPO" in t or re.match(rf"{numero}-GSI-L.*SUP_CRD", fichero):
                res.setdefault("cupo", e)
        return res

    segundo = de_seccion("SEGUNDO EJERCICIO")
    l2 = listados(segundo, 2)
    if "general" not in l2:
        # Por si cambian los títulos: el PDF del 2.º suele llamarse 2-GSI-L-...SUP_GEN...
        for e in pdfs:
            fichero = urlparse(e["href"]).path.rsplit("/", 1)[-1].upper()
            if re.match(r"2-GSI-L.*SUP_GEN", fichero):
                l2["general"] = e
                break
    return {
        "pdfs": pdfs,
        "segundo": segundo,
        "listados_2": l2,
        "listados_1": listados(de_seccion("PRIMER EJERCICIO"), 1),
        "hay_titulo_segundo": any(norm(e["seccion"]) == "SEGUNDO EJERCICIO"
                                  for e in p.enlaces),
    }


# -------------------------------------------------------------------- PDFs
def texto_pdf(datos: bytes) -> str:
    """pdftotext -layout (poppler-utils) si está; si no, pypdf."""
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(datos)
            f.flush()
            r = subprocess.run(["pdftotext", "-layout", f.name, "-"],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.decode("utf-8", "replace")
    try:
        import io
        from pypdf import PdfReader
        lector = PdfReader(io.BytesIO(datos))
        return "\n".join((p.extract_text() or "") for p in lector.pages)
    except ImportError:
        raise RuntimeError("Instala poppler-utils (pdftotext) o pypdf")


def buscar_en_listado(texto: str, nombre: str) -> dict:
    """{'legible', 'encontrado', 'nota', 'personas'} para un listado de aprobados."""
    t = norm(texto)
    personas = len(re.findall(r"\*{2,}\d{3,5}\*+", texto))
    n = norm(nombre)
    m = re.search(re.escape(n) + r"\s+(\d{1,3}(?:,\d{1,3})?)\b", t)
    if not m:  # pypdf a veces pone el NIF entre el nombre y la nota
        m = re.search(re.escape(n) + r"\s*\*+\d{3,5}\*+\s+(\d{1,3}(?:,\d{1,3})?)\b", t)
    encontrado = bool(m) or n in t
    if not encontrado:
        # por si el nombre viene partido en otra línea: basta con los apellidos
        apellidos = n.split(",")[0].strip()
        encontrado = bool(apellidos) and apellidos in t
    return {
        "legible": personas >= MIN_NIFS_PDF,
        "encontrado": encontrado,
        "nota": m.group(1) if m else None,
        "personas": personas,
    }


_NOTA = r"(\d{1,3}(?:,\d{1,3})?)"
_NIF = r"(\*{2,}\d{3,5}\*+)"


def parsear_listado(texto: str) -> dict:
    """Filas (nif, nombre, nota) de un listado de aprobados del INAP.

    pdftotext -layout da "NIF  NOMBRE  NOTA" (dos columnas por línea);
    pypdf suele dar "NOMBRE NIF NOTA". Se prueba el formato que más filas saque.
    """
    nifs = len(re.findall(r"\*{2,}\d{3,5}\*+", texto))
    a = [(m[1], m[2], m[3]) for m in
         re.finditer(_NIF + r"\s+([^\d*\n]+?)\s+" + _NOTA + r"(?=\s|$)", texto)]
    b = [(m[2], m[1], m[3]) for m in
         re.finditer(r"([A-ZÁÉÍÓÚÜÑÇ][^\d*\n]*?)\s*" + _NIF + r"\s+" + _NOTA + r"(?=\s|$)", texto)]
    filas = a if len(a) >= len(b) else b
    out = []
    for nif, nombre, nota in filas:
        try:
            out.append({"nif": nif, "nombre": norm(nombre),
                        "nota": float(nota.replace(",", "."))})
        except ValueError:
            pass
    return {"filas": out, "nifs": nifs, "completo": nifs > 0 and len(out) == nifs}


def _clave(f: dict) -> str:
    return f["nif"] + "|" + f["nombre"]


def fmt_nota(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def calcular_puesto(nombre: str, l1: list, l2_gen: list, l2_cupo: list,
                    plazas_general: int = 634, plazas_cupo: int = 46) -> dict:
    """Puesto final en el acceso general (Anexo IX y norma común 13.5/13.6).

    - Nota final = calificación 1.er ejercicio + calificación 2.º ejercicio.
    - Desempate: más nota en el 2.º; luego más nota en el 1.º (aciertos/errores
      del 1.º no se publican, así que esos empates quedan como "empatado").
    - Cupo de discapacidad: los primeros `plazas_cupo` se quedan su plaza; los
      que aprueben y no quepan en su cupo compiten en el general con su nota.
      Las plazas vacías del cupo NO pasan al general (ingreso libre).
    """
    yo = norm(nombre)
    nota1 = {_clave(f): f["nota"] for f in l1}
    nota1_nombre = {}
    for f in l1:
        nota1_nombre.setdefault(f["nombre"], []).append(f["nota"])

    def con_total(filas):
        res, sin_n1 = [], 0
        for f in filas:
            n1 = nota1.get(_clave(f))
            if n1 is None and len(nota1_nombre.get(f["nombre"], [])) == 1:
                n1 = nota1_nombre[f["nombre"]][0]
            if n1 is None:
                sin_n1 += 1          # p. ej. nota conservada de otra convocatoria
                continue
            res.append({**f, "n1": n1, "n2": f["nota"], "total": round(n1 + f["nota"], 2)})
        res.sort(key=lambda x: (-x["total"], -x["n2"], -x["n1"]))
        return res, sin_n1

    gen, sin_gen = con_total(l2_gen)
    cupo, sin_cupo = con_total(l2_cupo)
    cupo_sin_plaza = cupo[plazas_cupo:]
    ranking = sorted(gen + cupo_sin_plaza, key=lambda x: (-x["total"], -x["n2"], -x["n1"]))

    mio = next((i for i, x in enumerate(ranking) if x["nombre"] == yo), None)
    if mio is None:
        return {"calculado": False, "motivo": "no estás en el listado general del 2.º"}
    me = ranking[mio]
    empatados = sum(1 for x in ranking if x is not me and x["total"] == me["total"]
                    and x["n2"] == me["n2"] and x["n1"] == me["n1"])
    todos = sorted(gen + cupo, key=lambda x: (-x["total"], -x["n2"], -x["n1"]))
    return {
        "calculado": True,
        "nota1": me["n1"], "nota2": me["n2"], "total": me["total"],
        "puesto": mio + 1,
        "aprobados_general": len(ranking),
        "plazas_general": plazas_general,
        "dentro_de_plazas": mio + 1 <= plazas_general,
        "empatados": empatados,
        "puesto_global": next(i for i, x in enumerate(todos) if x is me) + 1,
        "aprobados_total": len(todos),
        "aprobados_cupo": len(cupo),
        "cupo_compitiendo_en_general": len(cupo_sin_plaza),
        "sin_nota1": sin_gen + sin_cupo,
        "nota_ultimo_general": ranking[-1]["total"] if ranking else None,
        "nota_puesto_plazas": (ranking[plazas_general - 1]["total"]
                               if len(ranking) >= plazas_general else None),
    }


def puesto_1er_ejercicio(nombre: str, l1_gen: list) -> dict:
    yo = norm(nombre)
    orden = sorted(l1_gen, key=lambda x: -x["nota"])
    for i, f in enumerate(orden):
        if f["nombre"] == yo:
            mejor = sum(1 for x in orden if x["nota"] > f["nota"])
            empate = sum(1 for x in orden if x["nota"] == f["nota"]) - 1
            return {"puesto": mejor + 1, "de": len(orden), "empatados": empate,
                    "nota": f["nota"]}
    return {}


def texto_puesto(p: dict) -> str:
    if not p or not p.get("calculado"):
        return ""
    s = (f"Nota final {fmt_nota(p['total'])} (1.º {fmt_nota(p['nota1'])} + "
         f"2.º {fmt_nota(p['nota2'])}) → puesto {p['puesto']} de "
         f"{p['aprobados_general']} en acceso general")
    if p["empatados"]:
        s += f" (empatado con {p['empatados']}; deciden aciertos/errores del 1.º)"
    s += (f". {p['plazas_general']} plazas: "
          + ("DENTRO." if p["dentro_de_plazas"] else "FUERA de plazas."))
    return s


# ------------------------------------------------------- simulacros (pruebas)
def _pdf_texto(s: str) -> bytes:
    s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return s.encode("cp1252", "replace")


def pdf_listado(titulo: str, filas: list, por_pagina: int = 100) -> bytes:
    """PDF de listado con el formato del INAP (dos columnas), sin dependencias.

    filas: dicts con nif, nombre, nota. Se ordenan alfabéticamente como el real.
    """
    filas = sorted(filas, key=lambda f: f["nombre"])
    paginas = []
    for p in range(0, max(len(filas), 1), por_pagina):
        trozo = filas[p:p + por_pagina]
        mitad = (len(trozo) + 1) // 2
        ops = ["BT", "/F1 8 Tf"]

        def txt(x, y, s):
            ops.append((f"1 0 0 1 {x} {y} Tm (", s, ") Tj"))

        y = 560
        txt(40, y, "MINISTERIO PARA LA TRANSFORMACIÓN DIGITAL Y DE LA FUNCIÓN PÚBLICA")
        txt(560, y, "COMISIÓN PERMANENTE DE SELECCIÓN")
        txt(40, y - 22, "PRUEBAS SELECTIVAS PARA INGRESO LIBRE EN EL CUERPO DE GESTIÓN DE SISTEMAS E "
                        "INFORMÁTICA DE LA ADMINISTRACIÓN DEL ESTADO  ·  DOCUMENTO DE PRUEBA DE GSI-ALERTA")
        txt(40, y - 34, titulo)
        txt(40, y - 46, "SISTEMA DE ACCESO: GENERAL")
        y -= 66
        for x in (40, 420):
            txt(x, y, "D.N.I.")
            txt(x + 70, y, "APELLIDOS Y NOMBRE")
            txt(x + 300, y, "PUNTUACIÓN")
        y -= 12
        for i in range(mitad):
            for col, j in enumerate((i, i + mitad)):
                if j >= len(trozo):
                    continue
                f, x = trozo[j], 40 + col * 380
                txt(x, y, f["nif"])
                txt(x + 70, y, f["nombre"])
                txt(x + 300, y, fmt_nota(f["nota"]))
            y -= 9
        ops.append("ET")
        cuerpo = b""
        for o in ops:
            if isinstance(o, tuple):
                antes, texto, despues = o
                cuerpo += antes.encode() + _pdf_texto(texto) + despues.encode() + b"\n"
            else:
                cuerpo += o.encode() + b"\n"
        paginas.append(cuerpo)

    objs = []  # (num, bytes)
    n_font = 3
    kids = []
    for k, cuerpo in enumerate(paginas):
        n_page, n_cont = 4 + 2 * k, 5 + 2 * k
        kids.append(f"{n_page} 0 R")
        objs.append((n_page, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
                             f"/Resources << /Font << /F1 {n_font} 0 R >> >> /Contents {n_cont} 0 R >>".encode()))
        objs.append((n_cont, b"<< /Length %d >>\nstream\n" % len(cuerpo) + cuerpo + b"endstream"))
    objs += [(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
             (2, f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()),
             (3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")]
    objs.sort()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num, data in objs:
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + data + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for num in range(1, len(objs) + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def simular_listado2(l1: list, nombre: str, apruebas: bool, semilla=None) -> list:
    """Listado ficticio del 2.º ejercicio a partir de los aprobados reales del 1.º."""
    import random
    rnd = random.Random(semilla)
    yo = norm(nombre)
    otros = [f for f in l1 if f["nombre"] != yo]
    if not otros:  # sin listado del 1.º leído: nombres inventados
        otros = [{"nif": f"***{rnd.randint(0, 9999):04d}**",
                  "nombre": f"PERSONA {i:03d}, PRUEBA", "nota": round(rnd.uniform(25, 41), 2)}
                 for i in range(500)]
    aprobados = rnd.sample(otros, k=int(len(otros) * 0.62))
    filas = [{**f, "nota": round(rnd.uniform(25, 45), 2)} for f in aprobados]
    if apruebas:
        mio = next((f for f in l1 if f["nombre"] == yo),
                   {"nif": "***0000**", "nombre": yo, "nota": 28.45})
        filas.append({**mio, "nota": round(rnd.uniform(30, 38), 2)})
    return filas


# ---------------------------------------------------------------- el bot
class Bot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": UA,
            "Accept-Language": "es-ES,es;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        e = cargar(ESTADO, {})
        e.setdefault("eventos", [])
        e.setdefault("ultimo_id", 0)
        e.setdefault("vistos", [])
        e.setdefault("barridas", 0)
        e.setdefault("errores_seguidos", 0)
        e.setdefault("resultado", None)
        self.estado = e

    # -- eventos
    def evento(self, tipo: str, titulo: str, texto: str, url: str = "") -> None:
        with NUEVO_EVENTO:
            self.estado["ultimo_id"] += 1
            self.estado["eventos"].append({
                "id": self.estado["ultimo_id"], "ts": time.time(), "tipo": tipo,
                "titulo": titulo, "texto": texto, "url": url,
            })
            self.estado["eventos"] = self.estado["eventos"][-MAX_EVENTOS:]
            guardar(ESTADO, self.estado)
            NUEVO_EVENTO.notify_all()
        log.info("AVISO [%s] %s - %s", tipo, titulo, texto)
        msg = texto + (f"\n{url}" if url and tipo != "error" else "")
        threading.Thread(target=self.pimonitor, daemon=True, args=(
            "/api/evento", {"titulo": titulo, "msg": msg,
                            "prio": PRIO_PIMONITOR.get(tipo, 3), "cat": "opos"})).start()

    def eventos_desde(self, desde: int) -> list:
        return [e for e in self.estado["eventos"] if e["id"] > desde]

    # -- Pi Monitor (pi-agent en la misma Pi; sin token desde localhost)
    def pimonitor(self, ruta: str, datos: dict | None = None) -> None:
        base = (self.cfg.get("pi_agent") or "").rstrip("/")
        if not base:
            return
        datos = dict(datos or {}, bot=self.cfg.get("pi_agent_bot", "gsialerta.service"))
        try:
            r = requests.post(base + ruta, json=datos, timeout=5)
            if r.status_code >= 400:
                log.debug("pi-agent %s -> HTTP %s %s", ruta, r.status_code, r.text[:120])
        except requests.RequestException as ex:
            log.debug("pi-agent no responde (%s): %s", ruta, ex)

    # -- red
    def descargar(self, url: str, binario=False):
        params = None if binario else {"_": int(time.time())}  # salta cachés
        r = self.http.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.content if binario else r.text

    def leer_listado(self, enlace: dict) -> dict:
        datos = self.descargar(enlace["href"], binario=True)
        if not datos.startswith(b"%PDF"):
            raise RuntimeError("lo descargado no es un PDF")
        texto = texto_pdf(datos)
        res = buscar_en_listado(texto, self.cfg["nombre"])
        res["url"] = enlace["href"]
        res["listado"] = parsear_listado(texto)
        return res

    # -- barrida
    def barrer(self) -> dict:
        with LOCK:
            return self._barrer()

    def _barrer(self) -> dict:
        e = self.estado
        html = self.descargar(self.cfg["url"])
        pag = analizar_pagina(html, self.cfg["url"])
        if not pag["pdfs"] or not pag["hay_titulo_segundo"]:
            raise RuntimeError("la página no tiene la estructura esperada "
                               "(¿mantenimiento o la han cambiado?)")

        e["barridas"] += 1
        e["ultima_barrida"] = time.time()
        e["errores_seguidos"] = 0
        if e.get("aviso_caida"):
            e["aviso_caida"] = False
            self.evento("info", "La web del INAP vuelve a responder",
                        "Sigo vigilando cada minuto.")

        # PDFs nuevos en la página
        vistos = set(e["vistos"])
        listados2 = {x["href"] for x in pag["listados_2"].values()}
        primera_vez = not e.get("inicializado")
        for p in pag["pdfs"]:
            if p["href"] in vistos:
                continue
            vistos.add(p["href"])
            if not primera_vez and p["href"] not in listados2:
                donde = f" ({p['seccion']})" if p["seccion"] else ""
                self.evento("novedad", "Novedad en la web de GSI libre",
                            f"{p['texto'] or 'Nuevo PDF'}{donde}", p["href"])
        e["vistos"] = sorted(vistos)
        e["inicializado"] = True
        e["segundo_ejercicio"] = [x["texto"] for x in pag["segundo"]]

        # Listados del 1.er ejercicio (una vez): tu nota, tu puesto y las notas de
        # todos, que hacen falta para calcular el puesto final. Sirve además de
        # prueba de que la Pi lee bien los PDF.
        if not e.get("l1_ok") and "general" in pag["listados_1"]:
            try:
                self._leer_primero(pag["listados_1"])
            except Exception as ex:
                log.warning("No pude leer los listados del 1.er ejercicio: %s", ex)

        # ¿Ya están las notas del 2.º?
        res = e.get("resultado") or {}
        l2 = pag["listados_2"]
        if l2 and not res.get("veredicto"):
            if not res.get("publicado"):
                res = {"publicado": time.time()}
                e["resultado"] = res
            self._resolver(l2, res)

        guardar(ESTADO, e)
        return pag

    def _leer_primero(self, l1: dict) -> None:
        e = self.estado
        r1 = self.leer_listado(l1["general"])
        if not (r1["legible"] and r1["listado"]["filas"]):
            raise RuntimeError("listado general del 1.º ilegible")
        if r1["encontrado"]:
            e["nota1"] = r1["nota"] or "sí"
        e["puesto1"] = puesto_1er_ejercicio(self.cfg["nombre"], r1["listado"]["filas"])
        filas = list(r1["listado"]["filas"])
        completo = r1["listado"]["completo"]
        if "cupo" in l1:
            try:
                rc = self.leer_listado(l1["cupo"])
                filas += rc["listado"]["filas"]
                completo = completo and rc["listado"]["completo"]
            except Exception as ex:
                log.warning("No pude leer el cupo del 1.º (solo afecta al cupo): %s", ex)
                completo = False
        e["l1"] = filas
        e["l1_completo"] = completo
        e["l1_ok"] = True
        log.info("1.er ejercicio: nota %s, puesto %s; %d notas guardadas%s",
                 e.get("nota1"), e["puesto1"].get("puesto"), len(filas),
                 "" if completo else " (faltan filas)")

    def _puesto(self, lecturas: dict) -> dict:
        e = self.estado
        gen = lecturas.get("general")
        if not e.get("l1") or not gen or not gen["listado"]["filas"]:
            return {"calculado": False, "motivo": "faltan listados"}
        cupo = lecturas.get("cupo")
        p = calcular_puesto(
            self.cfg["nombre"], e["l1"], gen["listado"]["filas"],
            cupo["listado"]["filas"] if cupo else [],
            int(self.cfg.get("plazas_general", 634)), int(self.cfg.get("plazas_cupo", 46)))
        p["aviso"] = [m for m, falta in (
            ("no he podido leer el listado del cupo", cupo is None),
            ("faltan filas en algún listado", not (e.get("l1_completo")
                                                   and gen["listado"]["completo"]
                                                   and (cupo is None or cupo["listado"]["completo"]))),
            (f"{p.get('sin_nota1', 0)} aprobados sin nota del 1.º", p.get("sin_nota1")),
        ) if falta]
        return p

    def _resolver(self, l2: dict, res: dict) -> None:
        """Lee los listados del 2.º y da el veredicto (o lo reintenta)."""
        lecturas = {}
        for tipo in ("general", "cupo"):
            if tipo not in l2:
                continue
            try:
                lecturas[tipo] = self.leer_listado(l2[tipo])
            except Exception as ex:
                log.warning("No pude leer el listado %s: %s", tipo, ex)

        url_general = l2.get("general", l2.get("cupo"))["href"]
        dentro = next((t for t, r in lecturas.items()
                       if r["legible"] and r["encontrado"]), None)
        general = lecturas.get("general")

        if dentro:
            r = lecturas[dentro]
            nota = r["nota"] or "?"
            extra = "" if dentro == "general" else " (listado del cupo)"
            try:
                puesto = self._puesto(lecturas)
            except Exception as ex:
                log.exception("No pude calcular el puesto")
                puesto = {"calculado": False, "motivo": str(ex)}
            res.update(veredicto="aprobado", nota=r["nota"], url=r["url"],
                       personas=r["personas"], ts=time.time(), puesto=puesto)
            detalle = texto_puesto(puesto)
            if detalle and puesto.get("aviso"):
                detalle += " Ojo: " + "; ".join(puesto["aviso"]) + "."
            self.evento("resultado", "✅ ¡APRUEBAS EL 2.º EJERCICIO!",
                        f"2.º ejercicio: {nota}{extra}. "
                        + (detalle or f"Aprueban {r['personas']} personas."), r["url"])
        elif general and general["legible"]:
            res.update(veredicto="no", nota=None, url=general["url"],
                       personas=general["personas"], ts=time.time())
            self.evento("resultado", "❌ No apareces en el listado del 2.º ejercicio",
                        f"Han publicado los aprobados del 2.º ejercicio "
                        f"({general['personas']} personas) y no figuras. "
                        "Abre el PDF para comprobarlo.", general["url"])
        elif not res.get("avisado_publicado"):
            # Está publicado pero aún no he podido leerlo: aviso igualmente
            res["avisado_publicado"] = True
            self.evento("publicado", "📢 ¡Han publicado las notas del 2.º ejercicio!",
                        "Aún no he podido leer el PDF; lo reintento cada minuto. "
                        "Toca para abrirlo.", url_general)

    # -- simulacros: misma cadena que el aviso real, con un PDF inventado
    def simular(self, caso: str, base_url: str) -> dict:
        """caso = 'aprobado' | 'suspenso'. No toca el resultado real."""
        apruebas = caso == "aprobado"
        with LOCK:
            l1 = list(self.estado.get("l1") or [])
        filas = simular_listado2(l1, self.cfg["nombre"], apruebas)
        pdf = pdf_listado("RELACIÓN NOMINATIVA DE ASPIRANTES QUE SUPERAN EL SEGUNDO EJERCICIO "
                          "(SIMULACRO)", filas)
        clave = secrets.token_urlsafe(12)
        with LOCK:
            PDFS_PRUEBA[clave] = pdf
            while len(PDFS_PRUEBA) > 10:
                PDFS_PRUEBA.pop(next(iter(PDFS_PRUEBA)))
        url = f"{base_url}/prueba/{clave}/2-GSI-L-SUP_GEN_SIMULACRO.pdf"

        # lo mismo que con el PDF real: texto -> veredicto -> filas -> puesto
        texto = texto_pdf(pdf)
        r = buscar_en_listado(texto, self.cfg["nombre"])
        listado = parsear_listado(texto)
        if r["legible"] and r["encontrado"]:
            puesto = (calcular_puesto(self.cfg["nombre"], l1, listado["filas"], [],
                                      int(self.cfg.get("plazas_general", 634)),
                                      int(self.cfg.get("plazas_cupo", 46)))
                      if l1 else {"calculado": False})
            detalle = texto_puesto(puesto) or f"Aprueban {r['personas']} personas."
            if not l1:
                detalle += " (Sin listado del 1.º leído: el puesto no se puede calcular.)"
            self.evento("prueba_resultado", "🧪 PRUEBA · ✅ ¡APRUEBAS EL 2.º EJERCICIO!",
                        f"2.º ejercicio: {r['nota'] or '?'}. {detalle}", url)
            return {"caso": caso, "nota": r["nota"], "puesto": puesto, "url": url}
        self.evento("prueba_resultado", "🧪 PRUEBA · ❌ No apareces en el listado del 2.º ejercicio",
                    f"Han publicado los aprobados del 2.º ejercicio ({r['personas']} personas) "
                    "y no figuras. Abre el PDF para comprobarlo.", url)
        return {"caso": caso, "personas": r["personas"], "url": url}

    # -- vista para la APK
    def vista(self) -> dict:
        with LOCK:
            e = self.estado
            return {
                "version": VERSION,
                "nombre": self.cfg["nombre"],
                "url": self.cfg["url"],
                "ultima_barrida": e.get("ultima_barrida"),
                "barridas": e["barridas"],
                "errores_seguidos": e["errores_seguidos"],
                "ultimo_error": e.get("ultimo_error"),
                "intervalo_segundos": self.intervalo(),
                "nota1": e.get("nota1"),
                "puesto1": e.get("puesto1"),
                "segundo_ejercicio": e.get("segundo_ejercicio", []),
                "resultado": e.get("resultado"),
                "ultimo_evento": e["ultimo_id"],
            }

    def intervalo(self) -> int:
        return max(30, int(self.cfg.get("intervalo_segundos", 60)))

    def bucle(self) -> None:
        while True:
            try:
                self.barrer()
                log.debug("Barrida %d OK", self.estado["barridas"])
                self.pimonitor("/api/latido")
            except Exception as ex:
                with LOCK:
                    e = self.estado
                    if not e["errores_seguidos"]:
                        e["desde_error"] = time.time()
                    e["errores_seguidos"] += 1
                    e["ultimo_error"] = f"{hora()} {ex}"
                    minutos = (time.time() - e["desde_error"]) / 60
                    log.warning("Barrida fallida (%d seguidas): %s",
                                e["errores_seguidos"], ex)
                    if (minutos >= self.cfg.get("aviso_caida_minutos", 20)
                            and not e.get("aviso_caida")):
                        e["aviso_caida"] = True
                        self.evento("error", "⚠️ No puedo leer la web del INAP",
                                    f"Lleva {int(minutos)} min fallando: {ex}",
                                    self.cfg["url"])
                    guardar(ESTADO, e)
            DESPERTAR.wait(self.intervalo())
            DESPERTAR.clear()


# ---------------------------------------------------------------- servidor HTTP
def crear_handler(bot: Bot, api_key: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"GSI-ALERTA/{VERSION}"

        def log_message(self, fmt, *args):
            log.debug("HTTP %s - %s", self.client_address[0], fmt % args)

        def _json(self, codigo: int, datos) -> None:
            cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def _autorizado(self) -> bool:
            if secrets.compare_digest(self.headers.get("X-API-Key", ""), api_key):
                return True
            self._json(401, {"error": "api key incorrecta"})
            return False

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path == "/api/ping":
                return self._json(200, {"ok": True, "app": "gsi-alerta"})
            m = re.fullmatch(r"/prueba/([\w-]+)/[\w.-]+\.pdf", url.path)
            if m:  # sin clave: lo abre el navegador del móvil; la ruta es aleatoria
                with LOCK:
                    pdf = PDFS_PRUEBA.get(m.group(1))
                if pdf is None:
                    return self._json(404, {"error": "esa prueba ya no existe; lanza otra"})
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                 'attachment; filename="2-GSI-L-SUP_GEN_SIMULACRO.pdf"')
                self.send_header("Content-Length", str(len(pdf)))
                self.end_headers()
                self.wfile.write(pdf)
                return
            if not self._autorizado():
                return
            if url.path == "/api/estado":
                return self._json(200, bot.vista())
            if url.path == "/api/eventos":
                try:
                    desde = int(q.get("desde", ["0"])[0])
                    espera = min(60, max(0, int(q.get("espera", ["0"])[0])))
                except ValueError:
                    desde, espera = 0, 0
                with NUEVO_EVENTO:
                    if espera and not bot.eventos_desde(desde):
                        NUEVO_EVENTO.wait_for(lambda: bot.eventos_desde(desde), espera)
                    return self._json(200, {"eventos": bot.eventos_desde(desde),
                                            "ultimo_id": bot.estado["ultimo_id"],
                                            "ultima_barrida": bot.estado.get("ultima_barrida")})
            self._json(404, {"error": "no existe"})

        def do_POST(self):
            url = urlparse(self.path)
            if not self._autorizado():
                return
            if url.path == "/api/check":
                try:
                    bot.barrer()
                except Exception as ex:
                    return self._json(502, {"error": f"No pude leer la web: {ex}"})
                return self._json(200, bot.vista())
            if url.path == "/api/simular":
                caso = parse_qs(url.query).get("caso", [""])[0]
                if caso not in ("aprobado", "suspenso"):
                    return self._json(400, {"error": "caso = aprobado | suspenso"})
                host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
                try:
                    return self._json(200, bot.simular(caso, f"http://{host}"))
                except Exception as ex:
                    log.exception("Simulacro fallido")
                    return self._json(500, {"error": f"El simulacro ha fallado: {ex}"})
            if url.path == "/api/prueba":
                bot.evento("prueba", "🔔 Prueba de GSI-ALERTA",
                           "Si ves esto, las notificaciones funcionan.", bot.cfg["url"])
                return self._json(200, {"ok": True})
            self._json(404, {"error": "no existe"})

    return Handler


def cargar_config() -> dict:
    cfg = dict(CONFIG_DEFECTO)
    cfg.update(cargar(CONFIG, {}))
    if not cfg.get("nombre"):
        raise SystemExit('Pon tu nombre en config.json: "nombre": "APELLIDOS, NOMBRE"')
    if not cfg.get("api_key"):
        cfg["api_key"] = secrets.token_urlsafe(24)
        guardar(CONFIG, cfg)
        log.info("Generada api_key nueva en config.json: %s", cfg["api_key"])
    return cfg


def probar(cfg: dict) -> None:
    """Una barrida sin tocar estado.json, para comprobar la instalación."""
    global ESTADO
    ESTADO = Path(tempfile.gettempdir()) / "gsialerta-prueba.json"
    ESTADO.unlink(missing_ok=True)
    bot = Bot(dict(cfg, pi_agent=""))   # la prueba no manda avisos a Pi Monitor
    pag = bot.barrer()
    print(f"\nPágina OK. {len(pag['pdfs'])} PDFs.")
    print("Segundo ejercicio:", [x["texto"] for x in pag["segundo"]])
    print("Nota 1.er ejercicio leída del PDF:", bot.estado.get("nota1"),
          "| puesto:", bot.estado.get("puesto1"),
          "| notas guardadas:", len(bot.estado.get("l1", [])))
    print("Resultado 2.º ejercicio:", bot.estado.get("resultado") or "aún no publicado")
    ESTADO.unlink(missing_ok=True)


def main() -> None:
    cfg = cargar_config()
    if "--probar" in sys.argv:
        return probar(cfg)

    bot = Bot(cfg)
    puerto = int(cfg.get("puerto", 8806))
    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), crear_handler(bot, cfg["api_key"]))
    servidor.daemon_threads = True
    threading.Thread(target=bot.bucle, daemon=True).start()
    log.info("GSI-ALERTA %s en el puerto %d; barrida cada %d s; buscando a %s",
             VERSION, puerto, bot.intervalo(), cfg["nombre"])
    servidor.serve_forever()


if __name__ == "__main__":
    main()
