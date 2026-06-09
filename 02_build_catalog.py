#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_catalog.py — Catálogo descriptivo del corpus crudo.
Etapa 2/8 del pipeline.

Qué hace  : recorre los .txt crudos y detecta, por archivo, la revista (por ISSN),
            el tipo de artículo y las secciones presentes; vuelca un catálogo legible.
Entrada   : --src  carpeta con los .txt crudos (de la etapa 1).
Salida    : --out/descripcion_textos.txt (catálogo) + un resumen por consola.
Requisitos: Python 3.9+ (solo biblioteca estándar).
Uso       : python 02_build_catalog.py --src ./data/textos --out ./data/analisis_textos
Argumentos: --src, --out
"""
import argparse
import os, re, sys, glob, unicodedata, datetime
from collections import Counter

# Rutas por línea de comandos (sin rutas personales hardcodeadas).
_ap = argparse.ArgumentParser(
    description="Cataloga los .txt crudos del corpus: revista (ISSN), tipo y secciones.")
_ap.add_argument("--src", default="./data/textos",
                 help="Carpeta con los .txt crudos (entrada, de la etapa 1).")
_ap.add_argument("--out", default="./data/analisis_textos",
                 help="Carpeta de salida del catálogo.")
_args = _ap.parse_args()
SRC = _args.src
OUT_DIR = _args.out
OUT_TXT = os.path.join(OUT_DIR, "descripcion_textos.txt")

# ---- ISSN -> revista (verificado con portal.issn.org para los 14 ISSN del corpus) ----
JOURNALS = {
 "0034-8597": "Revista de Neuro-Psiquiatría",
 "1018-130X": "Revista Médica Herediana",
 "1019-4355": "Revista Estomatológica Herediana",
 "1022-5129": "Revista de Gastroenterología del Perú",
 "1025-5583": "Anales de la Facultad de Medicina (UNMSM)",
 "1609-9117": "Revista de Investigaciones Veterinarias del Perú (RIVEP)",
 "1726-4634": "Revista Peruana de Medicina Experimental y Salud Pública",
 "1727-558X": "Horizonte Médico (Lima)",
 "1728-5917": "Acta Médica Peruana",
 "2227-4731": "Revista del Cuerpo Médico Hospital Nacional Almanzor Aguinaga Asenjo",
 "2304-5132": "Revista Peruana de Ginecología y Obstetricia",
 "2308-0531": "Revista de la Facultad de Medicina Humana (URP)",
 "2313-2957": "Revista de Investigaciones Altoandinas",
 "2413-4465": "Interacciones (Revista de avances en Psicología)",
}

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def U(s):
    return strip_accents(s).strip().upper()

BOILER_EXACT = {
 "SERVICIOS PERSONALIZADOS","REVISTA","ARTICULO","INDICADORES","LINKS RELACIONADOS",
 "COMPARTIR","OTROS","COMO CITAR ESTE ARTICULO","SCIELO ANALYTICS","GOOGLE SCHOLAR H5M5 (2018)",
}
def is_boiler(line):
    u = U(line)
    if u in BOILER_EXACT: return True
    if u.startswith("- "): return True
    if "ISSN" in u and ("VERSION" in u or "IMPRESA" in u or "ON-LINE" in u or "ONLINE" in u): return True
    return False

def is_doi(line):
    s = line.strip().lower()
    return s.startswith("http") or s.startswith("doi:") or "doi.org" in s

# ---- secciones canonicas ----
SECTION_EXACT = {
 "RESUMEN":"Resumen","SUMMARY":"Abstract(EN)","ABSTRACT":"Abstract(EN)",
 "PALABRAS CLAVE":"Palabras clave","PALABRAS CLAVES":"Palabras clave","PALABRAS-CLAVE":"Palabras clave",
 "KEY WORDS":"Key words","KEYWORDS":"Key words",
 "INTRODUCCION":"Introducción","INTRODUCTION":"Introducción","ANTECEDENTES":"Introducción",
 "MATERIAL Y METODOS":"Materiales y métodos","MATERIALES Y METODOS":"Materiales y métodos",
 "MATERIAL Y METODO":"Materiales y métodos","PACIENTES Y METODOS":"Materiales y métodos",
 "METODOS":"Materiales y métodos","METODOLOGIA":"Materiales y métodos","METODO":"Materiales y métodos",
 "MATERIALS AND METHODS":"Materiales y métodos","METHODS":"Materiales y métodos",
 "RESULTADOS":"Resultados","RESULTADO":"Resultados","RESULTS":"Resultados",
 "DISCUSION":"Discusión","DISCUSSION":"Discusión","DISCUSION Y CONCLUSIONES":"Discusión",
 "CONCLUSIONES":"Conclusiones","CONCLUSION":"Conclusiones","CONCLUSIONS":"Conclusiones",
 "CASO CLINICO":"Caso clínico","REPORTE DE CASO":"Caso clínico","REPORTE DE CASOS":"Caso clínico",
 "PRESENTACION DEL CASO":"Caso clínico","PRESENTACION DE CASO":"Caso clínico","DESCRIPCION DEL CASO":"Caso clínico",
 "CASE REPORT":"Caso clínico","CASE PRESENTATION":"Caso clínico","CASE":"Caso clínico",
 "REFERENCIAS BIBLIOGRAFICAS":"Referencias","BIBLIOGRAFIA":"Referencias","REFERENCIAS":"Referencias",
 "REFERENCES":"Referencias",
 "AGRADECIMIENTOS":"Agradecimientos","ACKNOWLEDGMENTS":"Agradecimientos","ACKNOWLEDGEMENTS":"Agradecimientos",
}
def section_of(line):
    raw = U(line)
    if not raw: return None
    key = raw.rstrip(" .:;-")
    if key in SECTION_EXACT: return SECTION_EXACT[key]
    m = re.match(r'^(PALABRAS\s+CLAVES?|PALABRAS-CLAVE|KEY\s*WORDS|RESUMEN|ABSTRACT|SUMMARY)\b', raw)
    if m:
        g = m.group(1)
        if g.startswith("PALABRAS"): return "Palabras clave"
        if g.startswith("KEY"): return "Key words"
        if g == "RESUMEN": return "Resumen"
        return "Abstract(EN)"
    return None

# ---- tipo de articulo ----
TYPE_EXACT = {
 "REPORTE DE CASO":"Reporte de caso","REPORTE DE CASOS":"Reporte de caso","REPORTES DE CASOS":"Reporte de caso",
 "CASO CLINICO":"Reporte de caso","NOTA CLINICA":"Reporte de caso","REPORTE DE CASO CLINICO":"Reporte de caso",
 "CASE REPORT":"Reporte de caso","CLINICAL CASE":"Reporte de caso","CASE REPORTS":"Reporte de caso",
 "ARTICULO ORIGINAL":"Artículo original","ARTICULOS ORIGINALES":"Artículo original","TRABAJO ORIGINAL":"Artículo original",
 "ORIGINAL":"Artículo original","TRABAJOS ORIGINALES":"Artículo original","ORIGINAL ARTICLE":"Artículo original",
 "ARTICULO DE REVISION":"Revisión","REVISION":"Revisión","REVISION DE TEMA":"Revisión","REVIEW":"Revisión","REVIEW ARTICLE":"Revisión",
 "EDITORIAL":"Editorial","CARTA AL EDITOR":"Carta al editor","CARTAS AL EDITOR":"Carta al editor","LETTER TO THE EDITOR":"Carta al editor",
 "COMUNICACION CORTA":"Comunicación corta","COMUNICACION BREVE":"Comunicación corta","COMUNICACIONES CORTAS":"Comunicación corta",
 "IMAGENES":"Imágenes en medicina","IMAGENES EN MEDICINA":"Imágenes en medicina",
 "HISTORIA DE LA MEDICINA":"Historia de la medicina","SIMPOSIO":"Simposio","ACTUALIZACION":"Actualización",
 "ARTICULO ESPECIAL":"Artículo especial","SECCION ESPECIAL":"Sección especial",
}
def type_of(line):
    return TYPE_EXACT.get(U(line).rstrip(" .:;-"))

def looks_spanish(s):
    if re.search(r'[áéíóúñ¿¡ÁÉÍÓÚÑ]', s): return True
    return len(re.findall(r'\b(de|la|el|en|los|las|una|con|por|que|del|se|un)\b', s.lower())) >= 3

def read_lines(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return [ln.rstrip('\r\n') for ln in f]

def trim(t, n=650):
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) <= n: return t
    cut = t[:n]
    dot = cut.rfind('. ')
    if dot > n*0.5: return cut[:dot+1]
    return cut.rstrip(" ,;:") + "…"

def parse(path):
    name = os.path.basename(path)
    issn = name[1:10]
    year = name[10:14] if name[10:14].isdigit() else "?"
    lines = read_lines(path)

    # localizar bloque de cabecera por la PRIMERA aparicion de ISSN (no la ultima)
    issn_idx = [i for i,l in enumerate(lines) if 'ISSN' in l.upper()]
    cursor, voldate = 0, None
    if issn_idx:
        first = issn_idx[0]
        run_end = first
        j = first + 1
        while j < len(lines):
            if not lines[j].strip(): j += 1; continue
            if 'ISSN' in lines[j].upper(): run_end = j; j += 1; continue
            break
        k = run_end + 1
        while k < len(lines) and not lines[k].strip(): k += 1
        if k < len(lines):
            voldate = lines[k].strip(); cursor = k + 1
        else:
            cursor = run_end + 1
    else:
        k = 0
        while k < len(lines) and (not lines[k].strip() or is_boiler(lines[k])): k += 1
        cursor = k

    # saltar boilerplate / DOI / etiquetas de tipo; capturar tipo
    art_type = None
    while cursor < len(lines):
        l = lines[cursor]
        if not l.strip() or is_boiler(l) or is_doi(l): cursor += 1; continue
        t = type_of(l)
        if t:
            if art_type is None: art_type = t
            cursor += 1; continue
        break

    # titulo = primera linea sustancial que NO sea encabezado de seccion
    title = None
    if cursor < len(lines):
        cand = lines[cursor].strip()
        if cand and section_of(cand) is None and not is_doi(cand):
            title = cand; cursor += 1

    # estructura (secciones en orden de aparicion)
    structure, seen = [], set()
    ref_idx = None
    pc_idx = None
    for i, l in enumerate(lines):
        c = section_of(l)
        if c:
            if c not in seen:
                seen.add(c); structure.append(c)
            if c == "Referencias" and ref_idx is None: ref_idx = i
            if c == "Palabras clave" and pc_idx is None: pc_idx = i

    # "de que trata": resumen en espanol = linea no vacia inmediatamente antes de "Palabras clave"
    abstract = ""
    if pc_idx is not None:
        j = pc_idx - 1
        while j >= 0 and not lines[j].strip(): j -= 1
        if j >= 0 and section_of(lines[j]) is None:
            abstract = lines[j].strip()
            # incluir parrafo previo contiguo si el resumen abarca 2 lineas
            p = j - 1
            if p >= 0 and lines[p].strip() and section_of(lines[p]) is None and len(lines[j].strip()) < 120:
                abstract = lines[p].strip() + " " + abstract
    if not abstract:
        # fallback: primer parrafo de prosa real del cuerpo (omite encabezados y referencias)
        end = ref_idx if ref_idx is not None else len(lines)
        body = [l.strip() for l in lines[cursor:end]
                if l.strip() and not re.match(r'^\d+\s*[\.\)]', l.strip())
                and section_of(l.strip()) is None]
        longish = [s for s in body if len(s) >= 160]
        if longish: abstract = longish[0]
        elif body: abstract = max(body, key=len)

    return dict(name=name, issn=issn, year=year, voldate=voldate, type=art_type,
                title=title, abstract=abstract, structure=structure,
                nlines=sum(1 for l in lines if l.strip()))

def final_type(r):
    if r["type"]: return r["type"]
    s = set(r["structure"])
    if "Caso clínico" in s: return "Reporte de caso (inferido)"
    if "Materiales y métodos" in s and "Resultados" in s: return "Artículo original (inferido)"
    if "Resumen" not in s and r["nlines"] <= 10: return "Imagen / nota breve (inferido)"
    return "Indeterminado"

files = sorted(glob.glob(os.path.join(SRC, "*.txt")))
recs = [parse(p) for p in files]
for r in recs:
    r["journal"] = JOURNALS.get(r["issn"], "(revista no identificada)")
    r["ftype"] = final_type(r)

os.makedirs(OUT_DIR, exist_ok=True)

by_journal = Counter(r["journal"] for r in recs)
by_type = Counter(r["ftype"] for r in recs)
by_year = Counter(r["year"] for r in recs)
with_abs = sum(1 for r in recs if r["abstract"])
no_title = sum(1 for r in recs if not r["title"])

L = []
W = L.append
W("="*78)
W("CATÁLOGO DESCRIPTIVO DEL CORPUS DE TEXTOS")
W("Carpeta analizada: " + SRC)
W("Generado: " + datetime.date.today().isoformat() + "  |  Total de archivos: %d" % len(recs))
W("="*78)
W("")
W("QUÉ ES ESTE CORPUS")
W("-"*78)
W("Conjunto de %d artículos científicos en español, extraídos en texto plano" % len(recs))
W("desde SciELO Perú (14 revistas peruanas, en su mayoría biomédicas). Cada .txt")
W("es un artículo. El nombre del archivo es el identificador SciELO (PID): la letra")
W("S + ISSN de la revista + año + fascículo + correlativo. Ej.: S1022-5129 2005 ...")
W("")
W("Predomina con holgura el formato 'reporte de caso clínico'. Se detectan dos")
W("variantes de maquetación del texto:")
W("  • Clásica  : cabecera SciELO + revista/ISSN + título + RESUMEN/ABSTRACT +")
W("               INTRODUCCIÓN ... DISCUSIÓN + REFERENCIAS (todo en español).")
W("  • Reciente : artículo bilingüe; cuerpo en inglés (INTRODUCTION, CASE REPORT,")
W("               DISCUSSION) con resumen y palabras clave también en español.")
W("Algunos archivos vienen sin cabecera (empiezan directo en el cuerpo): en esos")
W("la revista se identifica por el ISSN del nombre del archivo.")
W("")
W("CÓMO LEER CADA FICHA")
W("-"*78)
W("  Revista    : nombre de la publicación (con su ISSN).")
W("  Año        : año de publicación (tomado del identificador del archivo).")
W("  Tipo       : clase de artículo; '(inferido)' = deducido de las secciones,")
W("               no rotulado explícitamente en el texto.")
W("  Título     : título del artículo tal como aparece en el texto.")
W("  Trata de   : de qué trata, tomado del RESUMEN del propio artículo (o del")
W("               primer párrafo cuando no hay resumen rotulado).")
W("  Estructura : secciones detectadas, en orden de aparición.")
W("")
W("RESUMEN GENERAL")
W("-"*78)
W("Artículos por revista:")
for j, c in by_journal.most_common():
    W("  %3d  %s" % (c, j))
W("")
W("Artículos por tipo:")
for t, c in by_type.most_common():
    W("  %3d  %s" % (c, t))
W("")
yrs = sorted(by_year)
W("Rango de años: %s–%s   |   Fichas con resumen extraído: %d/%d   |   Sin título en el texto: %d"
  % (yrs[0], yrs[-1], with_abs, len(recs), no_title))
W("")
W("="*78)
W("FICHAS POR ARCHIVO")
W("="*78)

for n, r in enumerate(recs, 1):
    W("")
    W("[%03d] %s" % (n, r["name"]))
    W("  Revista    : %s (ISSN %s)" % (r["journal"], r["issn"]))
    W("  Año        : %s%s" % (r["year"], ("   ·   " + r["voldate"]) if r["voldate"] else ""))
    W("  Tipo       : %s" % r["ftype"])
    W("  Título     : %s" % (r["title"] if r["title"] else "(sin título en el texto; el documento inicia directamente en el cuerpo)"))
    W("  Trata de   : %s" % (trim(r["abstract"]) if r["abstract"] else "(no se pudo extraer un resumen)"))
    W("  Estructura : %s" % (" › ".join(r["structure"]) if r["structure"] else "(sin secciones rotuladas)"))

with open(OUT_TXT, "w", encoding="utf-8") as f:
    f.write("\n".join(L))

# resumen por consola
print("OK ->", OUT_TXT)
print("Total:", len(recs), "| con resumen:", with_abs, "| sin titulo:", no_title)
print("TIPOS:", dict(by_type))
print("REVISTAS:", len(by_journal))
print("Indeterminados:", [r["name"] for r in recs if r["ftype"] == "Indeterminado"][:40])
