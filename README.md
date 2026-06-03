# Tanita BC-601 Dashboard

Aplicación web en Streamlit para cargar `DATAX.CSV` de una báscula Tanita BC-601, limpiar los datos y visualizar su evolución con un formato listo para impresión clínica.

## Funcionalidades

- Carga de `DATAX.CSV`
- Limpieza automática de fechas y columnas numéricas
- KPI cards con últimos valores de peso, grasa, masa muscular y edad metabólica
- Gráficas interactivas con Plotly
- Análisis segmental comparativo cuando el CSV incluye brazos y piernas
- Modo impresión en blanco y negro con `Ctrl+P`
- Exportación de la tabla procesada a Excel y PDF

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ejecución

```bash
streamlit run app.py
```

La aplicación detecta automáticamente columnas habituales del CSV Tanita aunque el nombre exacto pueda variar ligeramente.
