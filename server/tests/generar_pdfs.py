"""Genera PDFs de listados con el mismo formato que los del INAP, con nombres inventados.

Los PDF reales llevan datos de otras personas, así que no van al repositorio
(tests/fixtures/reales/ está en .gitignore). Estos sirven para las pruebas.

    python tests/generar_pdfs.py
"""
import random
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

AQUI = Path(__file__).resolve().parent / "fixtures"
NOMBRES = ["ANA", "LUIS", "MARIA JOSE", "JAVIER", "LUCIA", "PABLO", "ELENA", "JUAN CARLOS",
           "CARMEN", "DAVID", "IRENE", "SERGIO", "NURIA", "ALVARO", "BEATRIZ", "IÑIGO"]
APELLIDOS = ["GARCIA", "LOPEZ", "MARTIN", "SANCHEZ", "PEREZ", "GOMEZ", "RUIZ", "DIAZ",
             "MORENO", "MUÑOZ", "ALONSO", "ROMERO", "NAVARRO", "TORRES", "DOMINGUEZ", "GIL",
             "VAZQUEZ", "SERRANO", "BLANCO", "SUAREZ", "MOLINA", "CASTRO", "ORTIZ", "RUBIO"]


def nota_txt(x):
    s = f"{x:.2f}".replace(".", ",")
    return s.rstrip("0").rstrip(",") if s.endswith("0") else s


def personas(n, semilla, fijas):
    rnd = random.Random(semilla)
    vistos, filas = set(), list(fijas)
    vistos.update(f[1] for f in fijas)
    while len(filas) < n:
        nombre = f"{rnd.choice(APELLIDOS)} {rnd.choice(APELLIDOS)}, {rnd.choice(NOMBRES)}"
        if nombre in vistos:
            continue
        vistos.add(nombre)
        filas.append((f"***{rnd.randint(0, 9999):04d}**", nombre, round(rnd.uniform(25, 41), 2)))
    return sorted(filas, key=lambda f: f[1])


def pdf(ruta, titulo, filas):
    c = canvas.Canvas(str(ruta), pagesize=landscape(A4))
    ancho, alto = landscape(A4)
    por_pagina = 50
    for p in range(0, len(filas), por_pagina):
        y = alto - 50
        c.setFont("Helvetica", 9)
        c.drawString(40, y, "MINISTERIO PARA LA TRANSFORMACION DIGITAL Y DE LA FUNCION PUBLICA")
        c.drawString(560, y, "COMISION PERMANENTE DE SELECCION")
        y -= 25
        c.drawString(40, y, titulo)
        y -= 14
        c.drawString(40, y, "SISTEMA DE ACCESO: GENERAL")
        y -= 22
        for x in (40, 420):
            c.drawString(x, y, "D.N.I.")
            c.drawString(x + 70, y, "APELLIDOS Y NOMBRE")
            c.drawString(x + 290, y, "PUNTUACION")
        y -= 16
        trozo = filas[p:p + por_pagina]
        mitad = (len(trozo) + 1) // 2
        for i in range(mitad):
            for col, f in enumerate((trozo[i], trozo[i + mitad] if i + mitad < len(trozo) else None)):
                if f is None:
                    continue
                x = 40 + col * 380
                c.drawString(x, y, f[0])
                c.drawString(x + 70, y, f[1])
                c.drawRightString(x + 340, y, nota_txt(f[2]))
            y -= 9
        c.showPage()
    c.save()


def main():
    yo = ("***1234**", "OPOSITORA PRUEBA, ANA", 28.45)
    l1 = personas(520, 1, [yo, ("***5555**", "TORRES RUBIO, PABLO", 25.0)])
    pdf(AQUI / "1-GSI-L-DM-SUP_GEN.pdf",
        "RELACION NOMINATIVA DE ASPIRANTES QUE SUPERAN EL PRIMER EJERCICIO", l1)
    l2 = personas(64, 2, [("***3684**", "LANZA HERRERO, JUAN JOSE", 40.08),
                          ("***2462**", "GARCIA MERCADO, JAVIER", 26.0)])
    pdf(AQUI / "2-GSI-P-SUP_GEN.pdf",
        "RELACION NOMINATIVA DE ASPIRANTES QUE SUPERAN EL SEGUNDO EJERCICIO", l2)


if __name__ == "__main__":
    main()
