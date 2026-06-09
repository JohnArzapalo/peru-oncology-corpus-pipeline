#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preannotate_ner.py — Pre-anotación NER (borrador, NO gold) con DeepSeek.
Etapa 8/8 del pipeline.

Qué hace  : propone un primer etiquetado de 5 entidades (ENFERMEDAD, SINTOMA,
            PROCEDIMIENTO, MEDICAMENTO, MORFOLOGIA) con offsets exactos y lo guarda
            en formato de PREDICCIONES de Label Studio para corrección humana.
Entrada   : --textos carpeta de textos aprobados (etapa 7).
Salida    : --out JSON (formato Label Studio) + --usage-log opcional.
Requisitos: Python 3.9+, requests; variable de entorno DEEPSEEK_API_KEY.
Uso       : python 08_preannotate_ner.py --textos ./data/set_validacion/textos_aprobados --out ./data/preanot.json
Argumentos: --textos, --out (obligatorio), --modelo, --usage-log, --max-tokens, --delay, --limite

--- Documentación original del autor ---
PRE-ANOTACION NER CON DEEPSEEK  (borrador, NO gold)
===================================================
Propone un primer etiquetado de las 5 entidades sobre cada texto, con OFFSETS
exactos, y lo guarda en formato de PREDICCIONES de Label Studio para que tu
solo CORRIJAS (no anotes desde cero).

Categorias (alineadas con los corpus de España):
  ENFERMEDAD (DisTEMIST) · SINTOMA (SympTEMIST) · PROCEDIMIENTO (MedProcNER)
  MEDICAMENTO (PharmaCoNER) · MORFOLOGIA (CANTEMIST)

ATENCION: esto es un BORRADOR generado por un LLM. NO es el gold standard.
La anotacion final la verifica y corrige un humano con apoyo clinico.

Se ejecuta una vez por modelo (flash y pro) para poder compararlos:
    py preanotar_deepseek.py --modelo deepseek-chat     --out preanot_flash.json
    py preanotar_deepseek.py --modelo deepseek-reasoner --out preanot_pro.json
Luego usar comparar_preanotaciones.py para ver las diferencias.

Requisitos: pip install requests ; set DEEPSEEK_API_KEY=tu-clave
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

ETIQUETAS = ["ENFERMEDAD", "SINTOMA", "PROCEDIMIENTO", "MEDICAMENTO", "MORFOLOGIA"]

SYSTEM_PROMPT = (
    "Eres un anotador experto de entidades clinicas en español. Extraes menciones "
    "textuales EXACTAS y respondes SIEMPRE en JSON valido, sin markdown ni texto extra."
)

# Prompt con definiciones + ejemplos (few-shot) para subir precision
USER_TEMPLATE = """Extrae TODAS las menciones de estas 5 categorias del texto clinico. Devuelve SOLO un JSON con una lista "entidades"; cada item: {{"texto": <mencion exacta copiada del texto>, "categoria": <una de las 5>}}.

CATEGORIAS:
- ENFERMEDAD: diagnostico/patologia/sindrome, incluida localizacion. Ej: "cancer gastrico", "HTA", "tuberculosis", "metastasis hepaticas".
- SINTOMA: sintoma o signo clinico. Ej: "dolor abdominal", "hemoptisis", "perdida de peso", "ictericia".
- PROCEDIMIENTO: examen/prueba/intervencion. Ej: "biopsia", "tomografia", "gastrectomia", "quimioterapia".
- MEDICAMENTO: farmaco/principio activo. Ej: "cisplatino", "5-FU", "erlotinib". NO incluyas la dosis.
- MORFOLOGIA: tipo histologico del tumor. Ej: "adenocarcinoma", "carcinoma de celulas claras", "linfoma difuso de celulas B grandes", "sarcoma".

REGLA CLAVE (morfologia vs enfermedad): el TIPO de celula/tejido del tumor es MORFOLOGIA ("adenocarcinoma"); la enfermedad o su localizacion es ENFERMEDAD ("cancer gastrico"). En "adenocarcinoma gastrico" -> "adenocarcinoma"=MORFOLOGIA y puede coexistir "gastrico"/"cancer gastrico"=ENFERMEDAD.

OTRAS REGLAS:
- Copia la mencion TAL CUAL aparece (misma forma, incluidas siglas: TAC, HTA, 5-FU, TBC).
- No incluyas articulos de borde ("el", "la").
- No anotes instituciones, nombres de persona, fechas, estadios ni valores de laboratorio.
- Si una mencion aparece varias veces, incluyela cada vez.

EJEMPLO:
Texto: "Varon con tos y hemoptisis; la TAC mostro masa pulmonar. Biopsia: adenocarcinoma. Se inicia cisplatino."
Salida: {{"entidades":[{{"texto":"tos","categoria":"SINTOMA"}},{{"texto":"hemoptisis","categoria":"SINTOMA"}},{{"texto":"TAC","categoria":"PROCEDIMIENTO"}},{{"texto":"Biopsia","categoria":"PROCEDIMIENTO"}},{{"texto":"adenocarcinoma","categoria":"MORFOLOGIA"}},{{"texto":"cisplatino","categoria":"MEDICAMENTO"}}]}}

TEXTO A ANOTAR:
\"\"\"
{texto}
\"\"\"
"""


