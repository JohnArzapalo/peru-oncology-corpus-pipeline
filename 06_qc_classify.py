#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qc_classify.py — Control de calidad (triage) con DeepSeek.
Etapa 6/8 del pipeline.

Qué hace  : pasa cada caso completo en una sola llamada y obtiene un JSON con la
            clasificación (¿es caso oncológico?, ¿humano?, idioma, calidad del
            recorte, revisar sí/no). Registra el consumo de tokens. NO anota entidades.
Entrada   : --textos carpeta del set de validación.
Salida    : --out qc_deepseek.csv (+ --usage-log opcional con tokens).
Requisitos: Python 3.9+, requests; variable de entorno DEEPSEEK_API_KEY.
Uso       : python 06_qc_classify.py --textos ./data/set_validacion/textos --out ./data/set_validacion/qc_deepseek.csv
Argumentos: --textos, --out, --usage-log, --modelo, --max-chars, --max-tokens, --delay

--- Documentación original del autor ---
CONTROL DE CALIDAD CON DEEPSEEK — texto completo en una sola llamada
====================================================================

DeepSeek tiene ventana de contexto de 1M tokens, asi que cada caso clinico
(~3,250 tokens) entra ENTERO en una sola llamada: el modelo analiza TODO el
documento de una vez y da la conclusion. No hace falta trocear.

Ademas registra el consumo de tokens de cada llamada en un log
(usage_log.csv) para calcular el costo real con costos_deepseek.py --log.

ATENCION: Esto es CONTROL DE CALIDAD (triage), NO anotacion. El LLM NO produce
el gold standard. La decision final (incluir/excluir y las anotaciones de
entidades) la hace y verifica un humano con apoyo clinico.

