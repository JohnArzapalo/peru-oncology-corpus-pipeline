#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_scielo.py — Recolección de casos clínicos oncológicos desde SciELO Perú.
Etapa 1/8 del pipeline.

Qué hace  : recorre SciELO Perú vía la API de ArticleMeta, filtra por términos
            oncológicos en el título y descarga el texto de cada candidato. El
            guardado es incremental y reanudable (puede ir año por año).
Entrada   : API pública de ArticleMeta/SciELO (no requiere datos locales).
Salida    : <out>/candidatos.csv  y  <out>/textos/*.txt  (un .txt por artículo, PID).
Requisitos: Python 3.9+, dependencias en requirements.txt.
Uso       : python 01_harvest_scielo.py --by-year --from-year 1997 --until-year 2026 --out ./data
Argumentos: --by-year, --from-year, --until-year, --max-scan, --incluir-resumen,
            --delay (cortesía, s), --no-fetch (solo descubrir), --collection, --out

--- Documentación original del autor ---
RECOLECCION MASIVA v3 — SciELO Perú (casos clinicos oncologicos)
================================================================

Pensada para una corrida GRANDE y robusta (la v2 era para el piloto):
  * Guarda candidatos.csv de forma INCREMENTAL (no se pierde nada si se cae).
  * Descarga de texto REANUDABLE: salta los .txt ya bajados.
  * Opcion de recorrer la coleccion AÑO POR AÑO (--by-year) para que una
    caida solo afecte el año en curso y puedas reanudar con --from-year.
  * Descarga el texto de TODOS los candidatos encontrados.
  * Filtro por titulo (precision); incluye relevancia alta/media.

Instalacion:
    pip install articlemetaapi requests trafilatura beautifulsoup4 lxml

USO RECOMENDADO (corrida completa, robusta y reanudable):
    python scielo_peru_harvest_v3.py --by-year --from-year 1997 --until-year 2026

Reanudar si se corto (ej. murio en 2010):
    python scielo_peru_harvest_v3.py --by-year --from-year 2010 --until-year 2026

Modo pasada unica (mas simple, menos robusto ante caidas):
    python scielo_peru_harvest_v3.py --max-scan 0      # 0 = sin tope
"""

import argparse
import csv
import os
import re
import sys
import time
import unicodedata
from datetime import datetime

try:
    from articlemeta.client import RestfulClient
except ImportError:
    sys.exit("ERROR: pip install articlemeta""api requests trafilatura beautifulsoup4 lxml")

try:
    import requests
except ImportError:
    requests = None


ONCO_STRONG = [
    "carcinoma", "adenocarcinoma", "sarcoma", "linfoma", "leucemia", "melanoma",
    "mieloma", "blastoma", "glioma", "teratoma", "seminoma", "neoplasia",
    "neoplasico", "oncolog", "metastasis", "metastasico", "cancer",
    "carcinomatosis", "osteosarcoma", "liposarcoma",
]
ONCO_WEAK = ["tumor", "tumoral", "maligno", "malignidad", "masa "]
CASE_TITLE_HINTS = [
    "caso clinico", "reporte de caso", "reporte de un caso", "a proposito de un caso",
    "presentacion de un caso", "case report", "reporte de dos casos",
]

CSV_FIELDS = ["pid", "titulo", "relevancia", "tipo_documento", "anio",
              "idioma", "licencia", "html_url"]


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def safe(obj, name, *a):
    try:
        attr = getattr(obj, name)
    except Exception:
        return None
    if callable(attr):
        try:
            return attr(*a)
        except Exception:
            return None
    return attr


def get_pid(a):
    pid = safe(a, "publisher_id")
    if pid:
        return pid
    try:
        return a.data.get("code")
    except Exception:
        return None


def get_title(a):
    return safe(a, "original_title") or ""


def get_abstract(a):
    ab = safe(a, "original_abstract")
    if ab:
        return ab
    d = safe(a, "abstracts")
    if isinstance(d, dict) and d:
        return " ".join(str(v) for v in d.values())
    return ""


def license_of(a):
    p = safe(a, "permissions")
    return (p.get("id") or p.get("url") or "") if isinstance(p, dict) else ""


def looks_like_case(a):
    t = norm(get_title(a))
    if any(h in t for h in CASE_TITLE_HINTS):
        return True
    return norm(safe(a, "document_type")) == "case-report"


def oncology_relevance(title, abstract, incluir_resumen):
    nt = norm(title)
    if any(k in nt for k in ONCO_STRONG):
        return "alta"
    if any(k in nt for k in ONCO_WEAK):
        return "media"
    if incluir_resumen and any(k in norm(abstract) for k in ONCO_STRONG):
        return "media"
    return None


def html_url_for(a, pid):
    ft = safe(a, "fulltexts")
    if isinstance(ft, dict) and isinstance(ft.get("html"), dict) and ft["html"]:
        return next(iter(ft["html"].values()))
    domain = safe(a, "scielo_domain") or "www.scielo.org.pe"
    return f"http://{domain}/scielo.php?script=sci_arttext&pid={pid}&tlng=es"


def extract_text_from_html(html):
    try:
        import trafilatura
        txt = trafilatura.extract(html, favor_recall=True,
                                  include_comments=False, include_tables=False)
        if txt and len(txt) > 500:
            return txt, "trafilatura"
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        ps = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        ps = [p for p in ps if len(p) > 40]
        txt = "\n\n".join(ps)
        if txt and len(txt) > 500:
            return txt, "bs4_parrafos"
    except Exception:
        pass
    return "", "fallo_extraccion"


def fetch_and_save(client, collection, pid, html_url, textos_dir):
    ruta = os.path.join(textos_dir, f"{pid}.txt")
    if os.path.exists(ruta):                       # reanudable
        return os.path.getsize(ruta), "ya_existia"
    try:
        art = client.document(code=pid, collection=collection, body=True)
        body = safe(art, "original_html")
        if body and len(body) > 200:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(body)
            return len(body), "articlemeta_body"
    except Exception:
        pass
    if requests and html_url:
        try:
            r = requests.get(html_url, timeout=30,
                             headers={"User-Agent": "tesis-pucp-harvest/3.0"})
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or "utf-8"
                txt, fuente = extract_text_from_html(r.text)
                if txt:
                    with open(ruta, "w", encoding="utf-8") as f:
                        f.write(txt)
                    return len(txt), fuente
        except Exception as e:
            return 0, f"error_http:{type(e).__name__}"
    return 0, "sin_texto"


# --------------------------------------------------------------------------

def detect_peru(client, forced=None):
    if forced:
        return forced
    try:
        for c in client.collections():
            if "peru" in norm(str(c.get("name", c.get("original_name", "")))):
                return c.get("acronym") or "per"
    except Exception:
        pass
    return "per"


def load_seen_pids(csv_path):
    seen = set()
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("pid"):
                    seen.add(row["pid"])
    return seen


def discover_stream(client, collection, csv_path, seen, max_scan,
                    incluir_resumen, from_date=None, until_date=None):
    """Recorre la coleccion y va AÑADIENDO candidatos al CSV en el momento.
    Devuelve (nuevos_candidatos, escaneados)."""
    nuevos, scanned = [], 0
    write_header = not os.path.exists(csv_path)
    f = open(csv_path, "a", encoding="utf-8", newline="")
    w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    if write_header:
        w.writeheader()
    try:
        gen = client.documents(collection=collection, body=False,
                               from_date=from_date, until_date=until_date)
        for a in gen:
            scanned += 1
            if scanned % 500 == 0:
                print(f"      ...escaneados {scanned}, nuevos {len(nuevos)}")
                f.flush()
            try:
                title = get_title(a)
                rel = oncology_relevance(title, get_abstract(a), incluir_resumen)
                if not rel or not looks_like_case(a):
                    continue
                pid = get_pid(a)
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                row = {
                    "pid": pid, "titulo": title, "relevancia": rel,
                    "tipo_documento": safe(a, "document_type"),
                    "anio": safe(a, "publication_date"),
                    "idioma": safe(a, "original_language"),
                    "licencia": license_of(a),
                    "html_url": html_url_for(a, pid),
                }
                w.writerow(row)
                f.flush()
                nuevos.append(row)
                print(f"      [+][{rel}] {pid}  {title[:70]}")
            except Exception:
                continue
            if max_scan and scanned >= max_scan:
                break
    except KeyboardInterrupt:
        print("      (interrumpido por el usuario; lo recolectado quedo guardado)")
    except Exception as e:
        print(f"      (la consulta se corto: {type(e).__name__}: {e}; "
              f"lo recolectado quedo guardado)")
    finally:
        f.close()
    return nuevos, scanned


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=None)
    ap.add_argument("--out", default="./data")
    ap.add_argument("--max-scan", type=int, default=0, help="Tope de escaneo (0 = sin tope).")
    ap.add_argument("--incluir-resumen", action="store_true")
    ap.add_argument("--by-year", action="store_true", help="Recorrer año por año (robusto).")
    ap.add_argument("--from-year", type=int, default=1997)
    ap.add_argument("--until-year", type=int, default=datetime.now().year)
    ap.add_argument("--delay", type=float, default=0.8, help="Segundos entre descargas (cortesia).")
    ap.add_argument("--no-fetch", action="store_true", help="Solo descubrir, no bajar texto.")
    args = ap.parse_args()

    textos_dir = os.path.join(args.out, "textos")
    os.makedirs(textos_dir, exist_ok=True)
    csv_path = os.path.join(args.out, "candidatos.csv")

    print("=" * 70)
    print("RECOLECCION MASIVA v3 — SciELO Peru / casos oncologicos")
    print("Inicio:", datetime.now().isoformat(timespec="seconds"))
    print("=" * 70)

    client = RestfulClient()
    collection = detect_peru(client, args.collection)
    seen = load_seen_pids(csv_path)
    if seen:
        print(f"[i] Reanudando: {len(seen)} candidatos ya estaban en {csv_path}")

    # ---- DESCUBRIMIENTO ----
    print("\n[1] DESCUBRIMIENTO")
    todos_nuevos, total_scanned = [], 0
    if args.by_year:
        for y in range(args.from_year, args.until_year + 1):
            print(f"   --- Año {y} ---")
            nuevos, sc = discover_stream(
                client, collection, csv_path, seen, args.max_scan,
                args.incluir_resumen,
                from_date=f"{y}-01-01", until_date=f"{y}-12-31")
            todos_nuevos += nuevos
            total_scanned += sc
            print(f"   Año {y}: +{len(nuevos)} nuevos (acumulado {len(seen)})")
    else:
        todos_nuevos, total_scanned = discover_stream(
            client, collection, csv_path, seen, args.max_scan, args.incluir_resumen)

    print(f"\n   Total candidatos en CSV: {len(seen)}  (escaneados {total_scanned})")

    # ---- DESCARGA DE TEXTO (reanudable: lee TODO el CSV, salta lo ya bajado) ----
    if args.no_fetch:
        print("\n[2] (--no-fetch) Descarga omitida.")
        return

    print("\n[2] DESCARGA DE TEXTO (reanudable)")
    all_rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    log_path = os.path.join(args.out, "fetch_log.csv")
    logf = open(log_path, "a", encoding="utf-8", newline="")
    logw = csv.DictWriter(logf, fieldnames=["pid", "chars", "fuente"])
    if os.path.getsize(log_path) == 0:
        logw.writeheader()

    ok, longitudes = 0, []
    for i, row in enumerate(all_rows, 1):
        pid = row["pid"]
        n, fuente = fetch_and_save(client, collection, pid, row.get("html_url"), textos_dir)
        logw.writerow({"pid": pid, "chars": n, "fuente": fuente}); logf.flush()
        if n > 0:
            ok += 1
            if fuente != "ya_existia":
                longitudes.append(n)
        if i % 25 == 0:
            print(f"   ...procesados {i}/{len(all_rows)}, con texto {ok}")
        if fuente not in ("ya_existia",):
            time.sleep(args.delay)
    logf.close()

    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    altas = sum(1 for r in all_rows if r.get("relevancia") == "alta")
    print(f"  Candidatos totales ............. {len(all_rows)}  (alta: {altas})")
    print(f"  Con texto en disco ............. {ok}")
    if longitudes:
        print(f"  Long. promedio (nuevos) ........ {sum(longitudes)/len(longitudes):,.0f} chars")
    print(f"\n  Textos en: {os.path.abspath(textos_dir)}")
    print(f"  Catalogo:  {os.path.abspath(csv_path)}")
    print("\n  Siguiente paso: aplicar el script de limpieza a cada .txt para")
    print("  dejar solo la narrativa clinica en español (sin front-matter ni referencias).")
    print("Fin:", datetime.now().isoformat(timespec="seconds"))


if __name__ == "__main__":
    main()
