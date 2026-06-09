#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sample_validation_set.py — Muestreo estratificado del set de validación.
Etapa 5/8 del pipeline.

Qué hace  : muestreo ESTRATIFICADO POR REVISTA (proporcional + mayor resto, con
            semilla fija -> reproducible) sobre los textos limpios con relevancia
            alta y un mínimo de palabras.
Entrada   : --candidatos candidatos.csv, --textos textos_limpios_final.
Salida    : --out/seleccion_validacion.csv (manifiesto), reserva.csv y textos/ copiados.
Requisitos: Python 3.9+ (solo biblioteca estándar).
Uso       : python 05_sample_validation_set.py --candidatos ./data/candidatos.csv --textos ./data/textos_limpios_final --n 120 --seed 42 --out ./data/set_validacion
Argumentos: --candidatos, --textos, --n, --seed, --min-palabras, --incluir-media, --out

--- Documentación original del autor ---
SELECCION DEL SET DE VALIDACION (v2) — adaptado al flujo nuevo
==============================================================
Igual que la version original (muestreo ESTRATIFICADO POR REVISTA, asignacion
proporcional + mayor resto, SEMILLA FIJA -> reproducible), pero adaptado para
el pipeline nuevo:

  - La RELEVANCIA se lee de candidatos.csv (no del reporte de limpieza).
  - Las PALABRAS se cuentan directamente de los .txt YA LIMPIOS
    (la carpeta textos_limpios_final que genera limpieza_opcionB.py).
  - El pool elegible = textos presentes en la carpeta limpia, con relevancia
    'alta' (o 'media' si se pide) y minimo de palabras.

Entradas:
  --candidatos  candidatos.csv         (pid, titulo, relevancia)
  --textos      textos_limpios_final   (carpeta con los .txt limpios)
Salidas (en --out):
  seleccion_validacion.csv  -> manifiesto de los N elegidos (anexo de tesis)
  reserva.csv               -> el resto del pool (colchon)
  textos/                   -> copia de los .txt seleccionados

Uso:
    py seleccionar_validacion_v2.py --candidatos ./data/candidatos.csv --textos ./data/textos_limpios_final --n 120 --seed 42 --out ./data/set_validacion
