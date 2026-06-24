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
