#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clean_corpus.py — Limpieza en dos etapas (reglas + LLM, solo recorta).
Etapa 4/8 del pipeline.

Qué hace  : (1) reglas recortan front-matter, resumen en inglés y referencias;
            (2) DeepSeek detecta basura residual y el código corta por slicing. El
            texto conservado es siempre subcadena literal del original (no reescribe).
Entrada   : --in  carpeta de textos seleccionados (etapa 3).
Salida    : --out carpeta de textos limpios + --reporte CSV (qué hizo cada etapa).
Requisitos: Python 3.9+, requests; variable de entorno DEEPSEEK_API_KEY
            (o usa --solo-reglas para correr sin LLM).
Uso       : python 04_clean_corpus.py --in ./data/seleccionados --out ./data/textos_limpios_final --reporte ./data/limpieza_final_reporte.csv
Argumentos: --in, --out, --reporte, --modelo, --solo-reglas, --delay

--- Documentación original del autor ---
LIMPIEZA TODO-EN-UNO (Opcion B: REGLAS + pulido con LLM)
========================================================
Un solo comando que hace las dos etapas de limpieza en cadena:

  ETAPA 1 (reglas, gratis, deterministica):
     recorta inicio (front-matter, resumen EN) y fin (referencias, etc.)
     buscando encabezados de seccion. Hace el grueso del trabajo.

  ETAPA 2 (LLM, DeepSeek):
     sobre el resultado de la etapa 1, el LLM DETECTA basura residual de
     casuisticas raras y el CODIGO corta el texto (no lo reescribe).

Resultado: una carpeta con los textos finales limpios + un reporte por
documento (que hizo cada etapa).

  El texto conservado es siempre subcadena literal del original
  (reglas y LLM solo recortan por slicing; nunca reescriben).

Requisitos: pip install requests ; set DEEPSEEK_API_KEY=tu-clave

Uso:
    py limpieza_opcionB.py --in ./data/seleccionados --out ./data/textos_limpios_final --reporte ./data/limpieza_final_reporte.csv
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata

try:
    import requests
except ImportError:
    sys.exit("Falta 'requests'. Ejecuta: pip install requests")

# =====================================================================
# ETAPA 1 — REGLAS (logica de limpieza_corpus.py, modo normal)
# =====================================================================

START_PATTERNS = [
    ("introduccion", r"^\s*introducci[oó]n\b"),
    ("presentacion_caso", r"^\s*presentaci[oó]n\s+de\s*l?\s+caso\b"),
    ("caso_clinico", r"^\s*caso\s+cl[ií]nic[oa]\b"),
    ("reporte_caso", r"^\s*reporte\s+de\s*l?\s+caso\b"),
    ("descripcion_caso", r"^\s*descripci[oó]n\s+de\s*l?\s+caso\b"),
    ("antecedentes", r"^\s*antecedentes\b"),
]
END_PATTERNS = [
    ("referencias", r"^\s*referencias(\s+bibliogr[aá]ficas)?\b"),
    ("bibliografia", r"^\s*bibliograf[ií]a\b"),
    ("references", r"^\s*references\b"),
    ("correspondencia", r"^\s*correspondencia\b"),
    ("agradecimientos", r"^\s*agradecimiento"),
    ("conflicto_interes", r"^\s*conflicto[s]?\s+de\s+inter[eé]s"),
    ("financiamiento", r"^\s*(financiamiento|fuente[s]?\s+de\s+financiamiento)\b"),
    ("declaracion", r"^\s*declaraci[oó]n"),
    ("recibido", r"^\s*(recibido|aceptado)\s*:"),
]
NAV_NOISE = {
    "servicios personalizados", "revista", "articulo", "indicadores",
    "citado por scielo", "links relacionados", "similares en scielo",
    "compartir", "como citar este articulo", "scielo analytics",
}
FIG_LINE = re.compile(
    r"^\s*(foto|figura|fig\.?|tabla|cuadro|gr[aá]fico|imagen|l[aá]mina|esquema)\s*(n[°º.\s]|\d|:)",
    re.IGNORECASE)
RE_LINKS = re.compile(r"\[\s*links?\s*\]", re.IGNORECASE)
RE_FIG_INLINE = re.compile(
    r"\(\s*(foto|figura|fig\.?|tabla|cuadro|gr[aá]fico|imagen|l[aá]mina|esquema)[^)]{0,30}\)",
    re.IGNORECASE)
