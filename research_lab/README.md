# Adrian Quant Lab

Este modulo es el primer MVP de una fabrica de investigacion cuantitativa.

La idea no es crear un bot nuevo rapidamente. La idea es crear un sistema que pueda responder:

> Esta idea tiene edge real, o solo parece buena en un backtest?

## Arquitectura

```text
Freqtrade OHLCV feather
  -> Parquet warehouse
  -> Data-quality gate
  -> Feature generator
  -> Edge engine
  -> Resultados versionados por run_id
  -> Streamlit dashboard
  -> Promotion gate dry-run
```

## Componentes

### `warehouse.py`

Convierte los historiales de Freqtrade a Parquet y genera features reutilizables.

### `indicators.py`

Calcula indicadores base:

- RSI
- ATR
- EMAs
- Bollinger Bands
- Donchian channels
- session VWAP
- volumen normalizado
- volatilidad compuesta
- regimen continuo

### `edge_engine.py`

Ejecuta edges simples y comparables:

- Donchian Breakout
- EMA Trend
- RSI Mean Reversion
- Volatility Expansion

No busca una estrategia perfecta. Busca comparar hipotesis con reglas iguales de costes, slippage, stop y toma de ganancias.

### `data_quality.py`

Valida el warehouse antes de investigar: fechas ordenadas, duplicados, fechas futuras,
OHLC valido, precios positivos, volumen no negativo, gaps temporales, freshness y ultima
vela cerrada esperada. Tambien comprueba que el resample `1h -> 4h` use
`open=first`, `high=max`, `low=min`, `close=last`, `volume=sum`.

Si falla con `--fail-on-error`, el pipeline se detiene y no hay promocion.

### `run_manager.py`

Crea un `run_id` unico por corrida y guarda manifiestos reproducibles en:

```text
research_lab/storage/results/runs/<run_id>/run_manifest.json
research_lab/storage/results/runs/<run_id>/edge_summary.parquet
research_lab/storage/results/runs/<run_id>/edge_trades.parquet
research_lab/storage/results/runs/<run_id>/walk_forward_summary.parquet
research_lab/storage/results/runs/<run_id>/data_quality_report.json
```

Tambien mantiene `research_lab/storage/results/latest/` como copia de la ultima corrida.

### `dashboard.py`

GUI personal en Streamlit para filtrar resultados por edge, par y timeframe.

## Uso local

Activar entorno:

```bash
source .venv/bin/activate
```

En el homeserver usa el entorno aislado:

```bash
cd /home/adrian/freqtrade
source .venv-lab/bin/activate
```

Instalar dependencias reproducibles:

```bash
python -m pip install -r research_lab/requirements.txt
```

Regla importante: ejecuta siempre como paquete desde la raíz del proyecto:

```bash
python -m research_lab.edge_engine
```

No uses:

```bash
python research_lab/edge_engine.py
```

Ese modo rompe imports como `from research_lab.warehouse import ...`.

Migrar OHLCV a Parquet:

```bash
python -m research_lab.warehouse migrate --source user_data/data/kucoin
```

Generar features para `1h`:

```bash
python -m research_lab.warehouse features --timeframes 1h
```

Crear `4h` desde `1h`:

```bash
python -m research_lab.warehouse resample --source-timeframe 1h --target-timeframe 4h
```

Generar features para `1h` y `4h`:

```bash
python -m research_lab.warehouse features --timeframes 1h,4h
```

Ejecutar edges base:

```bash
python -m research_lab.edge_engine --timeframes 1h,4h --walk-forward
```

Smoke test mínimo:

```bash
ROOT_DIR=/home/adrian/freqtrade \
VENV_DIR=.venv-lab \
PAIR=BTC/USDT \
TIMEFRAME=1h \
bash research_lab/scripts/smoke_test.sh
```

Debe crear y verificar:

- `ohlcv_manifest.parquet`
- `features_manifest.parquet`
- `features_all.parquet`
- `results/edge_summary.parquet`
- `lab.duckdb`

Abrir dashboard:

```bash
streamlit run research_lab/dashboard.py --server.address 0.0.0.0 --server.port 8501
```

Desde otra maquina conectada por Tailscale:

```text
http://homeserver:8501
```

## Uso recomendado en homeserver

Para tu servidor de 4 GB de RAM:

- Parquet es correcto.
- DuckDB es correcto.
- Streamlit es suficientemente liviano.
- No hace falta PostgreSQL al inicio.
- No hace falta Airflow al inicio.
- Cron o systemd timers son suficientes.

Pipeline nocturno ideal:

```text
Actualizar datos
Generar features
Ejecutar edges
Actualizar dashboard
Enviar resumen Telegram
```

## Dashboard personal

El dashboard corre con Streamlit:

```bash
streamlit run research_lab/dashboard.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false
```

Si el servidor esta en Tailscale, se puede abrir desde tus laptops o PC con:

```text
http://homeserver:8501
```

O usando la IP Tailscale del servidor:

```text
http://IP_TAILSCALE:8501
```