"""

import argparse
import csv
import os
import random
import shutil
from collections import Counter, defaultdict

JOURNALS = {
    "0034-8597": "Rev. Neuro-Psiquiatría",
    "1018-130X": "Rev. Médica Herediana",
    "1019-4355": "Rev. Estomatológica Herediana",
    "1022-5129": "Rev. Gastroenterología del Perú",
    "1025-5583": "Anales Fac. Medicina (UNMSM)",
    "1609-9117": "RIVEP (veterinaria)",
    "1726-4634": "Rev. Peruana Med. Exp. Salud Pública",
    "1727-558X": "Horizonte Médico (Lima)",
    "1728-5917": "Acta Médica Peruana",
    "2227-4731": "Rev. Cuerpo Médico HNAAA",
    "2304-5132": "Rev. Peruana Ginecología y Obstetricia",
    "2308-0531": "Rev. Fac. Medicina Humana (URP)",
    "2313-2957": "Rev. Inv. Altoandinas",
    "2413-4465": "Interacciones (Psicología)",
}


def journal_of(pid):
    issn = pid[1:10]
    return JOURNALS.get(issn, f"(ISSN {issn})"), issn


def contar_palabras(path):
    try:
        return len(open(path, encoding="utf-8", errors="ignore").read().split())
    except Exception:
        return 0


def stratified_sample(pool, n, seed):
    """Asignacion proporcional por revista + mayor resto, muestreo con semilla."""
    random.seed(seed)
    by_j = defaultdict(list)
    for r in pool:
        by_j[r["journal"]].append(r)

    total = len(pool)
    raw = {j: len(rs) / total * n for j, rs in by_j.items()}
    alloc = {j: int(v) for j, v in raw.items()}
    resto = n - sum(alloc.values())
    orden = sorted(raw.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)
    for j, _ in orden[:resto]:
        alloc[j] += 1
    for j in alloc:
        alloc[j] = min(alloc[j], len(by_j[j]))

    seleccion = []
    for j, rs in by_j.items():
        k = alloc[j]
        rs_sorted = sorted(rs, key=lambda r: r["pid"])
        seleccion += random.sample(rs_sorted, k) if k < len(rs_sorted) else rs_sorted
    return seleccion


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidatos", default="./data/candidatos.csv")
    ap.add_argument("--textos", default="./data/textos_limpios_final")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-palabras", type=int, default=150)
    ap.add_argument("--incluir-media", action="store_true",
                    help="Incluir tambien relevancia 'media' en el pool elegible.")
    ap.add_argument("--out", default="./data/set_validacion")
    args = ap.parse_args()

    if not os.path.isdir(args.textos):
        raise SystemExit(f"No existe la carpeta de textos: {args.textos}")

    # relevancia desde candidatos.csv
    relev = {}
    titulos = {}
    if os.path.exists(args.candidatos):
        for r in csv.DictReader(open(args.candidatos, encoding="utf-8")):
            relev[r["pid"]] = r.get("relevancia", "")
            titulos[r["pid"]] = r.get("titulo", "")
    else:
        print(f"AVISO: no se encontro {args.candidatos}; se tratara todo como relevancia 'alta'.")

    # pool = los .txt presentes en la carpeta limpia
    presentes = [f[:-4] for f in os.listdir(args.textos) if f.endswith(".txt")]
    rel_ok = {"alta"} | ({"media"} if args.incluir_media else set())

    pool = []
    descartados_rel = descartados_pal = 0
    for pid in presentes:
        rel = relev.get(pid, "alta")  # si no esta en candidatos, no excluir por relevancia
        if relev and rel not in rel_ok:
            descartados_rel += 1
            continue
        pal = contar_palabras(os.path.join(args.textos, pid + ".txt"))
        if pal < args.min_palabras:
            descartados_pal += 1
            continue
        journal, issn = journal_of(pid)
        pool.append({"pid": pid, "journal": journal, "issn": issn,
                     "relevancia": rel, "palabras": pal,
                     "titulo": titulos.get(pid, "")})

    if len(pool) < args.n:
        print(f"AVISO: el pool elegible ({len(pool)}) es menor que N={args.n}. Se seleccionan todos.")
        args.n = len(pool)

    seleccion = stratified_sample(pool, args.n, args.seed)
    sel_pids = {r["pid"] for r in seleccion}
    reserva = [r for r in pool if r["pid"] not in sel_pids]

    os.makedirs(args.out, exist_ok=True)
    textos_out = os.path.join(args.out, "textos")
    os.makedirs(textos_out, exist_ok=True)

    campos = ["pid", "journal", "issn", "relevancia", "palabras", "titulo"]

    def dump(path, data):
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            w.writerows(sorted(data, key=lambda r: r["pid"]))

    dump(os.path.join(args.out, "seleccion_validacion.csv"), seleccion)
    dump(os.path.join(args.out, "reserva.csv"), reserva)

    copiados = 0
    for r in seleccion:
        src = os.path.join(args.textos, r["pid"] + ".txt")
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(textos_out, r["pid"] + ".txt"))
            copiados += 1

    print("=" * 60)
    print("SELECCION DEL SET DE VALIDACION (v2)")
    print("=" * 60)
    print(f"  Textos en carpeta limpia ............... {len(presentes)}")
    print(f"  Descartados por relevancia ............. {descartados_rel}")
    print(f"  Descartados por < {args.min_palabras} palabras ......... {descartados_pal}")
    print(f"  Pool elegible (ok + {'/'.join(sorted(rel_ok))}) ........ {len(pool)}")
    print(f"  Seleccionados .......................... {len(seleccion)}")
    print(f"  Reserva (colchón) ...................... {len(reserva)}")
    print(f"  Semilla (reproducible) ................. {args.seed}")
    print(f"  .txt copiados .......................... {copiados}")
    print("\n  Distribución por revista (seleccion):")
    for j, c in Counter(r["journal"] for r in seleccion).most_common():
        print(f"     {c:3}  {j}")
    if seleccion:
        pal = sorted(r["palabras"] for r in seleccion)
        print(f"\n  Palabras: min={pal[0]}, mediana={pal[len(pal)//2]}, "
              f"max={pal[-1]}, total≈{sum(pal):,}")
    print(f"\n  Archivos en: {os.path.abspath(args.out)}")
    print(f"  Para reproducir: misma entrada + --seed {args.seed}")


if __name__ == "__main__":
    main()