RE_CITE_PAREN = re.compile(r"\(\s*\d+(?:\s*[,\-–y]\s*\d+)*\s*\)")
RE_CITE_BRACKET = re.compile(r"\[\s*\d+(?:\s*[,\-–]\s*\d+)*\s*\]")
RE_URL = re.compile(r"http[s]?://\S+")
RE_ISSN = re.compile(r"\bissn\b|versi[oó]n\s+(impresa|on-?line)", re.IGNORECASE)
RE_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _find_first(patterns, text, after=0):
    best_pos, best_label = None, None
    for label, pat in patterns:
        m = re.compile(pat, re.IGNORECASE | re.MULTILINE).search(text, after)
        if m and (best_pos is None or m.start() < best_pos):
            best_pos, best_label = m.start(), label
    return best_pos, best_label


def _line_cleanup(text):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(""); continue
        if _norm(s) in NAV_NOISE: continue
        if FIG_LINE.match(s): continue
        if RE_ISSN.search(s) and len(s) < 120: continue
        if re.fullmatch(r"[\d\s.\-–]+", s): continue
        out.append(line)
    return "\n".join(out)


def _inner_cleanup(text):
    text = unicodedata.normalize("NFC", text)
    text = RE_CTRL.sub("", text)
    text = RE_URL.sub("", text)
    text = RE_LINKS.sub("", text)
    text = RE_FIG_INLINE.sub("", text)
    text = RE_CITE_PAREN.sub("", text)
    text = RE_CITE_BRACKET.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text.strip()


# marcadores de un bloque de resumen/keywords en INGLES (front-matter bilingue)
RE_ABSTRACT_EN = re.compile(r"^\s*(abstract|summary)\b", re.IGNORECASE | re.MULTILINE)
# "INTRODUCCION" es el inicio mas confiable del contenido narrativo en español
RE_INTRO = re.compile(r"^\s*introducci[oó]n\b", re.IGNORECASE | re.MULTILINE)
# encabezados de TIPO de articulo que NO son inicio de narrativa (van al principio)
TIPO_INICIO = ("reporte_caso", "caso_clinico", "presentacion_caso", "descripcion_caso")


def etapa1_reglas(raw):
    """Devuelve (texto_limpio, info). Recorta por encabezados; limpia ruido.

    Maneja el caso bilingue: si hay un bloque ABSTRACT/SUMMARY en ingles antes
    de la INTRODUCCION (tipico de revistas que publican resumen en 2 idiomas),
    el inicio se fija en la INTRODUCCION para no arrastrar el texto en ingles.
    """
    info = {"inicio_regla": "", "fin_regla": ""}
    text = _line_cleanup(raw)

    start, slabel = _find_first(START_PATTERNS, text)
    if start is None:
        start, slabel = 0, "sin_inicio"

    # --- CORRECCION del front-matter bilingue ---
    # Localizar INTRODUCCION y un eventual bloque ABSTRACT/SUMMARY en ingles.
    intro_m = RE_INTRO.search(text)
    abs_m = RE_ABSTRACT_EN.search(text)
    intro_pos = intro_m.start() if intro_m else None

    if intro_pos is not None:
        # Si el inicio detectado es un encabezado de TIPO (reporte/caso clinico)
        # que esta al principio del documento, y existe una INTRODUCCION mas
        # abajo, el verdadero contenido empieza en la INTRODUCCION.
        if slabel in TIPO_INICIO and start < intro_pos:
            start, slabel = intro_pos, "introduccion"
        # Si hay un bloque ABSTRACT/SUMMARY (ingles) ANTES de la introduccion,
        # y el inicio actual cae antes de ese bloque, saltamos a la INTRODUCCION
        # para no conservar el resumen en ingles.
        elif abs_m and start <= abs_m.start() < intro_pos:
            start, slabel = intro_pos, "introduccion"

    info["inicio_regla"] = slabel
    end, elabel = _find_first(END_PATTERNS, text, after=start + 1)
    if end is None:
        end, elabel = len(text), "fin_archivo"
    info["fin_regla"] = elabel
    core = _inner_cleanup(text[start:end])
    return core, info


