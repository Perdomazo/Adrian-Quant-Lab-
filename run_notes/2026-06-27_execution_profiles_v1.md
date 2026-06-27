# Bitacora - Account Execution Profiles v1

Fecha: 2026-06-27

## Objetivo

Separar la evaluacion de cuenta en dos perfiles:

- `research`: modelo actual con asignacion `pro_rata` y slippage conservador.
- `freqtrade`: modelo operativo inicial compatible con backtest de Freqtrade,
  usando orden secuencial por pairlist estatica y slippage cero.

No se modificaron:

- Edges.
- Ventanas OOS.
- Umbral Account Candidate de 60%.
- Stop/TP/max hold.
- Pair universe.
- Account Candidate rules.

## Baseline congelada

Antes de tocar el simulador se creo y publico el tag:

- `history-2021-account-candidate-v1`
- Mensaje: `7 OOS folds, 3 watch, 0 candidates`

## Cambios implementados

### Configuracion

Se agregaron:

- `research_lab/config/account_rules_research.json`
- `research_lab/config/account_rules_freqtrade.json`
- `research_lab/config/cost_models.json`

Se conservaron los limites actuales:

- `max_open_positions=3`
- `max_total_exposure=0.75`
- `max_pair_exposure=0.30`

Motivo: cambiar `max_open_positions` a 5 mezclaria el sprint de perfiles con
un cambio de riesgo. Este sprint solo cambia semantica de ejecucion.

### Simulador

`account_simulator.py` ahora soporta:

- `execution_profile`
- `execution_model`
- `allocation_policy`
- `cost_model_version`
- `pair_priority_source`
- `pair_universe`

Perfiles:

- `research`
  - `allocation_policy=pro_rata`
  - `execution_model=conservative_intrabar_v1`
  - costes `research-costs-v1`

- `freqtrade`
  - `allocation_policy=pairlist_sequential`
  - `execution_model=freqtrade_backtest_v1`
  - costes `freqtrade-backtest-costs-v1`
  - slippage cero

La ejecucion secuencial:

- Procesa salidas pendientes antes que entradas.
- Recorre entradas vencidas en el orden de `pair_universe.json`.
- Recalcula stake despues de cada entrada.
- No redistribuye sobrantes.
- Rechaza entradas cuando no hay slots, cash o exposicion.

Trades y rechazos guardan:

- `execution_profile`
- `execution_model`
- `allocation_policy`
- `pair_priority`
- `allocation_sequence`
- `cost_model_version`

### Artefactos por perfil

Se agrego:

- `research_lab/profile_paths.py`

Los artefactos nuevos viven en:

```text
research_lab/storage/results/profiles/research/
research_lab/storage/results/profiles/freqtrade/
```

El perfil `research` tambien sigue escribiendo alias legacy en
`research_lab/storage/results/account_*.parquet` para no romper dashboard ni
consultas existentes.

### Validadores y Candidate

Se adapto:

- `account_validation.py`
- `account_oos.py`
- `account_oos_validation.py`
- `account_candidate.py`

Todos aceptan:

```text
--profile research|freqtrade
```

Y fallan si:

```text
CLI profile != config.execution_profile
```

El `candidate_id` ahora incluye:

- `execution_profile`
- `execution_model`
- `allocation_policy`
- `cost_model_version`

Por tanto, un resultado `research` y uno `freqtrade` generan IDs diferentes.

### Pipeline

`run_pipeline.sh` ahora ejecuta ambos perfiles despues de `edge_engine`:

```text
profile research
  account simulator
  account validation
  account OOS
  account OOS validation
  account candidate

profile freqtrade
  account simulator
  account validation
  account OOS
  account OOS validation
  account candidate
```

Luego ejecuta:

```text
profile_comparison
```

Variables nuevas:

- `ACCOUNT_PROFILES=research,freqtrade`
- `ACCOUNT_RULES_RESEARCH=research_lab/config/account_rules_research.json`
- `ACCOUNT_RULES_FREQTRADE=research_lab/config/account_rules_freqtrade.json`

### Manifest y snapshots

