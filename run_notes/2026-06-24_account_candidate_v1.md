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
  - `52 passed`
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
- Falta desplegar al homeserver y confirmar con la corrida remota actual que devuelve:
  - `status = success`
  - `candidate_count = 0`
  - `insufficient_history_count > 0`

## No implementado todavia

- Historico 2021-2022.
- Perfil Freqtrade-compatible.
- Estrategia equivalente.
- Parity test.
- Paper trading.
