# Bitacora - ampliacion de historico 2021

Fecha: 2026-06-27

## Objetivo

Ampliar el historico de KuCoin sin cambiar reglas de decision, account rules,
edges, costes, stops, take-profit, universo ni ventanas OOS. El objetivo fue
comprobar si los resultados `watch` de Account Candidate v1 sobreviven con mas
folds fuera de muestra.

## Linea base congelada

- Tag local/remoto creado: `account-candidate-v1-baseline`.
- Commit base etiquetado: `65ca14e36`.
- Run remoto validado antes de ampliar historico:
  `20260627T013209Z-nogit-1c896a`.
- Resultado base: `candidate_count=0`, `watch_count=2`,
  `insufficient_history_count=8`.

## Cambios de codigo previos al despliegue

- Commit desplegado: `9c517c5f7321449792a5b6d9f5e24c593b4a8748`
  (`Track pair coverage in account OOS`).
- Paquete desplegado: `/tmp/adrian_quant_pair_coverage.tgz`.
- SHA256 del paquete:
  `19aec56ef07a1fd11bb60d666d09bd0349f104d533386d42f2abf736326601f9`.
- Se agregaron metricas de cobertura por fold en `account_oos_summary`:
  `available_pairs`, `missing_pairs`, `available_pair_count`,
  `universe_pair_count`, `pair_coverage_rate`.
- Se agregaron metricas agregadas:
  `min_pair_coverage_rate`, `median_pair_coverage_rate`.
- Se agrego regla de elegibilidad:
  `min_pair_coverage_rate=0.60`.
- Tests remotos despues del despliegue: `59 passed`.

## Respaldo remoto

- Respaldo de codigo sin storage:
  `/home/adrian/freqtrade_backups/adrian_quant_code_backup_20260627T022651Z`.
- Respaldo de datos y storage:
  `/home/adrian/freqtrade_backups/adrian_quant_history_backup_20260627T022847Z`.
- Tamano del respaldo historico: `331M`.
- Imagen usada para descarga: `freqtradeorg/freqtrade:stable`.
- Digest local registrado:
  `freqtradeorg/freqtrade@sha256:99942e0b5788bc2fcc9f822e48b815b6984a1392f3f5bacbf8b38214e1722190`.

## Rango de datos

Antes de ampliar:

- BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT iniciaban en
  `2023-03-05 23:00:00`.

Despues de descargar 2022 y 2021 con `--prepend`:

- BTC/USDT: `2021-01-01 00:00:00` a `2026-06-26 22:00:00`.
- ETH/USDT: `2021-01-01 00:00:00` a `2026-06-26 22:00:00`.
- XRP/USDT: `2021-01-01 00:00:00` a `2026-06-26 22:00:00`.
- DOGE/USDT: `2021-02-09 03:00:00` a `2026-06-26 22:00:00`.
- SOL/USDT: `2021-08-04 10:00:00` a `2026-06-26 22:00:00`.

## Ingest manual posterior al historico

Run: `ingest-20260627T023155Z-nogit-177545`.

- `status=success`.
- `data_quality_status=passed`.
- `max_lag_bars=0`.
- `total_seconds=37`.

Despues de reactivar el timer, `Persistent=true` disparo un ingest adicional:

Run: `ingest-20260627T151024Z-nogit-e70941`.

- `status=success`.
- `data_quality_status=passed`.
- `expected_last_complete_candle=2026-06-27T08:00:00+00:00`.
- `actual_last_available_candle=2026-06-27T08:00:00+00:00`.
- `max_lag_bars=0`.
- `total_seconds=35`.

## Research remoto con historico ampliado

Run: `20260627T023351Z-nogit-38808c`.

