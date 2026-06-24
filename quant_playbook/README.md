# Quant Playbook

Este directorio es la base de conocimiento del laboratorio.

La regla es simple: cada hipótesis investigada debe quedar documentada con el mismo formato. No buscamos "bots"; buscamos comportamientos del mercado que sobrevivan años, pares, costes y validación fuera de muestra.

## Formato

Copia `TEMPLATE_EDGE.md` para cada hipótesis nueva:

```text
EDGE001_Donchian.md
EDGE002_EMA_Trend.md
EDGE003_Volatility_Expansion.md
```

## Estados

- `rechazado`: no hay evidencia suficiente.
- `watch`: merece seguimiento, pero no paper.
- `candidate`: pasa el primer filtro cuantitativo.
- `paper`: está en dry-run, sin dinero real.
- `retired`: funcionó antes, pero decayó.

## Regla

Ninguna estrategia va a paper por narrativa. Tiene que pasar:

- métricas agregadas,
- estabilidad temporal,
- stress de costes,
- Monte Carlo,
- mínimo de días observada,
- revisión humana.
