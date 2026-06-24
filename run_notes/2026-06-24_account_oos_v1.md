# 2026-06-24 - Account OOS v1

## Objetivo

Implementar la primera version de Account OOS para evaluar la cuenta completa fuera de muestra,
sin activar promocion ni paper trading.

## Verificacion operativa remota

- Se intento verificacion breve del homeserver por Tailscale:
  - `ssh -F /dev/null adrian@100.104.72.97`
- Resultado:
  - `connect to host 100.104.72.97 port 22: Connection timed out`
- Se reintento al cierre del bloque local con el mismo resultado.
- Decision:
  - No se hizo diagnostico profundo del apagado.
  - El despliegue remoto de este bloque queda pendiente hasta que SSH responda.

## Cambios implementados

- `research_lab/account_simulator.py`
  - Se agrego `SimulationResult`.
  - `simulate_account()` ahora es reutilizable por OOS.
  - La funcion acepta `start_date` y `end_date`.
  - Las senales se filtran a `[start_date, end_date)`.
  - Las entradas no pueden ejecutarse fuera del periodo OOS.
  - Las salidas pendientes que caen despues del final no se fuerzan; se mantiene mark-to-market.
  - La CLI full-history conserva los mismos artefactos:
    - `account_summary.parquet`
    - `account_trades.parquet`
    - `account_equity.parquet`
    - `account_rejections.parquet`

- `research_lab/validation_windows.py`
  - Se creo `TemporalFold`.
  - Se creo `build_temporal_folds()`.
  - Edge OOS y Account OOS quedan preparados para compartir las mismas ventanas.

- `research_lab/edge_engine.py`
  - El walk-forward dejo de usar folds internos.
  - Ahora usa `build_temporal_folds()`.

- `research_lab/account_oos.py`
  - Nuevo modulo Account OOS v1.
  - Simula por:
    - edge
    - timeframe
    - fold temporal
    - universo de pares
  - Reinicia cuenta por fold.
  - Usa el mismo motor de `simulate_account()`.
  - Escribe:
    - `account_oos_summary.parquet`
    - `account_oos_trades.parquet`
    - `account_oos_equity.parquet`
    - `account_oos_rejections.parquet`
    - `account_oos_aggregate.parquet`
  - Los Parquet vacios se escriben con esquema explicito para que DuckDB no falle.

- `research_lab/account_oos_validation.py`
  - Nuevo validador Account OOS.
  - Detecta:
    - trades fuera del periodo test
    - folds duplicados
    - equity rows duplicadas
    - run_id mezclado
    - NaN/inf en columnas numericas
    - agregados inconsistentes
  - Escribe:
    - `account_oos_validation.json`

- `research_lab/scripts/run_pipeline.sh`
  - El modo `research` ahora ejecuta:
    - `account_oos`
    - `account_oos_validation --fail-on-error`
  - Se ejecuta antes de snapshot y success.
  - Variables nuevas:
    - `PAIR_UNIVERSE`
    - `TRAIN_MONTHS`
    - `TEST_MONTHS`
    - `STEP_MONTHS`

- `research_lab/run_manager.py`
  - Se agregaron artefactos OOS a snapshots.
  - El manifest registra:
    - `account_oos_version`
    - `account_oos_validation_version`
    - `account_oos_validation_status`
    - `train_months`
    - `test_months`
    - `step_months`

- `research_lab/warehouse.py`
  - Se agregaron vistas DuckDB:
    - `account_oos_summary`
    - `account_oos_trades`
    - `account_oos_equity`
    - `account_oos_rejections`
    - `account_oos_aggregate`

- `research_lab/dashboard.py`
  - Se agrego la pestaña `Cuenta OOS`.
  - Muestra:
    - agregado por edge/timeframe
    - detalle por fold
    - equity y drawdown por fold
  - No concatena equity de folds porque cada fold reinicia capital.

- `research_lab/deep_validation.py`
  - Ajustes menores de tipos y formato requeridos por hooks de commit.
  - No se cambio la logica estadistica de deep validation.

## Tests agregados

- `tests/test_validation_windows.py`
- `tests/test_account_oos.py`
- `tests/test_account_oos_validation.py`

## Validaciones ejecutadas

- Compilacion Python:
  - `account_simulator.py`
  - `account_oos.py`
  - `account_oos_validation.py`
  - `validation_windows.py`
  - `edge_engine.py`
  - `run_manager.py`
  - `warehouse.py`
  - `dashboard.py`
- Bash:
  - `bash -n research_lab/scripts/run_pipeline.sh`
  - `bash -n research_lab/scripts/run_research.sh`
- Tests:
  - `37 passed`
- Mypy:
  - sin issues en los modulos del sprint y dependencias tocadas.
- Ruff:
  - `All checks passed`
- `git diff --check`:
  - sin errores.

## Corrida local research

Se intento:

```bash
ROOT_DIR=/home/perdomopro/Desktop/freqtrade \
VENV_DIR=.venv \
DOWNLOAD_DATA=0 \
PIPELINE_MODE=research \
RUN_PROMOTION=0 \
TIMEFRAMES=1h,4h \
PAIRS=BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT \
bash research_lab/scripts/run_research.sh
```

Resultado:

