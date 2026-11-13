"""Pruebas con los PDF reales del INAP. Solo en local: tests/fixtures/reales/ no se sube
al repositorio porque los listados llevan datos de otras personas.

Pon ahí 1-GSI-L-DM-SUP_GEN.pdf (1.er ejercicio, libre) y 2-GSI-P-SUP_GEN.pdf
(2.º ejercicio, promoción interna) descargados de la sede del INAP.
"""
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REALES = AQUI / "fixtures" / "reales"
sys.path.insert(0, str(AQUI.parent))

import gsialerta as g  # noqa: E402

# tu nombre sale de tu config.json local (no se sube al repositorio)
NOMBRE = g.cargar(AQUI.parent / "config.json", {}).get("nombre", "")


def _texto(nombre):
    ruta = REALES / nombre
    if not ruta.exists():
        return None
    return g.texto_pdf(ruta.read_bytes())


def test_primer_ejercicio_libre():
    t = _texto("1-GSI-L-DM-SUP_GEN.pdf")
    if t is None:
        print("  (sin PDF real, se salta)")
        return
    p = g.parsear_listado(t)
    assert p["completo"] and len(p["filas"]) == 789
    if NOMBRE:
        r = g.buscar_en_listado(t, NOMBRE)
        assert r["encontrado"] and r["nota"]
        print("   tu nota:", r["nota"], g.puesto_1er_ejercicio(NOMBRE, p["filas"]))


def test_segundo_ejercicio_interna():
    t = _texto("2-GSI-P-SUP_GEN.pdf")
    if t is None:
        print("  (sin PDF real, se salta)")
        return
    p = g.parsear_listado(t)
    assert p["completo"] and len(p["filas"]) == 64
    primero = p["filas"][0]
    assert g.buscar_en_listado(t, primero["nombre"])["nota"] == g.fmt_nota(primero["nota"]).rstrip("0").rstrip(",") \
        or g.buscar_en_listado(t, primero["nombre"])["nota"] == g.fmt_nota(primero["nota"])


if __name__ == "__main__":
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_"):
            f()
            print("OK", nombre)
