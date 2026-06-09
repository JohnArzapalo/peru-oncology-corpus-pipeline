#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filter_approved.py — Separa los aprobados del QC (revisar=no).
Etapa 7/8 del pipeline.

Qué hace  : lee el reporte del QC y copia SOLO los aprobados (revisar=no) a una
            carpeta nueva; lista los marcados (revisar=SI) en un CSV aparte para
            documentar la exclusión.
Entrada   : --qc qc_deepseek.csv, --textos carpeta clasificada.
Salida    : --out textos_aprobados/ + excluidos_qc.csv.
Requisitos: Python 3.9+ (solo biblioteca estándar).
Uso       : python 07_filter_approved.py --qc ./data/set_validacion/qc_deepseek.csv --textos ./data/set_validacion/textos --out ./data/set_validacion/textos_aprobados
Argumentos: --qc, --textos, --out, --marcados-csv

--- Documentación original del autor ---
FILTRAR APROBADOS DEL QC — arma el set final con los 'revisar=no'
=================================================================
Lee el reporte del QC (qc_deepseek.csv) y copia a una carpeta nueva SOLO los
documentos aprobados (revisar=no). Los marcados (revisar=SI) se dejan fuera,
pero se listan en un CSV aparte para que los revises y documentes la exclusion
en la tesis.

  El QC es triage: separar aprobados de marcados es operativo. La decision de
  excluir definitivamente sigue siendo tuya (revisa los marcados antes de
  descartarlos del todo).

Uso:
    py filtrar_aprobados.py --qc ./data/set_validacion/qc_deepseek.csv --textos ./data/set_validacion/textos --out ./data/set_validacion/textos_aprobados
"""

import argparse
import csv
import os
import shutil
from collections import Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qc", default="./data/set_validacion/qc_deepseek.csv",
                    help="CSV de salida del QC (con columnas pid, revisar, motivos).")
    ap.add_argument("--textos", default="./data/set_validacion/textos",
                    help="Carpeta con los .txt que se clasificaron.")
    ap.add_argument("--out", default="./data/set_validacion/textos_aprobados",
                    help="Carpeta destino para los aprobados (revisar=no).")
    ap.add_argument("--marcados-csv", default=None,
                    help="CSV donde listar los excluidos (por defecto, junto al --out).")
    args = ap.parse_args()

    if not os.path.exists(args.qc):
        raise SystemExit(f"No existe el reporte del QC: {args.qc}")
    if not os.path.isdir(args.textos):
        raise SystemExit(f"No existe la carpeta de textos: {args.textos}")
    os.makedirs(args.out, exist_ok=True)

    marcados_csv = args.marcados_csv or os.path.join(
        os.path.dirname(args.out) or ".", "excluidos_qc.csv")

    rows = list(csv.DictReader(open(args.qc, encoding="utf-8")))

    aprobados, marcados = [], []
    for r in rows:
        # 'revisar' == 'no' (aprobado) vs 'SI' (marcado). Tolerar mayus/minus.
        if str(r.get("revisar", "")).strip().lower() == "no":
            aprobados.append(r)
        else:
            marcados.append(r)

    # copiar aprobados que existan en disco
    copiados = faltantes = 0
    faltantes_list = []
    for r in aprobados:
        pid = r.get("pid", "")
        src = os.path.join(args.textos, pid + ".txt")
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(args.out, pid + ".txt"))
            copiados += 1
        else:
            faltantes += 1
            faltantes_list.append(pid)

    # guardar lista de excluidos (para documentar en la tesis)
    if marcados:
        campos = ["pid", "revisar", "motivos", "es_caso_oncologico",
                  "es_paciente_humano", "idioma_cuerpo", "tipo_cancer",
                  "tiene_narrativa_paciente", "calidad_recorte", "problemas"]
        with open(marcados_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            w.writerows(marcados)

    # desglose de motivos de exclusion
    motc = Counter()
    for r in marcados:
        for mo in (r.get("motivos", "") or "").split(";"):
            mo = mo.strip()
            if mo:
                motc[mo] += 1

    print("=" * 60)
    print("FILTRAR APROBADOS DEL QC")
    print("=" * 60)
    print(f"  Documentos en el QC ............ {len(rows)}")
    print(f"  APROBADOS (revisar=no) ......... {len(aprobados)}")
    print(f"  Marcados (revisar=SI) .......... {len(marcados)}")
    print(f"  .txt copiados a la carpeta ..... {copiados}"
          + (f"  (FALTAN {faltantes} en disco)" if faltantes else ""))
    if faltantes_list:
        print(f"     faltantes: {', '.join(faltantes_list[:10])}"
              + (" ..." if len(faltantes_list) > 10 else ""))
    if motc:
        print("\n  Motivos de los marcados (excluidos):")
        for mo, c in motc.most_common():
            print(f"     {c:3}  {mo}")
    print(f"\n  Aprobados en: {os.path.abspath(args.out)}")
    if marcados:
        print(f"  Lista de excluidos: {os.path.abspath(marcados_csv)}")
    print("\n  RECORDATORIO: revisa los marcados antes de descartarlos del todo;")
    print("  algunos pueden ser falsos positivos del QC. Documenta la exclusion")
    print("  en la tesis (de N candidatos se excluyeron M por estos motivos).")


if __name__ == "__main__":
    main()
