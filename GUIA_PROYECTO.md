# Adrian Quant Lab — Guía técnica completa y de uso

> Documento de referencia técnica del laboratorio cuantitativo. Explica **qué hace cada
> pieza, cómo lo hace, cómo se conecta y corre en el homeserver**, y una **guía de uso**
> local y remota. Complementa a `adrian.md` (que es la guía conceptual de principiante).
>
> Última actualización: **2026-06-17** (sprint de frescura/eventos + account simulator v1).
> Rutas: local `/home/perdomopro/Desktop/freqtrade`, homeserver `/home/adrian/freqtrade`.

---

## 1. Qué es este proyecto

Es un fork de **Freqtrade** usado como **laboratorio de investigación**, no como bot de
producción. El objetivo no es operar rápido, sino tener un proceso repetible que responda:

> ¿Esta idea de trading tiene edge real, o solo se ve bien en un backtest?

El proyecto tiene dos capas:

1. **Freqtrade base** — el motor de trading/backtesting estándar (sin tocar su núcleo).
   Aquí vive la estrategia experimental `user_data/strategies/QuantScalper_SXD.py` (ya
   rechazada) y los scripts de análisis de raíz.
2. **`research_lab/`** — el laboratorio propio: convierte datos, calcula features, corre
   "edges" comparables, hace validación temporal fuera de muestra, stress/Monte Carlo,
   score objetivo, **simula una cuenta real** y los muestra en un dashboard web.

---

## 2. Arquitectura y flujo de datos

```text
freqtrade download-data (1h, KuCoin)   user_data/data/kucoin/*.feather
        │  warehouse migrate
        ▼
Parquet warehouse                      research_lab/storage/ohlcv/<exchange>/<PAIR>/<tf>.parquet
        │  warehouse resample (1h → 4h, solo buckets completos de 4 velas)
        ▼
OHLCV multi-timeframe (1h, 4h)
        │  data_quality --fail-on-error (gate estricto de frescura + integridad)
        ▼
OHLCV validado
        │  warehouse features  (indicators.add_core_features)
        ▼
Feature store                          research_lab/storage/features/.../<tf>.parquet
        │  edge_engine (run_edges + walk_forward + signal_events)
        ▼
Resultados versionados                 research_lab/storage/results/runs/<run_id>/
        │  account_simulator (cuenta real desde edge_signals)
        ▼
Métricas de cuenta (paralelas)         account_summary/trades/equity/rejections.parquet
        │  run_manager snapshot → refresh_duckdb → vistas SQL
        ▼
DuckDB (lab.duckdb)  +  Streamlit dashboard (puerto 8501)
        │
        ▼
Promotion gate (bloqueado hasta parity test)
```

Todo se materializa en **Parquet** (columnar, comprimido, rápido) y se indexa en
**DuckDB** como vistas SQL. El dashboard lee los Parquet directamente. No hay base de
datos pesada (PostgreSQL/Airflow) porque el homeserver tiene solo ~4 GB de RAM.

### Estructura de archivos

```text
research_lab/
├── __init__.py
├── warehouse.py          # ETL: feather → parquet, resample, features, índice DuckDB (escritura atómica)
├── data_quality.py       # gate estricto: frescura por lag_bars, NaN/inf, orden, resample robusto
├── run_manager.py        # run_id, manifests reproducibles y snapshots por corrida
├── indicators.py         # cálculo de todos los features
├── edge_engine.py        # backtest de edges (edges-v2) + walk-forward + eventos de señal + veredicto
├── account_simulator.py  # NUEVO: simula una cuenta real (cash, equity, exposición, posiciones)
├── dashboard.py          # GUI Streamlit
├── promotion.py          # gate seguro hacia Freqtrade dry-run/paper (bloqueado por parity test)
├── config.py             # helpers de configuración
├── config/
│   ├── decision_rules.json    # umbrales del veredicto del lab (lab-rules-v1)
│   ├── promotion_rules.json   # reglas de promoción a paper (promotion-rules-v1)
│   └── account_rules.json     # NUEVO: reglas de la cuenta simulada (account-rules-v1)
├── requirements.txt      # dependencias pinneadas del lab
├── README.md             # resumen del módulo
├── audit_integrity/      # carpeta para reportes de auditoría/integridad (placeholder)
├── scripts/
│   ├── run_pipeline.sh   # orquesta el pipeline completo (lo usa systemd)
│   └── smoke_test.sh     # verificación rápida de que el entorno produce artefactos
├── systemd/
│   ├── adrian-quant-lab.service        # mantiene el dashboard encendido
│   ├── adrian-quant-pipeline.service   # corre el pipeline una vez (con descarga de datos)
│   ├── adrian-quant-pipeline.timer     # dispara el pipeline cada noche 03:30
│   ├── adrian-quant-promotion.service  # reevalúa promoción del último run_id (manual)
│   └── adrian-quant-promotion.timer    # LEGADO: debe quedar inactivo
└── storage/
    ├── ohlcv/<exchange>/<PAIR>/<tf>.parquet      # precios crudos
    ├── features/<exchange>/<PAIR>/<tf>.parquet   # precios + indicadores
    ├── features_all.parquet                      # todos los features concatenados
    ├── ohlcv_manifest.parquet                    # inventario de OHLCV
    ├── features_manifest.parquet                 # inventario de features
    ├── lab.duckdb                                # vistas SQL
    └── results/
        ├── edge_summary.parquet          # 1 fila por (edge,par,tf) con métricas + veredicto
        ├── edge_trades.parquet           # cada trade simulado por el edge engine
        ├── edge_signals.parquet          # NUEVO: eventos de entrada/salida por edge/par/tf
        ├── walk_forward_summary.parquet  # métricas por fold fuera de muestra
        ├── season_summary.parquet        # liga por año
        ├── run_history.parquet           # snapshots por corrida
        ├── data_quality_report.json      # reporte del gate de calidad de la corrida
        ├── account_summary.parquet       # NUEVO: métricas de la cuenta por edge/timeframe
        ├── account_trades.parquet        # NUEVO: trades ejecutados por la cuenta simulada
        ├── account_equity.parquet        # NUEVO: curva de equity de la cuenta
        ├── account_rejections.parquet    # NUEVO: señales rechazadas y por qué
        ├── promotion_decision.json       # última decisión del gate de paper
        ├── deployment_manifest.json      # estado del despliegue del contenedor paper
        ├── latest/                       # copia de la última corrida
        └── runs/<run_id>/                # evidencia completa de cada corrida histórica

# Scripts de raíz (análisis sobre Freqtrade, fuera de research_lab):
quant_validation.py          # valida trades exportados de un backtest de Freqtrade
research_edge_study.py       # estudio de edge de una hipótesis antes de hacerla estrategia
config.kucoin.example.json   # config de Freqtrade (KuCoin spot, dry-run)

# Documentación de cada corrida:
run_notes/<fecha>_<tema>.md  # bitácora por sprint/corrida (qué se hizo y por qué)
quant_playbook/EDGE*.md      # ficha de cada edge candidato
```

