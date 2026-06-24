# Guía de uso - Adrian Quant Lab

Esta guía es operativa. Es para usar el laboratorio día a día desde tus laptops, PC o el
homeserver. La referencia técnica completa está en `GUIA_PROYECTO.md`.

> Última actualización: **2026-06-17** (frescura estricta, eventos de señal, account
> simulator v1, promoción bloqueada por parity test).

## 1. Abrir la GUI

Desde cualquier equipo conectado a Tailscale:

```text
http://homeserver:8501
```

Si no abre:

```bash
ssh adrian@homeserver
systemctl status adrian-quant-lab.service
journalctl -u adrian-quant-lab.service -n 80 --no-pager
sudo systemctl restart adrian-quant-lab.service    # reiniciar GUI
```

## 2. Qué significa cada pestaña

### Overview

Ranking principal. Columnas importantes:

- `verdict`: `reject`, `watch`, `candidate`.
- `score`: ranking 0-100.
- `PF`: profit factor.
- `expectancy`: retorno medio por trade.
- `max_drawdown`: drawdown temporal por barra.
- `mc_prob_negative`: probabilidad Monte Carlo de terminar negativo.
- `stress_pf_5bps`: PF si agregamos 5 bps de coste.

Lectura: `reject` = no tocar; `watch` = investigar más; `candidate` = potencial, pero
todavía revisar playbook (y hoy la promoción está bloqueada, ver §6).

### Robustez

Aquí se detecta si el lab se está engañando. Mira:

- `entry_signals`: cuántas señales genera.
- `activity_state`: si está activo o muerto.
- `expected_maker_fill_rate`: fill maker estimado.
- `tested_combinations`: cuántas combinaciones probaste.
- `rules_version`: versión de las reglas (`lab-rules-v1`).

Si un edge tiene pocos trades o pocas señales, no es edge: es muestra débil.

### Heatmap

Compara pares y timeframes:

1. Selecciona un edge.
2. Cambia métrica a `profit_factor`, `score`, `max_drawdown` o `expectancy`.
3. Busca consistencia, no solo un número alto aislado.

### Walk-forward

Nombre técnico actual: `temporal_oos_fixed_params`. Esto **no** optimiza parámetros en
train todavía; solo mide estabilidad temporal con parámetros fijos. Busca: muchos folds
positivos, PF mediano > 1, suficientes trades test, peor DD razonable.

### Liga

Resultados por año. La pregunta importante: ¿funciona en varios años o solo en uno? Un edge
que solo gana en 2024 no es robusto.

### Trades

Detalle de una configuración: curva de equity, drawdown, retornos por trade, razones de
salida. Si la curva depende de 2 trades gigantes, cuidado.

### Warehouse

Estado interno: manifiesto OHLCV, manifiesto features, run history, promotion decision.
Aquí ves si el sistema está actualizado (frescura, último `run_id`).

## 3. Pipeline manual

En el homeserver:

```bash
ssh adrian@homeserver
cd /home/adrian/freqtrade
source .venv-lab/bin/activate
DOWNLOAD_METHOD=docker bash research_lab/scripts/run_pipeline.sh
```

El pipeline hace, en orden:

1. toma un lock (`flock`) para no solaparse,
2. crea `run_id` y escribe manifest `running`,
3. **descarga datos** (1h, por Docker en el server) si `DOWNLOAD_DATA=1`,
4. migra datos si hay `.feather`,
5. crea `4h` (solo buckets completos de 4 velas),
6. **valida calidad de datos con gate estricto** (`--fail-on-error`),
7. genera features,
8. corre edges + validación temporal + eventos de señal (`edge_signals.parquet`),
9. **simula la cuenta real** (`account_simulator`),
10. guarda snapshot en `results/runs/<run_id>/` y `results/latest/`,
11. cierra `run_manifest.json` como `success` o `failed`,
12. revisa promoción a paper **solo si `RUN_PROMOTION=1`** (en el server está en `0`).

Ver la última corrida:

```bash
python -m research_lab.run_manager --storage research_lab/storage latest-id
cat research_lab/storage/results/latest/run_manifest.json
```

> En la laptop, para correr offline (sin descargar), agrega `DOWNLOAD_DATA=0`.

## 4. Pipeline nocturno

Corre solo a las **03:30**. Estado:

```bash
systemctl status adrian-quant-pipeline.timer
systemctl list-timers | grep adrian
```

Forzar ahora:

```bash
sudo systemctl start adrian-quant-pipeline.service
```

Logs:

```bash
journalctl -u adrian-quant-pipeline.service -n 160 --no-pager
```

