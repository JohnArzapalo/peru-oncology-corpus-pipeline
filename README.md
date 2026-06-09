# Corpus clínico oncológico peruano — Pipeline de construcción

Pipeline reproducible que construye un corpus de **casos clínicos oncológicos en español**
a partir de artículos de acceso abierto de **SciELO Perú**, con pre-anotación de cinco
tipos de entidades clínicas para tareas de Reconocimiento de Entidades Nombradas (NER).

> **Código** liberado bajo licencia **MIT** (este repositorio).
> El **corpus anotado** se libera por separado bajo **CC BY 4.0**.
> Este repositorio contiene **solo código**: no incluye textos, CSV de datos ni anotaciones.

## Flujo del pipeline

```
        SciELO Perú (API ArticleMeta)
                  │
   01_harvest_scielo        ──►  candidatos.csv + textos/ (crudos)
                  │
   02_build_catalog         ──►  descripcion_textos.txt (revista, tipo, secciones)
                  │
   03_filter_candidates     ──►  seleccionados/        (descarta veterinaria / inglés / sin estructura)
                  │
   04_clean_corpus          ──►  textos_limpios_final/ (reglas + DeepSeek; solo recorta, no reescribe)
                  │
   05_sample_validation_set ──►  set_validacion/       (muestreo estratificado por revista, seed=42)
                  │
   06_qc_classify           ──►  qc_deepseek.csv       (control de calidad / triage con DeepSeek)
                  │
   07_filter_approved       ──►  textos_aprobados/     (separa aprobados de marcados)
                  │
   08_preannotate_ner       ──►  preanot.json          (borrador NER, formato Label Studio)
                  │
   (09) Corrección humana en Label Studio   ──►  gold standard
                  │
   (10) Publicación del corpus              ──►  CC BY 4.0 (repositorio aparte)
```

## Entidades anotadas

`ENFERMEDAD` · `SINTOMA` · `PROCEDIMIENTO` · `MEDICAMENTO` · `MORFOLOGIA`

Alineadas con los corpus clínicos en español de referencia: DisTEMIST, SympTEMIST,
MedProcNER, PharmaCoNER y CANTEMIST.

## Requisitos

- Python 3.9 o superior
- Dependencias: `pip install -r requirements.txt`
- Para las etapas con LLM (04, 06, 08): una clave de la API de DeepSeek en la variable
  de entorno `DEEPSEEK_API_KEY` (copia `.env.example` a `.env` y complétala).

## Instalación

```bash
git clone https://github.com/<usuario>/peru-oncology-corpus-pipeline.git
cd peru-oncology-corpus-pipeline
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Configura la clave de DeepSeek (solo para las etapas con LLM):

```bash
cp .env.example .env               # Windows: copy .env.example .env
# edita .env y pon tu clave; o expórtala en la sesión:
export DEEPSEEK_API_KEY="tu-clave" # Windows (PowerShell): $env:DEEPSEEK_API_KEY="tu-clave"
```

## Ejecución por etapas

Los scripts están numerados y se ejecutan en orden. Todas las rutas usan `./data` por
defecto (esa carpeta está en `.gitignore`).

```bash
# 1) Recolección desde SciELO Perú
python 01_harvest_scielo.py --by-year --from-year 1997 --until-year 2026 --out ./data
#    -> ./data/candidatos.csv y ./data/textos/*.txt

# 2) Catálogo descriptivo del corpus crudo
python 02_build_catalog.py --src ./data/textos --out ./data/analisis_textos
#    -> ./data/analisis_textos/descripcion_textos.txt

# 3) Filtro previo (descarta veterinaria / inglés / sin estructura)
python 03_filter_candidates.py --textos ./data/textos --catalogo ./data/analisis_textos/descripcion_textos.txt --csv ./data/candidatos.csv --out ./data/seleccionados
#    -> ./data/seleccionados/ + filtro_reporte.csv

# 4) Limpieza (reglas + DeepSeek). Requiere DEEPSEEK_API_KEY; usa --solo-reglas para probar sin LLM
python 04_clean_corpus.py --in ./data/seleccionados --out ./data/textos_limpios_final --reporte ./data/limpieza_final_reporte.csv
#    -> ./data/textos_limpios_final/

# 5) Muestreo del set de validación (estratificado por revista, reproducible)
python 05_sample_validation_set.py --candidatos ./data/candidatos.csv --textos ./data/textos_limpios_final --n 120 --seed 42 --out ./data/set_validacion
#    -> ./data/set_validacion/ (seleccion_validacion.csv, reserva.csv, textos/)

# 6) Control de calidad con DeepSeek. Requiere DEEPSEEK_API_KEY
python 06_qc_classify.py --textos ./data/set_validacion/textos --out ./data/set_validacion/qc_deepseek.csv
#    -> ./data/set_validacion/qc_deepseek.csv

# 7) Filtrar los aprobados del QC
python 07_filter_approved.py --qc ./data/set_validacion/qc_deepseek.csv --textos ./data/set_validacion/textos --out ./data/set_validacion/textos_aprobados
#    -> ./data/set_validacion/textos_aprobados/ + excluidos_qc.csv

# 8) Pre-anotación NER (borrador). Requiere DEEPSEEK_API_KEY
python 08_preannotate_ner.py --textos ./data/set_validacion/textos_aprobados --out ./data/preanot.json
#    -> ./data/preanot.json (importable en Label Studio)
```

Cada script acepta `-h/--help` para ver todos sus argumentos.

## Nota sobre datos, LLM y ética

- Este repositorio contiene **únicamente código**. Los textos provienen de artículos de
  acceso abierto de SciELO; el corpus derivado y sus anotaciones se distribuyen aparte.
- Las etapas con LLM son **asistencia, no decisión final**: la limpieza solo **recorta**
  (el texto conservado es subcadena literal del original) y la anotación es un **borrador**.
  El *gold standard* lo revisa y corrige un anotador humano con apoyo clínico.
- Ninguna credencial se almacena en el código: la clave se lee de `DEEPSEEK_API_KEY`.

## Cómo citar

> Arzapalo Arana, John Manuel. *Corpus clínico oncológico peruano: pipeline de construcción*.
> 2026. Tesis de licenciatura, Pontificia Universidad Católica del Perú (PUCP).

## Licencia

- **Código:** MIT — ver el archivo [`LICENSE`](LICENSE).
- **Corpus anotado:** CC BY 4.0 (publicado en un repositorio de datos aparte).
