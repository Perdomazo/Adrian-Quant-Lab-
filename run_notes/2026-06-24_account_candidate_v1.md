# 2026-06-24 - Account Candidate v1

## Objetivo

Crear el gate de decision de cuenta despues de Account OOS, sin activar promocion ni paper.

La decision debe separar:

- `eligibility_status`
  - `invalid_run`
  - `failed_validation`
  - `insufficient_history`
  - `eligible`
- `account_verdict`
  - `reject`
  - `watch`
  - `candidate`

Con el historico actual, el resultado esperado es `insufficient_history` porque Account OOS solo tiene
tres folds por combinacion y la regla inicial exige cinco.

## Revision Account OOS remoto

Se consulto `account_oos_aggregate` en el homeserver y se guardo la salida en:

- `/tmp/account_oos_aggregate.txt`

Resultados principales:

- `ema_trend_20_50_100 4h`
  - folds: `3`
  - positive_fold_rate: `0.666667`
  - median_oos_return: `0.042043`
  - median_oos_profit_factor: `1.131329`
  - worst_oos_drawdown: `-0.081143`
  - total_oos_trades: `288`
- `volatility_expansion_20 4h`
  - folds: `3`
  - positive_fold_rate: `0.666667`
  - median_oos_return: `0.017438`
  - median_oos_profit_factor: `1.174805`
  - worst_oos_drawdown: `-0.054096`
  - total_oos_trades: `118`
- `donchian_20_10 4h`
  - folds: `3`
  - positive_fold_rate: `0.333333`
  - median_oos_return: `-0.001637`
  - median_oos_profit_factor: `0.993792`
  - worst_oos_drawdown: `-0.116719`
  - total_oos_trades: `293`
- `donchian_20_10 1h`
  - folds: `3`
  - positive_fold_rate: `0`
  - median_oos_return: `-0.082832`
  - median_oos_profit_factor: `0.899929`
  - worst_oos_drawdown: `-0.310593`
  - total_oos_trades: `1085`
- `ema_trend_20_50_100 1h`
  - folds: `3`
  - positive_fold_rate: `0`
  - median_oos_return: `-0.133490`
  - median_oos_profit_factor: `0.862132`
  - worst_oos_drawdown: `-0.330735`
  - total_oos_trades: `1177`

Conclusion:

- Hay configuraciones 4h prometedoras.
- Tres folds no son suficientes para declarar candidato.
- El gate debe reportar `insufficient_history`, no `reject`, cuando las metricas se ven bien pero falta evidencia temporal.

## Cambios implementados

- `research_lab/config/account_decision_rules.json`
  - Nueva configuracion versionada `account-decision-rules-v1`.
  - Reglas de elegibilidad:
    - data quality passed
    - account validation passed
    - account OOS validation passed
    - minimo `5` folds OOS
    - minimo `36` meses de observacion
  - Reglas separadas para `candidate` y `watch`.

- `research_lab/account_candidate.py`
  - Nuevo modulo `account-candidate-v1`.
  - Lee:
    - `run_manifest.json`
    - `data_quality_report.json`
    - `account_validation.json`
    - `account_oos_validation.json`
    - `account_summary.parquet`
    - `account_oos_aggregate.parquet`
    - `account_oos_summary.parquet`
    - `account_equity.parquet`
    - `account_decision_rules.json`
    - `pair_universe.json`
  - Genera `candidate_id` deterministico con hash de:
    - edge
    - edge version
    - timeframe
    - pair universe version/hash
    - account rules version/hash
    - account simulator version
    - features version
  - Registra reglas fallidas y aprobadas en cada fila.
  - Mantiene historial global y deduplica por `candidate_id + run_id`.
  - Calcula `consecutive_candidate_runs` y reinicia la racha cuando deja de ser candidate.

- Artefactos nuevos:
  - `account_candidate_summary.parquet`
  - `account_candidate_decision.json`
  - `account_candidate_history.parquet`

- `research_lab/scripts/run_pipeline.sh`
  - El modo research ahora ejecuta:
    - `account_candidate`
  - Orden actual:
    - ingest
    - features
    - edge engine
    - account simulator
    - account validation
    - account OOS
    - account OOS validation
    - account candidate
    - snapshot
    - success

- `research_lab/run_manager.py`
  - Snapshot incluye:
    - `account_candidate_summary.parquet`
    - `account_candidate_decision.json`
  - El manifest registra:
    - `account_candidate_version`
    - `account_decision_rules_version`
    - `account_decision_rules_hash`
    - `account_candidate_status`

- `research_lab/warehouse.py`
  - Nuevas vistas DuckDB:
    - `account_candidate_summary`
    - `account_candidate_history`

- `research_lab/dashboard.py`
  - Se agrego seccion `Decision de cuenta` dentro de Cuenta OOS.
  - Muestra elegibilidad, veredicto, `candidate_id`, metricas full-history, metricas OOS y reglas fallidas.

## Tests agregados

- `tests/test_account_candidate.py`

Casos cubiertos:

- Manifest fallido produce `invalid_run`.
- Data quality fallida bloquea.
- Account validation fallida bloquea.
- Account OOS validation fallida bloquea.
- Tres folds con minimo cinco produce `insufficient_history`.
- Metricas candidatas producen `candidate`.
- Hard gate fallido impide candidate.
- Reglas watch producen `watch`.
- Metricas debiles producen `reject`.
- Mismo input produce mismo `candidate_id`.
- Cambio en account rules cambia `candidate_id`.
- Cambio de universo cambia `candidate_id`.
- Cero candidatos no falla.
- History no duplica el mismo `run_id/candidate_id`.
- Racha se reinicia al dejar de ser candidate.