---

## 3. Componentes en detalle

### 3.1 `warehouse.py` — el almacén de datos

CLI: `python -m research_lab.warehouse [--storage DIR] [--exchange kucoin] <comando>`

| Comando | Qué hace |
|---|---|
| `migrate --source DIR` | Lee los `.feather` de Freqtrade, ordena por fecha, deduplica, y escribe un Parquet por par/timeframe en `storage/ohlcv/...`. Detecta par y timeframe con el regex `BASE_QUOTE-<tf>.feather`. Escribe `ohlcv_manifest.parquet`. |
| `resample --source-timeframe 1h --target-timeframe 4h [--pairs ...]` | Re-muestrea con agregación OHLCV correcta (`open=first, high=max, low=min, close=last, volume=sum`). **Solo materializa buckets con exactamente las velas fuente esperadas** (4 para 1h→4h): calcula `source_bars` y **descarta el último bucket parcial**, no lo tolera. |
| `features [--pairs ...] [--timeframes ...]` | Para cada Parquet OHLCV llama a `add_core_features()` y guarda en `storage/features/...`. Reconstruye `features_all.parquet` y `features_manifest.parquet`. |
| `index` | Solo refresca las vistas DuckDB. |

Detalles importantes:
- **Escritura atómica**: los Parquet grandes se escriben primero como temporal y luego se
  renombran (`write_parquet_atomic`). Esto evita que DuckDB, el dashboard o la promoción
  lean un archivo a medio escribir.
- **Manifiestos**: tablas inventario con `exchange, pair, timeframe, rows, start, end, path`,
  deduplicadas por `(exchange, pair, timeframe)` quedándose con la última versión.
- **`refresh_duckdb()`** crea/reemplaza vistas en `lab.duckdb` que apuntan vía
  `read_parquet(...)` a los manifiestos y resultados. Vistas creadas (si existe el Parquet):
  `ohlcv_manifest`, `features_manifest`, `features`, `edge_summary`, `edge_trades`,
  `edge_signals`, `walk_forward_summary`, `season_summary`, `run_history`,
  `account_summary`, `account_trades`, `account_equity`, `account_rejections`.

### 3.2 `indicators.py` — generador de features

Función central: `add_core_features(df)` (versión `features-v2`). A partir de OHLCV calcula
(todo causal, con `min_periods` para no inventar valores al inicio):

- **Retornos**: `ret_1, ret_3, ret_6, ret_12, ret_24, ret_48`.
- **Tendencia**: `ema_{10,20,50,100,200}` y su distancia relativa `ema_N_dist`;
  pendientes `ema_20_slope` (5 barras) y `ema_50_slope` (10 barras).
- **Momentum**: `rsi_14` (RSI de Wilder vía EWMA).
- **Volatilidad**: `atr_14`, `atr_pct_14`, `realized_vol_20`, `parkinson_vol_20`,
  `ewma_vol_20`, y `composite_vol` = mediana de las cuatro (más robusta que una sola).
- **Bandas de Bollinger**: `bb_lower/mid/upper_20`, ancho `bb_width_20`, posición
  relativa `bb_pos_20` (0 = banda baja, 1 = banda alta).
- **Donchian**: `donchian_high_20`, `donchian_low_10`, `donchian_high_55`, `donchian_low_20`.
- **Volumen**: `volume_z_50` (z-score de log-volumen), `volume_pct_100` (percentil rolling).
- **VWAP de sesión**: `session_vwap` (reinicia cada día) y `session_vwap_dist`.
- **Régimen continuo** (no discreto): `bull_prob`, `bear_prob`, `range_prob` (suman ~1),
  `regime_score` (−1 a +1) y `vol_rank_200` (percentil de volatilidad).

