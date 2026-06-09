#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filter_candidates.py — Filtro previo del pool antes de la limpieza.
Etapa 3/8 del pipeline.

Qué hace  : descarta veterinaria (RIVEP), artículos con cuerpo en inglés y los sin
            estructura aprovechable; copia los seleccionados y escribe un reporte con
            la decisión por archivo. No borra nada (solo copia).
Entrada   : --textos (crudos), --catalogo (descripcion_textos.txt), --csv (candidatos.csv).
Salida    : --out/ (.txt seleccionados) + filtro_reporte.csv.
Requisitos: Python 3.9+ (solo biblioteca estándar).
Uso       : python 03_filter_candidates.py --textos ./data/textos --csv ./data/candidatos.csv --out ./data/seleccionados
Argumentos: --textos, --catalogo, --csv, --out, --solo-alta

--- Documentación original del autor ---
FILTRO PREVIO — depura el pool antes de la limpieza
===================================================

Usa el catalogo (descripcion_textos.txt) y candidatos.csv para:
  * Descartar VETERINARIA (RIVEP, ISSN 1609-9117): no es clinica humana.
  * Descartar articulos con CUERPO EN INGLES (bilingues): no sirven para
    validar NER en español; solo tienen el resumen en español.
  * Marcar para REVISION MANUAL los casos sin estructura aprovechable.
  * Producir una lista corta priorizada (relevancia alta, español, con
    estructura util) y copiar esos .txt a una carpeta 'seleccionados'.

No borra nada: solo COPIA los seleccionados a otra carpeta y escribe un
reporte con la decision por archivo (para que la revises/justifiques en la tesis).

Uso:
    python filtro_previo.py \
        --textos ./data/textos \
        --catalogo ./data/analisis_textos/descripcion_textos.txt \
        --csv ./data/candidatos.csv \
        --out ./data/seleccionados
"""

import argparse
import csv
import os
import re
import shutil
import unicodedata

ISSN_VETERINARIA = "1609-9117"   # RIVEP

# Palabras de cuerpo en ingles (encabezados de seccion sajones en el texto)
EN_BODY_MARKERS = [
    "case report", "case presentation", "introduction", "discussion",
    "materials and methods", "results", "background",
]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def parse_catalog(path):
    """Devuelve dict pid -> {titulo, tipo, estructura}."""
    recs = {}
    if not os.path.exists(path):
        return recs
    txt = open(path, encoding="utf-8", errors="replace").read()
    fichas = re.split(r"\n\[\d{3}\]\s+", txt)[1:]
    for f in fichas:
        name = f.split("\n", 1)[0].strip()
        pid = name[:-4] if name.endswith(".txt") else name

        def field(n):
            m = re.search(rf"{n}\s*:\s*(.*)", f)
            return m.group(1).strip() if m else ""
        recs[pid] = {
            "titulo": field("Título"),
            "tipo": field("Tipo"),
            "estructura": field("Estructura"),
        }
    return recs


def body_is_english(textos_dir, pid, titulo):
    """Heuristica: el titulo esta en ingles (sin tildes + palabras EN) o el
    cuerpo abre con encabezados sajones."""
    nt = norm(titulo)
    title_en = (bool(re.search(r"\b(of|the|with|and|case|report|primary|syndrome|tumor)\b", nt))
                and not re.search(r"[áéíóúñ]", titulo or ""))
    # confirmar con el cuerpo
    p = os.path.join(textos_dir, pid + ".txt")
    body_en = False
    if os.path.exists(p):
        head = norm(open(p, encoding="utf-8", errors="ignore").read()[:4000])
        hits = sum(1 for m in EN_BODY_MARKERS if re.search(rf"^\s*{re.escape(m)}\b", head, re.MULTILINE))
        # tiene 'case report'/'discussion' en ingles pero NO 'discusion' en español
        body_en = hits >= 2 and "discusion" not in head
    return title_en or body_en


def has_useful_structure(estructura):
    """Tiene narrativa clinica aprovechable: Introduccion y/o Caso clinico."""
    e = estructura or ""
    return ("Introducción" in e) or ("Caso clínico" in e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--textos", default="./data/textos")
    ap.add_argument("--catalogo", default="./data/analisis_textos/descripcion_textos.txt")
    ap.add_argument("--csv", default="./data/candidatos.csv")
    ap.add_argument("--out", default="./data/seleccionados")
    ap.add_argument("--solo-alta", action="store_true",
                    help="Quedarse solo con relevancia 'alta'.")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cat = parse_catalog(args.catalogo)

    meta = {}
    if os.path.exists(args.csv):
        for r in csv.DictReader(open(args.csv, encoding="utf-8")):
            meta[r.get("pid")] = r

    archivos = [f[:-4] for f in os.listdir(args.textos) if f.endswith(".txt")]
    reporte = []
    seleccionados = 0
    contad = {"veterinaria": 0, "ingles": 0, "sin_estructura": 0,
              "baja_relevancia": 0, "seleccionado": 0, "revisar": 0}

    for pid in sorted(archivos):
        c = cat.get(pid, {})
        m = meta.get(pid, {})
        rel = m.get("relevancia", "")
        titulo = c.get("titulo", "") or m.get("titulo", "")
        estructura = c.get("estructura", "")
        issn = pid[1:10]

        decision, motivo = "SELECCIONADO", ""

        if issn == ISSN_VETERINARIA:
            decision, motivo = "DESCARTADO", "veterinaria (RIVEP)"
            contad["veterinaria"] += 1
        elif body_is_english(args.textos, pid, titulo):
            decision, motivo = "DESCARTADO", "cuerpo en ingles (bilingue)"
            contad["ingles"] += 1
        elif not has_useful_structure(estructura):
            decision, motivo = "REVISAR", "sin Introduccion/Caso clinico rotulado"
            contad["sin_estructura"] += 1
            contad["revisar"] += 1
        elif args.solo_alta and rel != "alta":
            decision, motivo = "DESCARTADO", "relevancia no alta"
            contad["baja_relevancia"] += 1
        else:
            contad["seleccionado"] += 1

        if decision == "SELECCIONADO":
            shutil.copy2(os.path.join(args.textos, pid + ".txt"),
                         os.path.join(args.out, pid + ".txt"))
            seleccionados += 1

        reporte.append({
            "pid": pid, "decision": decision, "motivo": motivo,
            "relevancia": rel, "issn": issn,
            "titulo": titulo[:80], "estructura": estructura[:90],
        })

    with open(os.path.join(args.out, "filtro_reporte.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(reporte[0].keys()))
        w.writeheader()
        w.writerows(reporte)

    print("=" * 60)
    print("FILTRO PREVIO — RESUMEN")
    print("=" * 60)
    print(f"  Archivos analizados ............ {len(archivos)}")
    print(f"  SELECCIONADOS (copiados) ....... {seleccionados}")
    print(f"  A REVISAR a mano ............... {contad['revisar']}")
    print(f"  Descartados veterinaria ........ {contad['veterinaria']}")
    print(f"  Descartados ingles ............. {contad['ingles']}")
    if args.solo_alta:
        print(f"  Descartados baja relevancia .... {contad['baja_relevancia']}")
    print(f"\n  Seleccionados en: {os.path.abspath(args.out)}")
    print(f"  Reporte: {os.path.join(os.path.abspath(args.out), 'filtro_reporte.csv')}")
    print("\n  Siguiente: corre limpieza_corpus.py sobre la carpeta 'seleccionados'")
    print("  (modo NORMAL, no --solo-caso, por la estructura de este corpus).")


if __name__ == "__main__":
    main()