def parse_json(content):
    if not content:
        return None
    content = re.sub(r"^```(json)?", "", content.strip())
    content = re.sub(r"```$", "", content.strip())
    i, j = content.find("{"), content.rfind("}")
    if i != -1 and j != -1:
        candidate = content[i:j + 1]
    else:
        candidate = content
    try:
        return json.loads(candidate)
    except Exception:
        pass
    # RESCATE: si el JSON quedo cortado (p.ej. se acabaron los tokens),
    # recuperar las entidades completas {"texto":...,"categoria":...} que si
    # alcanzaron a generarse, en vez de perder todo el documento.
    ents = re.findall(
        r'\{\s*"texto"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"categoria"\s*:\s*"([^"]*)"\s*\}',
        content)
    if ents:
        return {"entidades": [{"texto": t, "categoria": c} for t, c in ents]}
    return None


def call_deepseek(api_key, model, texto, max_tokens=3000, retries=5, timeout=180):
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
                parsed = parse_json(content)
                if parsed is not None:
                    return parsed, usage, ""
                return None, usage, "respuesta_no_json"
            if r.status_code == 429:
                w = r.headers.get("Retry-After")
                try:
                    w = float(w)
                except (TypeError, ValueError):
                    w = backoff
                time.sleep(min(w, 65)); backoff = min(backoff * 2, 65); continue
            if r.status_code in (500, 502, 503):
                time.sleep(backoff); backoff = min(backoff * 2, 65); continue
            if r.status_code in (401, 402, 403):
                return None, {}, f"HTTP {r.status_code} (clave o saldo): {r.text[:100]}"
            return None, {}, f"HTTP {r.status_code}: {r.text[:100]}"
        except Exception as e:
            if intento == retries:
                return None, {}, f"{type(e).__name__}: {e}"
            time.sleep(backoff); backoff = min(backoff * 2, 65)
    return None, {}, "agotados_reintentos"


def localizar_offsets(texto, entidades):
    """Convierte {texto, categoria} en spans con start/end exactos, buscando
    cada mencion en el texto. Maneja repeticiones avanzando el cursor por
    categoria+mencion. Devuelve lista de spans validos (descarta los no hallados)."""
    spans = []
    usados = {}  # (mencion) -> ultima posicion usada, para repeticiones
    for ent in entidades:
        mencion = (ent.get("texto") or "").strip()
        cat = (ent.get("categoria") or "").strip().upper()
        if not mencion or cat not in ETIQUETAS:
            continue
        desde = usados.get(mencion, 0)
        idx = texto.find(mencion, desde)
        if idx == -1:  # intentar desde el principio (por si el orden no coincide)
            idx = texto.find(mencion)
        if idx == -1:
            continue  # el modelo "invento" o parafraseo: no se puede ubicar -> se descarta
        usados[mencion] = idx + len(mencion)
        spans.append({"start": idx, "end": idx + len(mencion),
                      "text": texto[idx:idx + len(mencion)], "label": cat})
    return spans


