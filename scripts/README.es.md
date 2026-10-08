# Carpeta `scripts`

Esta carpeta contiene **scripts auxiliares** del monorepo: automatizaciones de desarrollo, utilidades de mantenimiento, tareas repetitivas (setup, lint, migraciones, generación de datos, etc.) y tooling interno.

- **Propósito principal**: agrupar herramientas de soporte que no pertenecen a una app/agente/pipeline específico, pero facilitan el trabajo del equipo.
- **Recomendación**: documenta cada script (qué hace, parámetros, requisitos, ejemplos de uso) y procura que sean reproducibles (y seguros) en distintos entornos.

## Análisis local de incidencias

Requiere Python 3.10 o posterior y usa únicamente la biblioteca estándar. Desde la raíz del monorepo:

```bash
python3 scripts/analyze.py incidents-trackflow.csv
```

Valida los campos y catálogos definidos en `CONTEXT.md`, excluye del análisis los registros inválidos, muestra sus motivos sin exponer emails y solicita confirmación antes de exportar `results.csv`. Cuando el archivo se llama `incidents-trackflow.csv`, contrasta los resultados con las cifras de referencia del contexto y detiene la ejecución si no coinciden.