- El pipeline fallo correctamente en `data_quality`.
- Motivo:
  - datos locales stale.
  - `actual_last_available_candle`: `2026-06-16T20:00:00+00:00`
  - `expected_last_complete_candle`: `2026-06-24T12:00:00+00:00`
  - `max_lag_bars`: `184`
  - `allowed_lag_bars`: `1`
- Decision:
  - No se relajo el gate.
  - La corrida completa debe hacerse en el homeserver cuando SSH y descarga esten operativos.

## Estado al cierre

- Fase completada localmente:
  - Account OOS v1
  - Account OOS validation
  - Integracion al research pipeline
  - DuckDB views
  - Dashboard basico OOS
  - Tests locales
- Pendiente:
  - Verificacion operativa remota por SSH.
  - Despliegue al homeserver.
  - Research manual remoto con datos actualizados.
  - Activar timer semanal solo si la corrida remota queda en success.

## No implementado todavia

- `account_candidate`
- historico extendido hacia 2021 en homeserver
- perfil Freqtrade-compatible
- estrategia Freqtrade equivalente
- parity test
- paper monitor
- promocion automatica

## Despliegue remoto del 2026-06-24

Paquete desplegado:

- `/tmp/adrian_quant_account_oos.tgz`
- SHA256:
  - `ccec2e8f47cf49b4066865d876ef13b9f668ed2419890595fe978d3c7ee9cf49`
- Commit registrado en homeserver:
  - `4735466cfea78a85b6e278d9f2d732ff49002405`
- Backup remoto:
  - `/tmp/adrian_quant_backup_20260624T225327Z`

Validaciones remotas:

- Compilacion Python:
  - OK
- Bash `-n`:
  - OK
- Tests remotos:
  - `37 passed`

Ingest manual remoto:

- `run_id`: `ingest-20260624T225556Z-nogit-9d265b`
- `status`: `success`
- `data_quality_status`: `passed`
- `files_checked`: `10`
- `stale_pairs`: `[]`
- `incomplete_pairs`: `[]`
- `max_lag_bars`: `0`

Research manual remoto:

- Primer intento por SSH directo:
  - `run_id`: `20260624T225658Z-nogit-a31af4`
  - `status`: `failed`
  - `error`: `pipeline_failed_exit_120`
  - Causa probable: corte de conexion SSH durante la etapa posterior a `account_validation`.
  - `data_quality_status`: `passed`
  - `account_validation_status`: `passed`
  - `account_oos_validation.json`: faltante en ese intento.
- Segundo intento con `nohup`:
  - `run_id`: `20260624T232201Z-nogit-fe3f19`
  - `status`: `success`
  - `data_quality_status`: `passed`
  - `account_validation_status`: `passed`
  - `account_oos_validation_status`: `passed`
  - `total_seconds`: `1125`
  - `features_seconds`: `33`
  - `edge_engine_seconds`: `49`
  - `walk_forward_seconds`: `8`
  - `account_simulator_seconds`: `579`
  - `account_oos_seconds`: `441`
  - `account_oos_validation_seconds`: `2`

Account validation remoto:

- `negative_cash_rows`: `0`
- `equity_identity_failures`: `0`
- `exposure_violations`: `0`
- `position_limit_violations`: `0`
- `duplicate_trade_ids`: `0`
- `nonfinite_rows`: `0`
- `trades_checked`: `7824`
- `equity_rows_checked`: `144832`

Account OOS validation remoto:

- `status`: `passed`
- `folds_checked`: `24`
- `trades_outside_test`: `0`
- `duplicate_folds`: `0`
- `duplicate_equity_rows`: `0`
- `aggregate_mismatches`: `0`
- `missing_artifacts`: `[]`
- `trade_rows`: `3476`
- `equity_rows`: `65520`
- `rejection_rows`: `22004`

Account OOS agregado remoto:

- `ema_trend_20_50_100 4h`
  - positive_fold_rate: `0.666667`
  - median_oos_return: `0.042043`
  - median_oos_profit_factor: `1.131329`
  - worst_oos_drawdown: `-0.081143`
  - total_oos_trades: `288`
- `volatility_expansion_20 4h`
  - positive_fold_rate: `0.666667`
  - median_oos_return: `0.017438`
  - median_oos_profit_factor: `1.174805`
  - worst_oos_drawdown: `-0.054096`
  - total_oos_trades: `118`

Estado final de servicios:

- `adrian-quant-lab.service`: `active`
- `adrian-quant-ingest.timer`: `active`, `enabled`
- `adrian-quant-research.timer`: `active`, `enabled`
- `adrian-quant-deep.timer`: `inactive`, `disabled`
- `adrian-quant-pipeline.timer`: `inactive`, `disabled`
- `adrian-quant-promotion.timer`: `inactive`, `disabled`
- Dashboard:
  - `HTTP/1.1 200 OK`
- Paper containers:
  - ninguno encontrado con `freqtrade-paper-promoted`.

Proximos pasos:

- Observar la primera corrida automatica de ingest.
- No activar deep hasta validarlo manualmente.
- No activar promocion.
- Siguiente sprint recomendado:
  - `account_candidate`
  - despues de revisar distribucion de folds OOS y trades por fold.