## Pipeline automatico

El script:

```text
research_lab/scripts/run_pipeline.sh
```

ejecuta:

1. migracion a Parquet
2. resample `1h -> 4h`
3. gate de calidad de datos
4. generacion de features
5. ejecucion de edges base
6. temporal OOS con parametros fijos
7. snapshot versionado en `results/runs/<run_id>/`
8. manifest final `success` o `failed`
9. promocion a paper solo si la corrida termino bien y el gate de datos paso

Uso manual:

```bash
ROOT_DIR=/home/adrian/freqtrade \
TIMEFRAMES=1h \
PAIRS=BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT \
bash research_lab/scripts/run_pipeline.sh
```

## Robustez

El motor ahora registra:

- `entry_signals`
- `exit_signals`
- `expected_maker_fill_rate`
- `signal_to_trade_rate`
- `activity_state`
- `stress_pf_2bps`, `stress_pf_5bps`, `stress_pf_10bps`
- Monte Carlo por edge agregado
- drawdown temporal por barra
- `tested_combinations` para anti data-snooping
- `score` unico
- `rules_version`

Importante: el archivo `walk_forward_summary.parquet` contiene `validation_type =
temporal_oos_fixed_params`. Eso no es WFO con optimizacion in-sample. Es estabilidad
temporal fuera de muestra con parametros fijos.

## Reproducibilidad

Cada corrida nocturna crea un `run_id` como:

```text
20260618T033000Z-a8c41f2-1ab2cd
```

Ese identificador se comparte entre manifiestos, reporte de datos, trades, WFO y decision
de promocion. La regla operativa es simple: si no existe `run_manifest.json` con
`status=success`, ese resultado no se usa para promover nada.

## Playbook

La base de conocimiento vive en:

```text
quant_playbook/
```

Cada hipótesis debe documentarse con `TEMPLATE_EDGE.md`. El objetivo no es acumular bots,
sino responder preguntas falsables.

## Promocion a paper

Existe un gate seguro:

```bash
python -m research_lab.promotion \
  --root /home/adrian/freqtrade \
  --storage research_lab/storage \
  --rules research_lab/config/promotion_rules.json \
  --run-id "$(python -m research_lab.run_manager --storage research_lab/storage latest-id)"
```

El servicio systemd puede correrlo con `--execute`, pero solo genera/levanta un contenedor
Freqtrade `dry_run` cuando se cumplen reglas estrictas:

- `run_manifest.status == success`;
- `data_quality.status == passed`;
- minimo de dias observado,
- score minimo,
- PF minimo,
- fold rate positivo,
- drawdown permitido,
- dry-run only.

Estado actual: `no_candidate`. No hay contenedor paper promovido automaticamente todavia.

## systemd

Hay plantillas listas en:

```text
research_lab/systemd/
```

Archivos:

- `adrian-quant-lab.service`: mantiene la GUI encendida.
- `adrian-quant-pipeline.service`: corre el pipeline una vez.
- `adrian-quant-pipeline.timer`: ejecuta el pipeline cada noche a las 03:30.
- `adrian-quant-promotion.service`: evalua manualmente si el ultimo run puede pasar a paper.
- `adrian-quant-promotion.timer`: legado; se recomienda dejarlo apagado porque el pipeline ya ejecuta promocion al final.

Instalacion sugerida en el homeserver:

```bash
sudo cp research_lab/systemd/adrian-quant-*.service /etc/systemd/system/
sudo cp research_lab/systemd/adrian-quant-pipeline.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adrian-quant-lab.service
sudo systemctl enable --now adrian-quant-pipeline.timer
```

Antes de copiar, confirma que la ruta real del proyecto sea:

```text
/home/adrian/freqtrade
```

Si esta en otra ruta, edita los `WorkingDirectory`, `PATH` y `ExecStart`.

## Estado actual del despliegue

El homeserver ya tiene:

- `research_lab` extraido en `/home/adrian/freqtrade/research_lab`
- entorno Python en `/home/adrian/freqtrade/.venv-lab`
- dashboard activo con `adrian-quant-lab.service`
- pipeline nocturno activo con `adrian-quant-pipeline.timer`
- proxima ejecucion diaria: `03:30`

Comprobar estado:

```bash
systemctl status adrian-quant-lab.service
systemctl status adrian-quant-pipeline.timer
```

Ver logs:

```bash
journalctl -u adrian-quant-lab.service -f
journalctl -u adrian-quant-pipeline.service -n 100 --no-pager
```

## Regla de decision

Un edge no pasa solo por tener retorno positivo.

Debe mirar como minimo:

- cantidad de trades
- Profit Factor
- expectancy
- drawdown
- estabilidad por par
- estabilidad por timeframe
- sensibilidad a costes
- walk-forward
- Monte Carlo

El objetivo inicial no es encontrar el santo grial.

El objetivo inicial es encontrar una estrategia aburrida con PF aproximado de `1.2` a `1.4`, suficientes trades y resultados razonables fuera de muestra.