## Validaciones locales

- Tests:
  - `56 passed`
- Ruff:
  - `All checks passed`
- Python compile:
  - OK
- Bash:
  - `bash -n research_lab/scripts/run_pipeline.sh`
- `git diff --check`:
  - sin errores

## Estado al cierre local

- Account Candidate v1 implementado localmente.
- Integracion al research pipeline implementada.
- DuckDB y dashboard actualizados.

## Bugs detectados durante el despliegue

- Manifest contaminado por artefactos de otro run:
  - Un run fallido temprano heredo `account_candidate_status=success` desde un JSON global anterior.
  - Se corrigio `run_manager.py` para leer status JSON solo si `run_id` coincide.
  - Se agrego `tests/test_run_manager.py`.

- Evaluacion historica usando JSON globales:
  - `account_candidate` podia evaluar un `run_id` historico con validaciones globales sobrescritas por otro run.
  - Se corrigio para preferir `results/runs/<run_id>/...` y usar global solo si el `run_id` coincide.

- `account_candidate` dentro del pipeline veia el manifest en `running`:
  - El pipeline ejecuta account candidate antes de `run_manager complete --status success`.
  - Standalone sigue exigiendo `success`.
  - El pipeline ahora pasa `--allow-running-run` explicitamente.
  - Se agrego test para exigir ese flag.

## Despliegue remoto

Commits relevantes:

- `0f87afb56` - Add account candidate gate.
- `fb742de72` - Prevent stale run statuses in manifests.
- `884f75915` - Use run snapshots for account candidate inputs.
- `2faad6352` - Allow account candidate during active pipeline run.

Paquete final desplegado:

- `/tmp/adrian_quant_account_candidate.tgz`
- SHA256:
  - `ea49dcd53ce9f41bed517f239aaad622cecf46988b454c158268aeb82089d920`
- Commit registrado en homeserver:
  - `2faad635237d7e9bd2e4bb4e849f00e7c15d5d4b`
- Backup final remoto:
  - `/tmp/adrian_quant_backup_20260627T015510Z`

Validaciones remotas:

- Compilacion Python:
  - OK
- Bash `-n`:
  - OK
- Tests remotos:
  - `56 passed`

Research manual remoto con descarga activa:

- `run_id`: `20260627T013209Z-nogit-1c896a`
- `status`: `success`
- `data_quality_status`: `passed`
- `account_validation_status`: `passed`
- `account_oos_validation_status`: `passed`
- `account_candidate_status`: `success`
- `total_seconds`: `1151`
- `download_seconds`: `27`
- `features_seconds`: `33`
- `edge_engine_seconds`: `49`
- `walk_forward_seconds`: `9`
- `account_simulator_seconds`: `586`
- `account_oos_seconds`: `430`
- `account_candidate_seconds`: `2`
- `peak_memory_mb`: `3.336`

Data quality remoto:

- `files_checked`: `10`
- `stale_pairs`: `[]`
- `incomplete_pairs`: `[]`
- `missing_candles`: `0`
- `nonfinite_ohlcv`: `0`
- `max_lag_bars`: `0`

Account validation remoto:

- `status`: `passed`
- `negative_cash_rows`: `0`
- `equity_identity_failures`: `0`
- `exposure_violations`: `0`
- `position_limit_violations`: `0`
- `duplicate_trade_ids`: `0`
- `nonfinite_rows`: `0`
- `trades_checked`: `7824`

Account OOS validation remoto:

- `status`: `passed`
- `folds_checked`: `24`
- `trades_outside_test`: `0`
- `duplicate_folds`: `0`
- `aggregate_mismatches`: `0`
- `mixed_run_ids`: `0`

Account candidate remoto:

- `status`: `success`
- `rows`: `8`
- `eligible_candidates`: `0`
- `candidate_count`: `0`
- `watch_count`: `2`
- `reject_count`: `6`
- `insufficient_history_count`: `8`

Configuraciones en `watch` pero no elegibles por falta de historico:

- `ema_trend_20_50_100 4h`
  - full PF: `1.126638`
  - OOS folds: `3`
  - OOS positive fold rate: `0.666667`
  - OOS median PF: `1.131329`
  - OOS trades: `288`
- `volatility_expansion_20 4h`
  - full PF: `1.065653`
  - OOS folds: `3`
  - OOS positive fold rate: `0.666667`
  - OOS median PF: `1.174805`
  - OOS trades: `118`

Estado final de servicios:

- `adrian-quant-lab.service`: `active`
- `adrian-quant-ingest.timer`: `active`
- `adrian-quant-research.timer`: `active`
- `adrian-quant-deep.timer`: `inactive`
- `adrian-quant-pipeline.timer`: `inactive`
- `adrian-quant-promotion.timer`: `inactive`
- Dashboard:
  - `HTTP/1.1 200 OK`
- Paper containers:
  - ninguno encontrado con `freqtrade-paper-promoted`

## No implementado todavia

- Historico 2021-2022.
- Perfil Freqtrade-compatible.
- Estrategia equivalente.
- Parity test.
- Paper trading.