La idea: calcular indicadores **una sola vez** y reutilizarlos en todos los edges.

### 3.3 `data_quality.py` — gate estricto de calidad

CLI: `python -m research_lab.data_quality --storage DIR --timeframes 1h,4h --run-id <id> [--fail-on-error] [--max-lag-bars N]`

Antes era laxo (frescura de hasta 96 h como simple warning). **Ahora es un gate real** que
detiene el pipeline con `--fail-on-error`. Valida por par y timeframe:

- **Columnas requeridas**: `date, open, high, low, close, volume`.
- **Orden temporal crudo** *antes* de ordenar (`dates_not_sorted`) y `invalid_dates`.
- **No finitos**: NaN, `inf`, `-inf` en OHLCV → `nonfinite_ohlcv` (falla).
- **Velas incompletas**: cualquier vela posterior a `expected_last_closed_open()` →
  `incomplete_candles_present` (falla).
- **Frescura por lag, no por horas**: calcula `lag_bars` (cuántas velas de atraso hay
  contra la última esperada por reloj) y falla con `stale_data` si supera `--max-lag-bars`
  (**default `1`**). `--max-stale-hours` se mantiene por compatibilidad de CLI pero ya no
  es el gate principal.
- OHLC válido, precios positivos, volumen no negativo, mínimo de filas, gaps temporales.

Campos clave del reporte (`data_quality_report.json`):

```text
expected_last_complete_candle   # última vela que debería existir según el reloj
actual_last_available_candle    # última vela que realmente hay en el Parquet
lag_bars / max_lag_bars         # atraso en velas
allowed_lag_bars                # tolerancia (default 1)
status: passed | failed
```

**Validación del resample `1h → 4h`**: `validate_resample()` usa merge **externo** con
indicador (no interno), verifica conteo de filas, cobertura de fechas, detecta target
vacío/parcial (`missing_target_resample`) y evita que `np.allclose([], [])` pase
silenciosamente. Reconstruye el esperado con la misma regla de **solo buckets completos**.

### 3.4 `run_manager.py` — reproducibilidad por corrida

CLI: `python -m research_lab.run_manager --root DIR --storage DIR [--run-id ID] <comando>`
con comandos `new-id`, `start`, `complete --status ...`, `snapshot`, `latest-id`.

Cada corrida genera un `run_id`:

```text
YYYYMMDDTHHMMSSZ-<git_commit_o_nogit>-<uuid6>
```

El `run_manifest.json` registra estado (`running|success|failed`), tiempos, versión de
Python/Freqtrade/plataforma, hashes de requirements/datos/features, semilla fija, y los
campos de frescura (`expected_last_complete_candle`, `actual_last_available_candle`,
`max_lag_bars`, `allowed_lag_bars`; `last_complete_candle` queda como **alias legacy del
dato real**). También versiona todo:

```text
features_version        = features-v2
edge_registry_version   = edges-v2
decision_rules_version  = lab-rules-v1
promotion_rules_version = promotion-rules-v1
account_rules_version   = account-rules-v1
account_simulator_version = account-sim-v1
```

`snapshot` copia a `results/runs/<run_id>/` y `results/latest/` la lista `RUN_FILES`:
`edge_summary`, `edge_trades`, `edge_signals`, `walk_forward_summary`, `season_summary`,
`run_history`, `data_quality_report.json`, y los cuatro `account_*.parquet`. **Ya no copia
una `promotion_decision.json` vieja**: esa decisión solo la escribe el módulo de promoción.

### 3.5 `edge_engine.py` — el motor de edges (`edges-v2`)

Define 4 "edges" (controles de laboratorio, no estrategias finales). Cada uno es
`EdgeSpec(name, description, max_hold, version="edges-v2")`:

| Edge | Entrada (long) | Salida | Max hold |
|---|---|---|---|
| `donchian_20_10` | cierre rompe máximo previo de 20 barras **y** volumen percentil > 0.35 | cierre < mínimo previo de 10 barras | 120 |
| `ema_trend_20_50_100` | close > EMA20 > EMA50 > EMA100, pendiente EMA50 > 0, `bear_prob` < 0.35 | close < EMA50 **o** `bear_prob` > 0.55 | 120 |
| `rsi_mean_reversion_25` | RSI<25, `bb_pos`<0.15, `bear_prob`<0.45, `volume_z`>−0.75 | RSI>52 **o** close>banda media **o** `bear_prob`>0.60 | 48 |
| `volatility_expansion_20` | rompe máximo 20 + close>VWAP + vol_ratio>1.15 + volumen pct>0.60 + `bear_prob`<0.40 | **revisada**: close < EMA20 **o** close < `donchian_low_20` previo | 96 |

> Nota: la salida de `volatility_expansion_20` se cambió. Antes usaba `prev_high_55`, lo que
> provocaba salidas inmediatas por estar debajo de un máximo de 55 barras; ahora usa
> `prev_low_20` junto con `close < ema_20`.

**Mecánica del backtest (`backtest_edge`)** — realista y sin look-ahead:
- La señal se evalúa en la barra `i`; la **entrada se ejecuta al `open` de la barra
  siguiente** (`i+1`) con slippage: `entry = open * (1 + slippage)`.
