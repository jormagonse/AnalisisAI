# Organizador de PDFs por evaluaciones

`organizar_pdfs_por_csv.py` relaciona los PDFs judiciales con los registros de
`evaluaciones_completo.csv` usando CI, número de expediente, nombre, carátula,
juzgado, delito y fecha.

La salida mantiene el orden del CSV:

```text
organizados_por_evaluacion/
  001_<caratula>/
    registro_0001_id_<id>/
      fuertes/
      parciales/
      registro.json
```

Los PDFs originales no se modifican. Los documentos repetidos en distintas
carpetas se deduplican por SHA-256 y sus rutas de origen quedan registradas.

## Ejecución

Instalar la dependencia:

```powershell
python -m pip install pypdf
```

Ejemplo:

```powershell
python .\organizar_pdfs_por_csv.py `
  --csv "C:\Users\jorma\source\repos\CSJ\ScrapreAI\evaluaciones_completo.csv" `
  --pdf-dir "C:\Users\jorma\source\repos\CSJ\ScrapreAI" `
  --out-dir "C:\Users\jorma\source\repos\CSJ\ScrapreAI\organizados_por_evaluacion"
```

La salida incluye `resumen.json` y `coincidencias.csv`. Los PDFs escaneados sin
capa de texto quedan registrados, pero requieren OCR para mejorar su detección.