- `status=success`.
- `data_quality_status=passed`.
- `account_validation_status=passed`.
- `account_oos_validation_status=passed`.
- `account_candidate_status=success`.
- `source_commit=9c517c5f7321449792a5b6d9f5e24c593b4a8748`.
- `deployment_package_hash=19aec56ef07a1fd11bb60d666d09bd0349f104d533386d42f2abf736326601f9`.
- `total_seconds=2437` (~40.6 min).
- `account_simulator_seconds=955` (~15.9 min).
- `account_oos_seconds=1314` (~21.9 min).
- Pico observado por systemd durante la corrida: ~1.9 GB.

Validacion de cuenta:

- `trades_checked=12631`.
- `equity_rows_checked=240368`.
- `negative_cash_rows=0`.
- `equity_identity_failures=0`.
- `exposure_violations=0`.
- `position_limit_violations=0`.
- `duplicate_trade_ids=0`.
- `nonfinite_rows=0`.

Validacion Account OOS:

- `folds_checked=56`.
- `summary_rows=56`.
- `trade_rows=8401`.
- `equity_rows=153600`.
- `trades_outside_test=0`.
- `duplicate_folds=0`.
- `mixed_run_ids=0`.
- `nonfinite_rows=0`.
- `aggregate_mismatches=0`.
- `pair_coverage_violations=0`.

## Resultado Account Candidate

- `candidate_count=0`.
- `watch_count=3`.
- `reject_count=5`.
- `insufficient_history_count=0`.
- `eligible_candidates=8`.

Los `watch` actuales son:

| Edge | TF | Folds | OOS positive rate | OOS median return | OOS median PF | Worst OOS DD | Full PF | Full DD |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `ema_trend_20_50_100` | 4h | 7 | 57.14% | 4.05% | 1.1753 | -9.71% | 1.1071 | -16.70% |
| `volatility_expansion_20` | 4h | 7 | 57.14% | 1.90% | 1.2208 | -5.16% | 1.0975 | -9.72% |
| `donchian_20_10` | 4h | 7 | 57.14% | 0.71% | 1.0340 | -11.02% | 1.0014 | -23.84% |

Ningun edge paso a `candidate`. La razon principal en los tres `watch` fue
`oos_positive_fold_rate=4/7=57.14%`, por debajo del minimo de 60%.
En `volatility_expansion_20` tambien falto `full_profit_factor` por poco:
`1.0975 < 1.10`.

## Cobertura por fold

El primer test OOS empieza en 2022-06-30/2022-07-01, despues de la fecha de
inicio disponible de SOL y DOGE. Por eso todos los folds evaluados tienen
`available_pair_count=5`, `universe_pair_count=5` y `pair_coverage_rate=1.0`.

## Timers y estado operativo

Estado final verificado:

- `adrian-quant-lab.service`: `active`.
- `adrian-quant-ingest.timer`: `active`.
- `adrian-quant-research.timer`: `active`.
- `adrian-quant-deep.timer`: `inactive`.
- `adrian-quant-pipeline.timer`: `inactive`.
- `adrian-quant-promotion.timer`: `inactive`.
- Dashboard local: `HTTP/1.1 200 OK`.
- No existe contenedor `freqtrade-paper-promoted`.

Proximos timers:

- Ingest: `Sun 2026-06-28 03:30:40 CST`.
- Research: `Sun 2026-06-28 05:00:59 CST`.

## Observaciones tecnicas

- La ampliacion historica cumplio el objetivo: paso de historia insuficiente a
  7 folds OOS por combinacion.
- Los dos `watch` previos sobrevivieron y se agrego `donchian_20_10 4h` como
  tercer `watch`.
- No se deben relajar reglas para convertirlos en candidatos. Estan cerca, pero
  no cumplen el gate versionado.
- `account_oos` quedo como nuevo cuello de botella: ~21.9 min. Antes de ampliar
  universo o perfiles conviene optimizar el OOS para no volver a preparar y
  recorrer features completas por cada edge/fold.
- La siguiente fase recomendada sigue siendo `Account Execution Profiles v1`:
  separar `research/pro_rata` de `freqtrade/sequential_deterministic`.