- Riesgo por trade = `composite_vol` en el momento de la señal:
  - **Stop**: `entry * (1 − clip(vol*stop_mult, 1%, 12%))`
  - **Take-profit**: `entry * (1 + clip(vol*tp_mult, 1.5%, 25%))`
- En cada barra comprueba en orden: ¿stop? ¿TP? ¿señal de salida o `max_hold`? Razones:
  `stop`, `take_profit`, `signal`, `max_hold`.
- **Stop con gap**: ya no asume fill en `stop_price`. Para long usa `min(stop_price, open_i)`,
  es decir, si la vela abre peor que el stop, se ejecuta al `open` real.
- **Coste**: `net_return = gross_return − 2*fee`.

**Fechas de salida corregidas (`edges-v2`)**:
- Salidas por `signal` o `max_hold` → `exit_date = dates[i+1]` (la vela cuyo `open` se usa).
- Salidas por `stop` o `take_profit` → `exit_date = dates[i]` (fill dentro de esa vela).
- `bars_held = exit_index − entry_index`.

**Metadatos por trade** (para auditoría y para el account simulator): `signal_date`,
`signal_index`, `entry_index`, `exit_index`, `stop_price_initial`,
`take_profit_price_initial`, `stop_distance`, `take_profit_distance`,
`composite_vol_at_signal`, fees, slippage, `max_hold`, `edge_version`.

**Eventos de señal (`signal_events`)**: genera `edge_signals.parquet` con cada evento de
`entry`/`exit` por edge, par y timeframe. **Esta es la fuente del account simulator**, no
`edge_trades.parquet`.

**Métricas (`summarize_trades`)**: `trades, win_rate, expectancy, profit_factor,
total_return, max_drawdown` (temporal por barra), `trade_max_drawdown`, duración de
drawdown, `sharpe_trade`, `avg_win`, `avg_loss`, telemetría de señales
(`entry_signals`, `exit_signals`, `signal_to_trade_rate`, `activity_state`), fill maker
estimado, stress de costes (`stress_pf_2bps/5bps/10bps`), Monte Carlo agregado, score
`0–100` y `rules_version`.

**Veredicto automático (`add_verdict`)** — umbrales versionados en
`research_lab/config/decision_rules.json` (`lab-rules-v1`):
- `candidate`: trades ≥ 80, PF ≥ 1.15, expectancy > 0, maxDD > −35%, score ≥ 60.
- `watch`: trades ≥ 50, PF ≥ 1.0, expectancy ≥ 0, maxDD > −45%.
- `reject`: el resto.

**Validación temporal (`run_walk_forward`)**: ventanas rodantes (`train_months=18`,
`test_months=6`, `step_months=6`). **NO es WFO con optimización in-sample todavía**; el
`validation_type` correcto es `temporal_oos_fixed_params` (estabilidad temporal fuera de
muestra con parámetros fijos).

CLI:
```bash
python -m research_lab.edge_engine \
  --storage research_lab/storage \
  --pairs BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT \
  --timeframes 1h,4h \
  --decision-rules research_lab/config/decision_rules.json \
  --run-id <run_id> \
  --walk-forward --train-months 18 --test-months 6 --step-months 6
```

### 3.6 `account_simulator.py` — simulador de cuenta real (NUEVO, `account-sim-v1`)

Mientras el edge engine mide cada edge **aislado** (como si cada trade fuera independiente
con todo el capital), el account simulator responde otra pregunta:

> Si operara este edge con **una sola cuenta**, con cash limitado, riesgo por trade,
> máximo de posiciones simultáneas y límites de exposición, ¿qué pasaría de verdad?

**Contrato implementado**:
- Fuente: `edge_signals.parquet` + `features/<exchange>/<pair>/<tf>.parquet`.
  **No usa `edge_trades.parquet`** como fuente de simulación.
- Simula una cuenta **independiente por `edge` y `timeframe`** (agrega todos los pares).
- Long-only spot, **máximo una posición abierta por `edge|pair|timeframe`**.
- Entradas en el `open` siguiente a la señal; salidas por señal en el `open` siguiente.
- Stop-loss y take-profit intrabar con política `stop_first`.
- Stop con gap usando `min(stop_price, open_price)`.
- `max_hold` programado para el `open` siguiente.
- Fin de datos con `mark_to_market` (no inventa un cierre artificial).
- Asignación de capital **pro-rata** cuando hay varias señales el mismo día y límites de
  exposición; si no hay capital, **rechaza la señal sin dejar el cash negativo**.

**Artefactos** (en `storage/results/`):
- `account_trades.parquet` — cada trade ejecutado por la cuenta.
- `account_equity.parquet` — curva de equity (cash + valor de mercado).
- `account_summary.parquet` — métricas por `edge/timeframe`: `trades, total_return,
  profit_factor, max_drawdown`, etc.
- `account_rejections.parquet` — señales rechazadas y la razón (`max_positions`,
  `max_total_exposure`, `max_pair_exposure`, `already_open`, falta de capital).

**Importante**: por ahora es una **capa paralela de auditoría**. No reemplaza todavía
`profit_factor`, `max_drawdown` ni `total_return` del backtest individual, ni cambia el
veredicto `candidate` ni la promoción. Es Python-loop heavy: la corrida completa de todos
los edges y timeframes puede tardar varios minutos.