# =====================================================================
# ETAPA 2 — PULIDO CON LLM (logica de limpieza_asistida_llm.py)
# =====================================================================

URL = "https://api.deepseek.com/chat/completions"
SYSTEM_PROMPT = ("Eres un asistente que IDENTIFICA (no reescribe) secciones no clinicas. "
                 "Respondes SIEMPRE en JSON valido, sin markdown.")
USER_TEMPLATE = """Te doy el texto de un caso clinico ya parcialmente limpio. NO lo reescribas. Solo IDENTIFICA si AUN arrastra material no clinico al inicio o al final, copiando un fragmento textual EXACTO del documento.

Responde SOLO con este JSON:
{{
  "inicio_basura": true|false,
  "frag_inicio_contenido": "string",
  "fin_basura": true|false,
  "frag_inicio_basura_final": "string",
  "tipo_basura": "string"
}}
Regla: los fragmentos deben ser copia EXACTA del texto. Ante duda, deja false (mejor conservar que borrar contenido clinico).

TEXTO:
\"\"\"
{texto}
\"\"\"
"""


def _parse_json(content):
    if not content:
        return None
    content = re.sub(r"^```(json)?", "", content.strip())
    content = re.sub(r"```$", "", content.strip())
    i, j = content.find("{"), content.rfind("}")
    if i != -1 and j != -1:
        content = content[i:j + 1]
    try:
        return json.loads(content)
    except Exception:
        return None