`run_manager.py` ahora:

- Copia `results/profiles/` dentro de `runs/<run_id>/profiles/` y `latest/profiles/`.
- Agrega `execution_profiles` al manifest con estados por perfil.
- Mantiene campos legacy planos para compatibilidad.

### DuckDB

`warehouse.refresh_duckdb()` ahora crea vistas combinadas:

- `account_summary_profiles`
- `account_trades_profiles`
- `account_equity_profiles`
- `account_rejections_profiles`
- `account_oos_summary_profiles`
- `account_oos_trades_profiles`
- `account_oos_equity_profiles`
- `account_oos_rejections_profiles`
- `account_oos_aggregate_profiles`
- `account_candidate_summary_profiles`
- `account_candidate_history_profiles`
- `profile_comparison_summary`
- `profile_trade_overlap`

### Comparacion de perfiles

Nuevo modulo:

- `research_lab/profile_comparison.py`

Artefactos:

- `profile_comparison_summary.parquet`
- `profile_trade_overlap.parquet`
- `profile_comparison.json`

Compara por `edge/timeframe`:

- retorno full-history
- PF
- drawdown
- OOS positive fold rate
- entradas
- trades
- overlap de entradas
- overlap de trades
- veredicto research vs freqtrade

## Validacion local

Comandos ejecutados:

```bash
.venv/bin/python -m py_compile \
  research_lab/account_simulator.py \
  research_lab/account_validation.py \
  research_lab/account_oos.py \
  research_lab/account_oos_validation.py \
  research_lab/account_candidate.py \
  research_lab/profile_comparison.py \
  research_lab/profile_paths.py \
  research_lab/run_manager.py \
  research_lab/warehouse.py
```

```bash
bash -n research_lab/scripts/run_pipeline.sh research_lab/scripts/run_research.sh
```

```bash
.venv/bin/python -m ruff check \
  research_lab/account_simulator.py \
  research_lab/account_validation.py \
  research_lab/account_oos.py \
  research_lab/account_oos_validation.py \
  research_lab/account_candidate.py \
  research_lab/profile_comparison.py \
  research_lab/profile_paths.py \
  research_lab/run_manager.py \
  research_lab/warehouse.py \
  tests/test_execution_profiles.py \
  tests/test_profile_comparison.py
```

```bash
.venv/bin/python -m pytest -q --noconftest \
  tests/test_account_simulator.py \
  tests/test_account_validation.py \
  tests/test_account_oos.py \
  tests/test_account_oos_validation.py \
  tests/test_account_candidate.py \
  tests/test_execution_profiles.py \
  tests/test_profile_comparison.py \
  tests/test_pipeline_modes.py \
  tests/test_run_manager.py
```

Resultado:

- `76 passed`
- `ruff`: passed
- `py_compile`: passed
- `bash -n`: passed
- `git diff --check`: passed

## Estado de fase

Implementacion local: completa.

Pendiente antes de cerrar el sprint:

- Ejecutar research local o remoto completo con `ACCOUNT_PROFILES=research,freqtrade`.
- Revisar `profile_comparison_summary`.
- Desplegar en homeserver.
- Ejecutar research remoto manual.
- Reactivar `adrian-quant-research.timer` solo si ambos perfiles pasan.

## Nota de rendimiento

El ultimo research remoto con un solo perfil tardo ~40.6 minutos, con
`account_oos` como cuello de botella principal. Ejecutar ambos perfiles puede
duplicar aproximadamente el tramo de cuenta/OOS. Si supera el margen operativo
semanal, la siguiente optimizacion debe ser recortar features al rango de fold
en `account_oos` antes de simular.

## Referencias verificadas

Se reviso documentacion oficial de Freqtrade sobre backtesting y callbacks.
Puntos relevantes:

- `--timeframe-detail` permite evaluar actividad dentro de velas con timeframe
  menor, pero sin detail solo se evaluan las primeras senales que caben por
  candle y los slots se liberan en la siguiente vela.
- `--fee` se aplica en entrada y salida.
- `custom_stake_amount()` puede devolver `0` o `None` para impedir la entrada.