CLI:
```bash
python -m research_lab.account_simulator \
  --storage research_lab/storage --exchange kucoin \
  --pairs BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT \
  --timeframes 1h,4h \
  --rules research_lab/config/account_rules.json \
  --run-id <run_id>
```

Configuración (`research_lab/config/account_rules.json`, `account-rules-v1`):

```json
{
  "initial_cash": 1000.0, "risk_per_trade": 0.005,
  "max_open_positions": 3, "max_total_exposure": 0.75, "max_pair_exposure": 0.30,
  "min_stake": 10.0, "max_stake": 250.0,
  "fee": 0.001, "entry_slippage": 0.0005, "exit_slippage": 0.0005, "stop_slippage": 0.001,
  "stop_mult": 3.0, "take_profit_mult": 5.0,
  "intrabar_policy": "stop_first", "allocation_policy": "pro_rata",
  "end_of_data_policy": "mark_to_market"
}
```

### 3.7 `dashboard.py` — GUI Streamlit

Lee los Parquet de `storage/results/` (cache de 30 s). Pestañas:
- **Overview**: ranking de edges con veredicto y métricas.
- **Robustez**: señales, fill maker estimado, MC, stress, reglas y data-snooping.
- **Heatmap**: tabla pivote par × timeframe para la métrica elegida + barras.
- **Walk-forward**: agregado temporal OOS por edge + detalle por fold.
- **Liga**: temporadas por año.
- **Trades**: equity, drawdown, histograma de retornos, razones de salida.
- **Warehouse**: inventario de OHLCV/features, run history, promotion decision y comandos.

### 3.8 `promotion.py` — gate seguro hacia paper

CLI: `python -m research_lab.promotion --root DIR --storage DIR --rules promotion_rules.json [--run-id ID] [--execute]`

**Hard gates** (todos deben pasar):
- `run_manifest.status == success` del run que se promueve.
- `data_quality.status == passed`.
- **`require_parity_test == true` → `parity_report.json` con `status=passed`**. Como ese
  test aún no existe, **la promoción está bloqueada por diseño** (`status: blocked,
  reason: Missing parity test report`).
- Solo `allowed_timeframes` (`4h`) y `allowed_edges` (`donchian_20_10`,
  `ema_trend_20_50_100`, `volatility_expansion_20`). **`rsi_mean_reversion_25` fue
  retirado** porque la estrategia generada no implementa ese edge todavía.
- Gates OOS del walk-forward: `min_positive_fold_rate` (0.6), `min_median_oos_pf` (1.05),
  `min_median_oos_expectancy` (0.0), `min_worst_oos_drawdown` (−0.30), `min_total_test_trades` (80).
- `min_score` (70), `min_profit_factor` (1.2), `max_drawdown_floor` (−0.30).

Otros cambios de seguridad:
- `preflight_gate()` propaga la frescura real (expected/actual/lag bars).
- `observation_days` **deja de ser el gate principal**; ahora se calcula una racha:
  `consecutive_candidate_days`, `consecutive_candidate_runs`, `first_current_streak_date`.
- Cada artefacto generado usa una **clase Freqtrade única por edge/par/timeframe/run**
  (evita colisión con la clase fija `PromotedEdgeStrategy`).
- `compose_yaml()` fija la imagen con `docker_image` de las reglas.
- Flujo de despliegue: `ready → starting → started | deployment_failed`. **Ya no escribe
  `started` antes de confirmar que Docker arrancó**, y escribe un `deployment_manifest.json`
  separado del `run_manifest.json`.
- Si pasa, genera estrategia paper, `config.promoted.paper.json`, `docker-compose.paper.yml`
  y levanta el contenedor `freqtrade-paper-promoted`, **siempre en `dry_run`**.

### 3.9 Scripts de raíz (sobre Freqtrade)

**`research_edge_study.py`** — estudia una hipótesis *antes* de convertirla en estrategia
(probabilidad de rebote, expectativa neta tras costes, maker vs taker, fill rate,
sensibilidad a cada filtro).

**`quant_validation.py`** — valida los trades **exportados** de un backtest de Freqtrade
(PF, expectancy, Sharpe, Sortino, MaxDD, Monte Carlo, stress de costes, edge decay y
walk-forward opcional).

**`config.kucoin.example.json`** — config de Freqtrade para `QuantScalper_SXD`: KuCoin spot,
`dry_run: true`, wallet 1000 USDT, timeframe 5m, fee 0.1%, pares SOL/XRP/DOGE. **API keys
vacías**. También se usa en el homeserver para la descarga de datos por Docker.

---

## 4. Cómo se conecta y corre en el homeserver

### 4.1 El homeserver

- SO **Debian 13 (trixie)**, con **Docker**, **Tailscale** y Freqtrade ya corriendo.
- **~3.7 GB RAM**, disco 103 GB (11% usado), CPU modesta. Suficiente para OHLCV 1h/4h,
  Parquet, DuckDB, pandas y Streamlit. No para HFT ni tick-by-tick.
- Ruta del proyecto en el server: **`/home/adrian/freqtrade`**.
- Entorno Python del laboratorio: **`/home/adrian/freqtrade/.venv-lab`**
  (distinto del `.venv` local de la laptop).