def _call(api_key, model, texto, retries=5, timeout=180):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": USER_TEMPLATE.format(texto=texto[:60000])}],
               "temperature": 0, "max_tokens": 400,
               "response_format": {"type": "json_object"}}
    backoff = 5
    for intento in range(1, retries + 1):
        try:
            r = requests.post(URL, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                return _parse_json(data["choices"][0]["message"].get("content", "")), data.get("usage", {}) or {}, ""
            if r.status_code == 429:
                w = r.headers.get("Retry-After")
                try: w = float(w)
                except (TypeError, ValueError): w = backoff
                time.sleep(min(w, 65)); backoff = min(backoff*2, 65); continue
            if r.status_code in (500, 502, 503):
                time.sleep(backoff); backoff = min(backoff*2, 65); continue
            if r.status_code in (401, 402, 403):
                return None, {}, f"HTTP {r.status_code} (clave/saldo)"
            return None, {}, f"HTTP {r.status_code}"
        except Exception as e:
            if intento == retries:
                return None, {}, f"{type(e).__name__}"
            time.sleep(backoff); backoff = min(backoff*2, 65)
    return None, {}, "agotados_reintentos"


def _buscar_flex(texto, frag):
    if not frag or len(frag) < 4:
        return -1
    idx = texto.find(frag)
    if idx != -1:
        return idx
    pal = [re.escape(p) for p in frag.split()]
    if len(pal) < 2:
        return -1
    m = re.search(r"\s+".join(pal), texto)
    return m.start() if m else -1


def etapa2_llm(texto, api_key, model):
    """Pule el texto (que ya paso por reglas). Devuelve (texto, info, usage, err)."""
    det, usage, err = _call(api_key, model, texto)
    info = {"llm_inicio": False, "llm_fin": False, "llm_tipo": "", "llm_descartado": "no"}
    if det is None:
        return texto, info, usage, err
    info["llm_tipo"] = det.get("tipo_basura", "")
    ini, fin = 0, len(texto)
    if det.get("inicio_basura"):
        pos = _buscar_flex(texto, (det.get("frag_inicio_contenido") or "").strip())
        if pos > 0:
            ini = pos; info["llm_inicio"] = True
    if det.get("fin_basura"):
        pos = _buscar_flex(texto, (det.get("frag_inicio_basura_final") or "").strip())
        if pos > ini:
            fin = pos; info["llm_fin"] = True
    cortado = texto[ini:fin].strip()
    # salvaguarda
    if len(cortado.split()) < 150 or len(cortado) < 0.3 * len(texto):
        info["llm_descartado"] = "SI (corte sospechoso; se conserva etapa 1)"
        return texto.strip(), info, usage, err
    return cortado, info, usage, err


# =====================================================================
# ORQUESTACION
# =====================================================================

CAMPOS = ["pid", "chars_original", "chars_etapa1", "chars_final",
          "inicio_regla", "fin_regla", "llm_inicio", "llm_fin",
          "llm_tipo", "llm_descartado", "error"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="indir", default="./data/seleccionados")
    ap.add_argument("--out", dest="outdir", default="./data/textos_limpios_final")
    ap.add_argument("--reporte", default="./data/limpieza_final_reporte.csv")
    ap.add_argument("--modelo", default="deepseek-chat")
    ap.add_argument("--solo-reglas", action="store_true",
                    help="Hacer solo la etapa 1 (sin LLM), util para probar gratis.")
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not args.solo_reglas and not api_key:
        sys.exit("ERROR: define DEEPSEEK_API_KEY (o usa --solo-reglas para probar la etapa 1).")
    if not os.path.isdir(args.indir):
        sys.exit(f"No existe: {args.indir}")
    os.makedirs(args.outdir, exist_ok=True)

    archivos = sorted(f for f in os.listdir(args.indir) if f.endswith(".txt"))
    hechos = set()
    if os.path.exists(args.reporte):
        for r in csv.DictReader(open(args.reporte, encoding="utf-8")):
            if r.get("pid") and not r.get("error"):
                hechos.add(r["pid"])

    nuevo = (not os.path.exists(args.reporte)) or os.path.getsize(args.reporte) == 0
    f_rep = open(args.reporte, "a", encoding="utf-8", newline="")
    repw = csv.DictWriter(f_rep, fieldnames=CAMPOS, extrasaction="ignore")
    if nuevo:
        repw.writeheader()

    print("=" * 60)
    print("LIMPIEZA TODO-EN-UNO  (Opcion B: reglas + LLM)")
    print("=" * 60)
    print(f"  Entrada: {args.indir}  ({len(archivos)} textos)")
    print(f"  Salida:  {args.outdir}")
    print(f"  Modo:    {'SOLO REGLAS (sin LLM)' if args.solo_reglas else 'reglas + LLM ('+args.modelo+')'}")
    print(f"  Ya hechos: {len(hechos)}\n")

    tot_in = tot_out = 0
    for i, fn in enumerate(archivos, 1):
        pid = fn[:-4]
        if pid in hechos:
            continue
        raw = open(os.path.join(args.indir, fn), encoding="utf-8", errors="ignore").read()

        # ETAPA 1: reglas
        t1, info1 = etapa1_reglas(raw)

        # ETAPA 2: LLM (si corresponde)
        info2 = {"llm_inicio": False, "llm_fin": False, "llm_tipo": "", "llm_descartado": "n/a"}
        err = ""
        final = t1
        if not args.solo_reglas:
            final, info2, usage, err = etapa2_llm(t1, api_key, args.modelo)
            if usage:
                tot_in += usage.get("prompt_tokens", 0)
                tot_out += usage.get("completion_tokens", 0)

        with open(os.path.join(args.outdir, fn), "w", encoding="utf-8") as f:
            f.write(final)

        repw.writerow({
            "pid": pid, "chars_original": len(raw), "chars_etapa1": len(t1),
            "chars_final": len(final), "inicio_regla": info1["inicio_regla"],
            "fin_regla": info1["fin_regla"], "llm_inicio": info2["llm_inicio"],
            "llm_fin": info2["llm_fin"], "llm_tipo": info2["llm_tipo"],
            "llm_descartado": info2["llm_descartado"], "error": err,
        }); f_rep.flush()

        marca = []
        if info2["llm_inicio"] or info2["llm_fin"]:
            marca.append("LLM recorto extra")
        print(f"  [{i:3}/{len(archivos)}] {pid}: {len(raw)}->{len(t1)}->{len(final)} chars"
              + ("  " + ";".join(marca) if marca else "")
              + (f"  ERROR={err}" if err else ""))
        if not args.solo_reglas:
            time.sleep(args.delay)

    f_rep.close()
    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    if not args.solo_reglas:
        costo = tot_in/1e6*0.14 + tot_out/1e6*0.28
        print(f"  Tokens LLM: in {tot_in:,} / out {tot_out:,}  -> ~${costo:.4f} (flash)")
    print(f"  Textos finales en: {os.path.abspath(args.outdir)}")
    print(f"  Reporte: {os.path.abspath(args.reporte)}")
    print("\n  El texto conservado es subcadena literal del original (solo recorte).")


if __name__ == "__main__":
    main()