Requisitos:
    pip install requests
    set DEEPSEEK_API_KEY=tu-clave    (sacala en https://platform.deepseek.com)

Uso:
    py qc_deepseek.py --textos ./data/set_validacion/textos --out ./data/set_validacion/qc_deepseek.csv
"""

import argparse
import csv
import json
import os
import re
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("Falta 'requests'. Ejecuta: pip install requests")

URL = "https://api.deepseek.com/chat/completions"
ENV_KEY = "DEEPSEEK_API_KEY"
MODELO_DEFAULT = "deepseek-chat"   # = v4-flash modo no-pensante (ideal para clasificar)

SYSTEM_PROMPT = (
    "Eres un asistente de control de calidad de un corpus de casos clinicos en espanol. "
    "Clasificas textos, NO anotas entidades. Respondes SIEMPRE en JSON valido, sin "
    "texto adicional ni markdown."
)

USER_TEMPLATE = """Analiza el siguiente texto COMPLETO de un articulo medico y responde SOLO con un objeto JSON con EXACTAMENTE estas claves:

{{
  "es_caso_oncologico": true|false,
  "es_paciente_humano": true|false,
  "idioma_cuerpo": "es"|"en"|"mixto",
  "tipo_cancer": "string",
  "tiene_narrativa_paciente": true|false,
  "calidad_recorte": "limpio"|"arrastra_referencias"|"arrastra_frontmatter"|"truncado"|"otro",
  "problemas": "string",
  "confianza": 0.0
}}

TEXTO:
\"\"\"
{texto}
\"\"\"
"""


def parse_response(content):
    if not content:
        return None
    content = re.sub(r"^```(json)?", "", content.strip())
    content = re.sub(r"```$", "", content.strip())
    content = content.strip()
    i, j = content.find("{"), content.rfind("}")
    if i != -1 and j != -1 and j > i:
        content = content[i:j + 1]
    try:
        return json.loads(content)
    except Exception:
        return None


def call_deepseek(api_key, model, texto, max_tokens=600, retries=5, timeout=120):
    """Devuelve (parsed, usage_dict, err)."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(texto=texto)},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    backoff = 5
    for intento in range(1, retries + 1):
        try:
            r = requests.post(URL, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                content = data["choices"][0]["message"].get("content", "")
                usage = data.get("usage", {}) or {}
                parsed = parse_response(content)
                if parsed is not None:
                    return parsed, usage, ""
                return None, usage, "respuesta_vacia_o_no_json"
            if r.status_code == 429:
                wait = r.headers.get("Retry-After")
                try:
                    wait = float(wait)
                except (TypeError, ValueError):
                    wait = backoff
                time.sleep(min(wait, 65)); backoff = min(backoff * 2, 65); continue
            if r.status_code in (500, 502, 503):
                time.sleep(backoff); backoff = min(backoff * 2, 65); continue
            if r.status_code in (401, 402, 403):
                return None, {}, f"HTTP {r.status_code} (revisa clave o saldo): {r.text[:120]}"
            return None, {}, f"HTTP {r.status_code}: {r.text[:120]}"
        except Exception as e:
            if intento == retries:
                return None, {}, f"{type(e).__name__}: {e}"
            time.sleep(backoff); backoff = min(backoff * 2, 65)
    return None, {}, "agotados_reintentos"


CAMPOS = ["pid", "revisar", "motivos", "es_caso_oncologico", "es_paciente_humano",
          "idioma_cuerpo", "tipo_cancer", "tiene_narrativa_paciente",
          "calidad_recorte", "confianza", "problemas", "error"]

USAGE_CAMPOS = ["pid", "prompt_tokens", "completion_tokens",
                "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"]


def decidir_flag(res):
    motivos = []
    if res is None:
        return True, "sin_respuesta_del_modelo"
    if res.get("es_caso_oncologico") is False:
        motivos.append("no_es_caso_oncologico")
    if res.get("es_paciente_humano") is False:
        motivos.append("no_humano")
    if res.get("idioma_cuerpo") in ("en", "mixto"):
        motivos.append(f"idioma_{res.get('idioma_cuerpo')}")
    if res.get("tiene_narrativa_paciente") is False:
        motivos.append("sin_narrativa_paciente")
    if res.get("calidad_recorte") not in ("limpio", None, ""):
        motivos.append(f"recorte_{res.get('calidad_recorte')}")
    try:
        if float(res.get("confianza", 1)) < 0.6:
            motivos.append("baja_confianza")
    except (TypeError, ValueError):
        pass
    return (len(motivos) > 0), ";".join(motivos)


def cargar_procesados(out_path):
    hechos = set()
    if os.path.exists(out_path):
        for row in csv.DictReader(open(out_path, encoding="utf-8")):
            pid = row.get("pid")
            exito = (not row.get("error")) and row.get("es_caso_oncologico") not in (None, "")
            if pid and exito:
                hechos.add(pid)
    return hechos


def reescribir_sin_fallidos(out_path, hechos):
    if not os.path.exists(out_path):
        return
    filas = [r for r in csv.DictReader(open(out_path, encoding="utf-8"))
             if r.get("pid") in hechos]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, extrasaction="ignore")
        w.writeheader(); w.writerows(filas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--textos", default="./data/set_validacion/textos")
    ap.add_argument("--out", default="./data/set_validacion/qc_deepseek.csv")
    ap.add_argument("--usage-log", default=None)
    ap.add_argument("--modelo", default=MODELO_DEFAULT,
                    help="deepseek-chat (flash, recomendado) o deepseek-reasoner (pro).")
    ap.add_argument("--max-chars", type=int, default=60000)
    ap.add_argument("--max-tokens", type=int, default=600)
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    api_key = os.environ.get(ENV_KEY)
    if not api_key:
        sys.exit(f"ERROR: define {ENV_KEY} con tu clave (set {ENV_KEY}=tu-clave).")

    if not os.path.isdir(args.textos):
        sys.exit(f"No existe la carpeta: {args.textos}")

    usage_log = args.usage_log or os.path.join(os.path.dirname(args.out) or ".", "usage_log.csv")

    archivos = sorted(f for f in os.listdir(args.textos) if f.endswith(".txt"))
    hechos = cargar_procesados(args.out)
    reescribir_sin_fallidos(args.out, hechos)
    pendientes = [f for f in archivos if f[:-4] not in hechos]

    print("=" * 60)
    print("CONTROL DE CALIDAD CON DEEPSEEK  (texto completo, 1 llamada/doc)")
    print("=" * 60)
    print(f"  Modelo ................. {args.modelo}")
    print(f"  Textos totales ......... {len(archivos)}")
    print(f"  Exitosos previos ....... {len(hechos)}")
    print(f"  Pendientes ............. {len(pendientes)}")
    print(f"  Log de consumo ......... {usage_log}\n")

    escribir_header = (not os.path.exists(args.out)) or os.path.getsize(args.out) == 0
    f_out = open(args.out, "a", encoding="utf-8", newline="")
    writer = csv.DictWriter(f_out, fieldnames=CAMPOS, extrasaction="ignore")
    if escribir_header:
        writer.writeheader()

    log_header = (not os.path.exists(usage_log)) or os.path.getsize(usage_log) == 0
    f_log = open(usage_log, "a", encoding="utf-8", newline="")
    logw = csv.DictWriter(f_log, fieldnames=USAGE_CAMPOS, extrasaction="ignore")
    if log_header:
        logw.writeheader()

    flagged = 0
    tot_in = tot_out = 0
    for i, fn in enumerate(pendientes, 1):
        pid = fn[:-4]
        texto = open(os.path.join(args.textos, fn), encoding="utf-8", errors="ignore").read()
        if len(texto) > args.max_chars:
            texto = texto[:args.max_chars]
        res, usage, err = call_deepseek(api_key, args.modelo, texto, args.max_tokens)
        revisar, motivos = decidir_flag(res)
        if revisar:
            flagged += 1

        fila = {"pid": pid, "revisar": "SI" if revisar else "no",
                "motivos": motivos, "error": err}
        if res:
            for k in ("es_caso_oncologico", "es_paciente_humano", "idioma_cuerpo",
                      "tipo_cancer", "tiene_narrativa_paciente", "calidad_recorte",
                      "confianza", "problemas"):
                fila[k] = res.get(k, "")
        writer.writerow(fila); f_out.flush()

        if usage:
            logw.writerow({
                "pid": pid,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
                "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
            }); f_log.flush()
            tot_in += usage.get("prompt_tokens", 0)
            tot_out += usage.get("completion_tokens", 0)

        marca = "REVISAR" if revisar else "ok"
        extra = f" [{motivos}]" if motivos else ""
        print(f"  [{i:3}/{len(pendientes)}] {pid}: {marca}{extra}"
              + (f"  ERROR={err}" if err else ""))
        time.sleep(args.delay)

    f_out.close(); f_log.close()

    costo = tot_in / 1e6 * 0.14 + tot_out / 1e6 * 0.28
    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"  Procesados esta corrida ..... {len(pendientes)}")
    print(f"  Marcados para REVISAR ....... {flagged}")
    print(f"  Tokens: entrada {tot_in:,} / salida {tot_out:,}")
    print(f"  Costo aprox. esta corrida ... ${costo:.4f}  (modelo flash)")
    print(f"\n  Reporte QC: {os.path.abspath(args.out)}")
    print(f"  Log consumo: {os.path.abspath(usage_log)}")
    print(f"  Costo exacto:  py costos_deepseek.py --log \"{usage_log}\"")
    print("\n  RECORDATORIO: triage, no anotacion. Revisa a mano los 'revisar=SI'.")


if __name__ == "__main__":
    main()