- **`.venv-lab` NO tiene Freqtrade instalado**: la descarga de datos en el server se hace
  por **Docker** (`DOWNLOAD_METHOD=docker`, imagen `freqtradeorg/freqtrade:stable`).
- **No hay `.git`** en `/home/adrian/freqtrade`: el despliegue se hace por **tarball**, no
  con `git pull` (ver §7).

### 4.2 Conexión

Acceso por **Tailscale** (VPN malla privada, sin exponer puertos a internet):

```text
http://homeserver:8501          # dashboard, nombre MagicDNS
http://<IP_TAILSCALE>:8501      # IP 100.x.y.z
ssh adrian@homeserver           # SSH (o ssh adrian@<IP_TAILSCALE>)
```

El dashboard escucha en `0.0.0.0:8501`, por eso es accesible desde toda la tailnet.

### 4.3 Cómo corre dentro: systemd

**a) Dashboard siempre encendido — `adrian-quant-lab.service`**
```ini
ExecStart=/home/adrian/freqtrade/.venv-lab/bin/streamlit run research_lab/dashboard.py \
  --server.address 0.0.0.0 --server.port 8501 --server.headless true \
  --browser.gatherUsageStats false
Restart=unless-stopped
```

**b) Pipeline nocturno — `adrian-quant-pipeline.service` + `.timer`**
- El `.timer` dispara a las **03:30** todos los días (`Persistent=true` → corre al encender
  si el server estuvo apagado).
- El `.service` (`oneshot`) ejecuta `run_pipeline.sh`. Variables de entorno actuales en el
  server:

```text
ROOT_DIR=/home/adrian/freqtrade
VENV_DIR=.venv-lab
SOURCE_DIR=user_data/data/kucoin
STORAGE_DIR=research_lab/storage
TIMEFRAMES=1h,4h
PAIRS=BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT
DOWNLOAD_DATA=1
DOWNLOAD_METHOD=docker
DOWNLOAD_CONFIG=config.kucoin.example.json
DOWNLOAD_TIMEFRAMES=1h
DOWNLOAD_DOCKER_IMAGE=freqtradeorg/freqtrade:stable
NEW_PAIRS_DAYS=1200
RUN_PROMOTION=0
```

**Qué hace `run_pipeline.sh`** (en orden):
1. Toma un **lock con `flock`** (evita corridas solapadas). Si no puede escribir en
   `/run/lock`, usa `STORAGE_DIR/pipeline.lock`.
2. Crea/exporta `RUN_ID` y escribe el manifest en estado `running`. Un `trap ERR` marca el
   run como `failed` si algo revienta.
3. Si `DOWNLOAD_DATA=1`: **descarga datos** con `freqtrade download-data` (`venv`,
   `docker` o `auto`), 1h, `--new-pairs-days $NEW_PAIRS_DAYS`.
4. `warehouse migrate` si hay `.feather`; si no, usa el Parquet existente.
5. `warehouse resample 1h → 4h` (solo buckets completos).
6. `data_quality --fail-on-error` (gate estricto de frescura/integridad).
7. `warehouse features --timeframes 1h,4h`.
8. `edge_engine --run-id <run_id> --walk-forward`.
9. **`account_simulator --run-id <run_id>`** (capa de cuenta real).
10. `run_manager snapshot` → copia a `results/runs/<run_id>/` y `results/latest/`.
11. `run_manager complete --status success`.
12. Si `RUN_PROMOTION=1`, corre la promoción sobre ese mismo `run_id` (en el server está
    en `0` mientras no exista el parity test).

**c) Gate de promoción — `adrian-quant-promotion.service` (manual)**
- Reevalúa el último `run_id` (`run_manager ... latest-id`).
- El timer separado **`adrian-quant-promotion.timer` es LEGADO y debe quedar `inactive`**:
  la promoción debe correr al final del pipeline (cuando ya se sabe que la corrida terminó
  bien), no por un horario fijo independiente.

### 4.4 Instalación de los servicios (una sola vez)

```bash
# En el homeserver, desde /home/adrian/freqtrade
sudo cp research_lab/systemd/adrian-quant-*.service /etc/systemd/system/
sudo cp research_lab/systemd/adrian-quant-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adrian-quant-lab.service      # dashboard
sudo systemctl enable --now adrian-quant-pipeline.timer   # pipeline nocturno
# NO habilitar adrian-quant-promotion.timer (legado): debe quedar inactivo
```

> ⚠️ Los archivos systemd asumen ruta `/home/adrian/freqtrade`, usuario `adrian` y venv
> `.venv-lab`. Si algo difiere, edita `WorkingDirectory`, `User`, `PATH` y `ExecStart`.

### 4.5 Operación y diagnóstico en el server

```bash
# Estado
systemctl status adrian-quant-lab.service
systemctl list-timers | grep adrian          # ver próxima ejecución
systemctl is-active adrian-quant-lab.service adrian-quant-pipeline.timer adrian-quant-promotion.timer

# Logs
journalctl -u adrian-quant-lab.service -f
journalctl -u adrian-quant-pipeline.service -n 160 --no-pager

# Forzar una corrida ahora (sin esperar a las 03:30)
sudo systemctl start adrian-quant-pipeline.service

# Reiniciar el dashboard
sudo systemctl restart adrian-quant-lab.service
```