## 5. Smoke test

Usa esto si algo parece roto:

```bash
cd /home/adrian/freqtrade
ROOT_DIR=/home/adrian/freqtrade VENV_DIR=.venv-lab \
PAIR=BTC/USDT TIMEFRAME=1h \
bash research_lab/scripts/smoke_test.sh
```

Debe terminar con `Smoke test OK`.

## 6. Promoción a Freqtrade paper

El gate de promoción corre como **último paso del pipeline** (no por horario separado),
solo cuando `RUN_PROMOTION=1` y la corrida terminó bien.

> **Estado actual: la promoción está BLOQUEADA por diseño.** Falta el parity test
> (`require_parity_test=true`), así que el gate devuelve `status: blocked, reason: Missing
> parity test report`. Esto es intencional: no se promueve nada hasta poder demostrar que
> la estrategia generada reproduce lo investigado.

Ver estado:

```bash
cat research_lab/storage/results/promotion_decision.json
cat research_lab/storage/results/deployment_manifest.json   # si existe
journalctl -u adrian-quant-pipeline.service -n 160 --no-pager
```

Forzar revisión manual (usa el último `run_id`):

```bash
sudo systemctl start adrian-quant-promotion.service
```

El timer separado `adrian-quant-promotion.timer` es **legado** y debe quedar **inactivo**.

Hard gates antes de paper:

- `run_manifest.status == success`;
- `data_quality.status == passed`;
- **parity test `passed`** (hoy ausente → bloqueado);
- candidato con score/PF/DD suficientes **y** gates OOS del walk-forward
  (fold rate, PF mediano OOS, expectancy mediana OOS, peor DD OOS, trades OOS);
- solo timeframes y edges permitidos (`4h`; `donchian_20_10`, `ema_trend_20_50_100`,
  `volatility_expansion_20` — `rsi_mean_reversion_25` retirado por ahora);
- solo dry-run.

Cuando pase, el sistema genera estrategia paper (clase única por edge/par/tf/run), config
dry-run, docker compose paper y metadata, y puede levantar `freqtrade-paper-promoted`,
siempre en dry-run.

## 7. Capa de cuenta real (account simulator)

Además del backtest por edge aislado, el pipeline simula **una cuenta de verdad** (cash
limitado, riesgo por trade, máximo de posiciones y exposición). Es una **capa paralela de
auditoría**: todavía no cambia el veredicto ni la promoción.

Artefactos generados (en `results/`): `account_summary.parquet`, `account_trades.parquet`,
`account_equity.parquet`, `account_rejections.parquet`.

Consultar con SQL:

```bash
python -c "import duckdb; con=duckdb.connect('research_lab/storage/lab.duckdb'); \
print(con.sql('SELECT edge,timeframe,trades,total_return,profit_factor,max_drawdown \
FROM account_summary ORDER BY total_return DESC').df())"
```

Reglas de la cuenta en `research_lab/config/account_rules.json` (`account-rules-v1`):
1000 USDT, riesgo 0.5% por trade, máx. 3 posiciones, exposición total 75%, por par 30%,
política intrabar `stop_first`, asignación `pro_rata`, fin de datos `mark_to_market`.

## 8. Diagnóstico rápido

| Síntoma | Acción |
|---|---|
| GUI no abre | `systemctl status adrian-quant-lab.service`; reiniciar; revisar Tailscale. |
| `data_quality: failed` con `stale_data` | Datos viejos. Corre `download-data` / espera; para aislar pruebas sube `--max-lag-bars`. |
| `incomplete_candles_present` | Hay velas sin cerrar. Re-descarga o espera al cierre. |
| `missing_target_resample` | Falta `4h` de algún par. Corre `warehouse resample`. |
| Promoción `blocked` | Esperado: falta el parity test. |
| Descarga falla en el server | Usa `DOWNLOAD_METHOD=docker` (el `.venv-lab` no tiene freqtrade). |
| Pipeline marcado `failed` | `journalctl -u adrian-quant-pipeline.service`; el `trap` marca failed si cualquier paso revienta. |

## 9. Reglas de seguridad

- No operar live desde este lab.
- No promover una estrategia por un solo PF alto.
- No confiar en una estrategia con pocos trades.
- No ignorar drawdown temporal.
- No cambiar reglas de decisión sin subir versión (`lab-rules-v*`, `promotion-rules-v*`,
  `account-rules-v*`).
- No habilitar el timer de promoción legado.
- No desbloquear la promoción hasta que exista el parity test.
- Todo candidato debe documentarse en `quant_playbook/` y cada sprint en `run_notes/`.