def a_label_studio(pid, texto, spans, modelo):
    """Formato de importacion de Label Studio con predicciones."""
    result = []
    for k, s in enumerate(spans):
        result.append({
            "id": f"{pid}_{k}",
            "from_name": "label", "to_name": "text", "type": "labels",
            "value": {"start": s["start"], "end": s["end"],
                      "text": s["text"], "labels": [s["label"]]},
        })
    return {
        "data": {"text": texto, "pid": pid},
        "predictions": [{"model_version": modelo, "result": result}],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--textos", default="./data/set_validacion/textos")
    ap.add_argument("--out", required=True, help="JSON de salida (Label Studio).")
    ap.add_argument("--modelo", default="deepseek-chat",
                    help="deepseek-chat (flash) o deepseek-reasoner (pro).")
    ap.add_argument("--usage-log", default=None)
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--limite", type=int, default=0,
                    help="Procesar solo los primeros N (0 = todos). Util para el piloto.")
    args = ap.parse_args()

    api_key = os.environ.get(ENV_KEY)
    if not api_key:
        sys.exit(f"ERROR: define {ENV_KEY}.")
    if not os.path.isdir(args.textos):
        sys.exit(f"No existe: {args.textos}")

    usage_log = args.usage_log or (os.path.splitext(args.out)[0] + "_usage.csv")
    archivos = sorted(f for f in os.listdir(args.textos) if f.endswith(".txt"))
    if args.limite:
        archivos = archivos[:args.limite]

    # reanudable: si el out ya existe, conservar lo hecho
    hechos = {}
    if os.path.exists(args.out):
        try:
            for tarea in json.load(open(args.out, encoding="utf-8")):
                hechos[tarea["data"]["pid"]] = tarea
        except Exception:
            hechos = {}

    f_log = open(usage_log, "a", encoding="utf-8", newline="")
    logw = csv.DictWriter(f_log, fieldnames=["pid", "prompt_tokens", "completion_tokens",
                                             "n_entidades", "error"])
    if os.path.getsize(usage_log) == 0:
        logw.writeheader()

    print("=" * 60)
    print(f"PRE-ANOTACION NER — modelo {args.modelo}")
    print("=" * 60)
    print(f"  Textos ........ {len(archivos)}  (ya hechos: {len(hechos)})")
    print(f"  Salida ........ {args.out}\n")

    tareas = dict(hechos)
    tot_in = tot_out = 0
    for i, fn in enumerate(archivos, 1):
        pid = fn[:-4]
        if pid in hechos:
            continue
        texto = open(os.path.join(args.textos, fn), encoding="utf-8", errors="ignore").read()
        res, usage, err = call_deepseek(api_key, args.modelo, texto, args.max_tokens)
        ents = (res or {}).get("entidades", []) if res else []
        spans = localizar_offsets(texto, ents) if res else []
        tareas[pid] = a_label_studio(pid, texto, spans, args.modelo)

        # guardar incrementalmente (no perder progreso)
        json.dump(list(tareas.values()), open(args.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

        if usage:
            tot_in += usage.get("prompt_tokens", 0)
            tot_out += usage.get("completion_tokens", 0)
        logw.writerow({"pid": pid,
                       "prompt_tokens": usage.get("prompt_tokens", 0) if usage else 0,
                       "completion_tokens": usage.get("completion_tokens", 0) if usage else 0,
                       "n_entidades": len(spans), "error": err}); f_log.flush()

        print(f"  [{i:3}/{len(archivos)}] {pid}: {len(spans)} entidades"
              + (f"  ERROR={err}" if err else ""))
        time.sleep(args.delay)

    f_log.close()
    costo = tot_in / 1e6 * 0.14 + tot_out / 1e6 * 0.28  # flash; pro es mas caro
    print("\n" + "=" * 60)
    print(f"  Documentos en salida ... {len(tareas)}")
    print(f"  Tokens: in {tot_in:,} / out {tot_out:,}")
    print(f"  Costo aprox (tarifa flash) ... ${costo:.4f}")
    print(f"  (si usaste el pro, multiplica ~x3 con descuento o ~x12 sin el)")
    print(f"\n  Salida Label Studio: {os.path.abspath(args.out)}")
    print(f"  Log de tokens: {os.path.abspath(usage_log)}")
    print("\n  RECORDATORIO: es un BORRADOR. Importalo en Label Studio como")
    print("  predicciones y CORRIGE a mano. No es el gold standard.")


if __name__ == "__main__":
    main()