---

## 5. Guía de uso

### 5.1 Setup local (laptop)

```bash
cd /home/perdomopro/Desktop/freqtrade
source .venv/bin/activate
```

En el homeserver el lab vive separado del bot:

```bash
cd /home/adrian/freqtrade
source .venv-lab/bin/activate
python -m pip install -r research_lab/requirements.txt
```

Dependencias pinneadas:

```text
pandas==3.0.3
numpy==2.4.6
pyarrow==24.0.0
duckdb==1.5.3
streamlit==1.58.0
```

Regla obligatoria: ejecutar como módulo (`python -m research_lab.edge_engine`), **no** como
script suelto (`python research_lab/edge_engine.py`) — eso rompe los imports de paquete.

### 5.2 Smoke test

```bash
ROOT_DIR=/home/adrian/freqtrade VENV_DIR=.venv-lab \
PAIR=BTC/USDT TIMEFRAME=1h \
bash research_lab/scripts/smoke_test.sh        # debe terminar con "Smoke test OK"
```

Verifica `ohlcv_manifest.parquet`, `features_manifest.parquet`, `features_all.parquet`,
`results/edge_summary.parquet` y `lab.duckdb`.

### 5.3 Pipeline completo de un tirón

```bash
# Local (sin red, usando el Parquet ya migrado):
ROOT_DIR=/home/perdomopro/Desktop/freqtrade VENV_DIR=.venv \
DOWNLOAD_DATA=0 RUN_PROMOTION=0 TIMEFRAMES=1h,4h \
PAIRS=BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT \
bash research_lab/scripts/run_pipeline.sh
```

> Con `DOWNLOAD_DATA=1` el pipeline intenta descargar datos frescos. En la laptop puedes
> usar `DOWNLOAD_DATA=0` para correr offline; en el server siempre va con `DOWNLOAD_DATA=1`
> y `DOWNLOAD_METHOD=docker`.

### 5.4 Paso a paso (control fino)

```bash
# 1. (opcional) descargar datos frescos
freqtrade download-data --config config.kucoin.example.json \
  --pairs BTC/USDT ETH/USDT SOL/USDT XRP/USDT DOGE/USDT \
  --timeframes 1h --new-pairs-days 1200

# 2. Migrar feather → parquet
python -m research_lab.warehouse migrate --source user_data/data/kucoin

# 3. Crear 4h desde 1h (solo buckets completos)
python -m research_lab.warehouse resample --source-timeframe 1h --target-timeframe 4h

# 4. Gate de calidad (estricto)
python -m research_lab.data_quality --storage research_lab/storage \
  --timeframes 1h,4h --run-id manual --fail-on-error

# 5. Features
python -m research_lab.warehouse features --timeframes 1h,4h

# 6. Edges + validación temporal OOS + eventos de señal
python -m research_lab.edge_engine --timeframes 1h,4h --run-id manual --walk-forward

# 7. Cuenta real (capa de auditoría)
python -m research_lab.account_simulator --timeframes 1h,4h --run-id manual
```

### 5.5 Dashboard local

```bash
streamlit run research_lab/dashboard.py \
  --server.address 0.0.0.0 --server.port 8501 \
  --server.headless true --browser.gatherUsageStats false
# Luego abrir http://localhost:8501
```

En el server ya corre solo; ábrelo en `http://homeserver:8501`.

### 5.6 Consultar resultados con SQL (DuckDB)

```bash
python -c "import duckdb; con=duckdb.connect('research_lab/storage/lab.duckdb'); \
print(con.sql('SELECT edge,pair,timeframe,trades,profit_factor,expectancy,verdict \
FROM edge_summary ORDER BY profit_factor DESC LIMIT 15').df())"
```

Vistas: `ohlcv_manifest`, `features_manifest`, `features`, `edge_summary`, `edge_trades`,
`edge_signals`, `walk_forward_summary`, `season_summary`, `run_history`, `account_summary`,
`account_trades`, `account_equity`, `account_rejections`.

### 5.7 Gate de promoción a paper

```bash
python -m research_lab.promotion \
  --root /home/adrian/freqtrade --storage research_lab/storage \
  --rules research_lab/config/promotion_rules.json \
  --run-id <run_id> [--execute]
```

Hoy devuelve `status: blocked` (falta el parity test). No promueve nada hasta que existan
todos los gates. Cuando pase, genera estrategia/config/compose paper y levanta
`freqtrade-paper-promoted`, siempre en `dry_run`.

### 5.8 Validar una estrategia de Freqtrade (flujo clásico)

```bash
freqtrade backtesting --config config.kucoin.example.json \
  --strategy QuantScalper_SXD --timerange 20220101-20260616 \
  --enable-protections --export trades

python quant_validation.py --trades user_data/backtest_results/<archivo>.zip \
  --mc 5000 --stress --edge-decay
```

### 5.9 Tests

```bash
pytest -q tests/test_account_simulator.py     # 10 casos (TP, stop, gap, stop_first, pro-rata, sin capital, reproducibilidad...)
```

---

## 6. Criterios de aprobación (recordatorio)

Un edge **no** se aprueba solo por retorno positivo. Mínimos antes de pensar en dry-run:
PF > 1.15 (ideal 1.2–1.4), expectancy positiva tras costes, ≥200 trades (frecuentes) o
≥80 (lentas), validación temporal positiva, Monte Carlo sin ruina fácil, sensibilidad
estable, poca dependencia de un solo par y drawdown temporal tolerable.

Estado actual:

- No hay `candidate` promovible; la promoción además está **bloqueada por falta de parity
  test** (por diseño).
- Hay edges en `watch`, principalmente `4h volatility_expansion`.
- `donchian_20_10 SOL/USDT 4h` tiene PF interesante pero DD temporal demasiado alto, por
  eso queda rechazado para paper automático.
- Última corrida en el homeserver: `success`, `data_quality: passed`, `promotion:
  no_candidate`, 35 reject / 5 watch.

Top `watch` reciente (1h,4h, 5 pares):

```text
volatility_expansion_20 ETH/USDT 4h  PF 1.204  DD -15.88%
volatility_expansion_20 DOGE/USDT 4h PF 1.199  DD -35.67%
volatility_expansion_20 XRP/USDT 4h  PF 1.109  DD -28.51%
ema_trend_20_50_100 BTC/USDT 4h      PF 1.019  DD -31.80%
donchian_20_10 BTC/USDT 4h           PF 1.003  DD -30.08%
```

---

## 7. Sincronizar laptop ↔ homeserver

El código se versiona con git en la laptop (rama `develop`). **El homeserver no tiene
`.git`**, así que el despliegue se hace por **tarball**, no con `git pull`:

```bash
# En la laptop: empaquetar el código del lab (sin storage)
tar --exclude='research_lab/storage' --exclude='__pycache__' \
  -czf /tmp/adrian_quant_lab_deploy.tgz research_lab GUIA_*.md run_notes config.kucoin.example.json

# Copiar al server
scp /tmp/adrian_quant_lab_deploy.tgz adrian@homeserver:/tmp/

# En el server: respaldar lo anterior y desplegar
ssh adrian@homeserver
cd /home/adrian/freqtrade
mkdir -p /tmp/adrian_quant_backup_$(date -u +%Y%m%dT%H%M%SZ)
cp -r research_lab /tmp/adrian_quant_backup_*/    # respaldo
tar -xzf /tmp/adrian_quant_lab_deploy.tgz
.venv-lab/bin/python -m py_compile research_lab/*.py    # compilar
sudo systemctl start adrian-quant-pipeline.service       # regenerar storage
```

El directorio `research_lab/storage/` (Parquet, DuckDB) **no se versiona ni se copia**: cada
máquina regenera su storage corriendo el pipeline.

> Nota histórica de datos: al reconstruir con `--new-pairs-days 1200` el histórico remoto
> arranca ~2023-03, no desde 2022. Sigue alcanzando para ventanas de 18 meses, pero si
> quieres 2022 completo hay que hacer una descarga histórica inicial más larga o restaurar
> un respaldo de datos.

---

## 8. Diagnóstico rápido

| Síntoma | Causa probable / acción |
|---|---|
| Dashboard dice "No hay resultados" | No has corrido el pipeline. Ejecuta los pasos o `run_pipeline.sh`. |
| `Missing ohlcv_manifest.parquet` | Falta `warehouse migrate` antes de `resample`/`features`. |
| `data_quality status: failed` con `stale_data` | Datos viejos: `lag_bars > allowed_lag_bars`. Corre `download-data` o sube `--max-lag-bars` solo para aislar pruebas. |
| `incomplete_candles_present` | Hay velas posteriores a la última cerrada esperada. Re-descarga / espera al cierre. |
| `missing_target_resample` | Falta el `4h` de algún par. Corre `warehouse resample`. |
| `nonfinite_ohlcv` | NaN/inf en datos: revisa la descarga, vuelve a migrar. |
| `Skipping missing features` en edge_engine | No generaste features para ese par/timeframe. |
| Promoción `status: blocked, Missing parity test report` | Esperado: la promoción está bloqueada hasta implementar el parity test. |
| Dashboard no abre desde otra máquina | Verifica Tailscale en ambos y que el server escuche en `0.0.0.0`. |
| Pipeline nocturno no corrió | `systemctl list-timers \| grep adrian`; `journalctl -u adrian-quant-pipeline`. |
| Pipeline se solapa / lock | `flock` evita corridas dobles; revisa `STORAGE_DIR/pipeline.lock`. |
| Descarga falla en el server | `.venv-lab` no tiene freqtrade: debe usar `DOWNLOAD_METHOD=docker` con la imagen stable. |

---

## 9. Pendientes técnicos (roadmap)

1. **Parity test real** entre `edge_engine` y la estrategia Freqtrade generada — desbloquea
   la promoción (`require_parity_test`).
2. Estrategia promovida con stop dinámico, take-profit dinámico y `max_hold` equivalentes.
3. **WFO real**: selección de parámetros en train y evaluación en test (hoy es
   `temporal_oos_fixed_params`).
4. Multiple testing: Deflated Sharpe, PBO/CSCV y bootstrap por bloques.
5. Optimizar el account simulator (hoy es Python-loop heavy) y decidir si sus métricas
   entran al veredicto `candidate`.
6. Paper monitor y despromoción automática.
7. Evaluar si `volatility_expansion_20` con salida `donchian_low_20` mejora o degrada
   estabilidad frente a la versión anterior.
