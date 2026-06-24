# Corrida 2026-06-17 - Reproducibilidad, data quality y pipeline seguro

## Solicitud del usuario

Convertir la hoja de ruta del laboratorio cuantitativo en avances concretos, priorizando:

- `run_id` unico por corrida.
- `run_manifest.json`.
- resultados historicos por corrida.
- gate real de calidad de datos.
- proteccion contra condiciones de carrera.
- promocion a paper dependiente de una corrida completa.
- documentacion actualizada.
- dejar un `.md` por cada corrida explicando lo realizado.

## Cambios implementados

### 1. Reproducibilidad por corrida

Se agrego `research_lab/run_manager.py`.

Ahora cada corrida crea un `run_id` con este formato:

```text
YYYYMMDDTHHMMSSZ-<git_commit_o_nogit>-<uuid6>
```

El manifest registra:

- `run_id`
- estado (`running`, `success`, `failed`)
- tiempos de inicio y fin
- version de Python
- version de Freqtrade
- hash de requirements
- hash de datos
- hash de features
- version de features
- version de edge registry
- version de reglas de decision
- version de reglas de promocion
- semilla fija
- ultima vela cerrada validada

Los resultados quedan copiados en:

```text
research_lab/storage/results/runs/<run_id>/
research_lab/storage/results/latest/
```

### 2. Gate de calidad de datos

Se agrego `research_lab/data_quality.py`.

Valida por par y timeframe:

- fechas ordenadas
- duplicados
- fechas futuras
- velas potencialmente incompletas
- OHLC valido
- precios positivos
- volumen no negativo
- cantidad minima de filas
- gaps temporales
- freshness del ultimo dato

Tambien valida el resample `1h -> 4h`:

- `open_4h = primer open`
- `high_4h = maximo high`
- `low_4h = minimo low`
- `close_4h = ultimo close`
- `volume_4h = suma de volumen`

Si se ejecuta con `--fail-on-error` y falla, el pipeline se detiene antes de generar features o edges.

### 3. Escrituras mas seguras

Se agrego escritura atomica para Parquet en `research_lab/warehouse.py`.

Los archivos grandes se escriben primero como temporal y luego se renombran al destino final. Esto reduce el riesgo de que DuckDB, dashboard o promocion lean un archivo incompleto.

Tambien se ajusto promocion para escribir JSON/texto de forma atomica cuando genera:

- decision de promocion
- config paper
- estrategia Freqtrade generada
- docker compose paper
- metadata de promocion

### 4. Pipeline nocturno endurecido

Se actualizo `research_lab/scripts/run_pipeline.sh`.

Nuevo orden:

1. tomar lock con `flock`
2. crear/exportar `RUN_ID`
3. escribir manifest `running`
4. migrar datos si hay `.feather`
5. resample `1h -> 4h`
6. correr `data_quality --fail-on-error`
7. generar features
8. correr `edge_engine --run-id <run_id> --walk-forward`
9. copiar snapshot a `results/runs/<run_id>/` y `results/latest/`
10. cerrar manifest como `success`
11. correr promocion solo si `RUN_PROMOTION=1`

Si algo falla, el manifest se marca como `failed`.

### 5. Edge engine conectado al `run_id`

Se actualizo `research_lab/edge_engine.py`.

Ahora acepta:

```bash
--run-id <run_id>
```

Ese `run_id` queda en:

- `edge_summary.parquet`
- `edge_trades.parquet`
- `walk_forward_summary.parquet`
- `run_history.parquet`

### 6. Promocion mas segura

Se actualizo `research_lab/promotion.py`.

Ahora la promocion puede recibir:

```bash
--run-id <run_id>
```

Hard gates nuevos:

- el manifest del run debe estar en `success`
- el reporte de calidad de datos debe estar en `passed`
- la decision se escribe con el mismo `run_id`

Tambien se corrigio un problema: el snapshot general ya no copia una `promotion_decision.json` vieja. Esa decision solo la escribe el modulo de promocion para el run correcto.

### 7. Systemd actualizado

Se actualizo:

- `research_lab/systemd/adrian-quant-pipeline.service`
- `research_lab/systemd/adrian-quant-promotion.service`

El pipeline ahora usa `RUN_PROMOTION=1`.

El servicio manual de promocion lee el ultimo `run_id` mediante:

```bash
python -m research_lab.run_manager --storage research_lab/storage latest-id
```

En el homeserver se dejo apagado el timer legado:

```text
adrian-quant-promotion.timer = inactive
```

La promocion ahora ocurre al final del pipeline, no por horario separado.

### 8. Documentacion actualizada

Se actualizaron:

- `research_lab/README.md`
- `GUIA_USO.md`
- `GUIA_PROYECTO.md`

Los documentos ahora explican:

- `run_id`
- `run_manifest.json`
- `data_quality_report.json`
- `results/runs/<run_id>/`
- `results/latest/`
- pipeline con lock
- promocion dependiente del pipeline
- timer de promocion como legado

## Pruebas locales

### Compilacion

Paso:

```bash
python -m py_compile research_lab/run_manager.py research_lab/data_quality.py research_lab/warehouse.py research_lab/edge_engine.py research_lab/promotion.py
```

### Sintaxis del pipeline

Paso:

```bash
bash -n research_lab/scripts/run_pipeline.sh
```

### Data quality local

Paso con `.venv`:

```text
status: passed
pairs_checked: 5
files_checked: 10
missing_candles: 0
duplicates: 0
invalid_ohlc: 0
warnings: 0
last_complete_candle: 2026-06-17T12:00:00+00:00
```

### Pipeline local completo

Se corrio con:

```bash
ROOT_DIR=/home/perdomopro/Desktop/freqtrade VENV_DIR=.venv RUN_PROMOTION=1 bash research_lab/scripts/run_pipeline.sh
```

Resultado local:

```text
run_id: 20260617T194638Z-35293a040-4806a2
run_manifest.status: success
data_quality_status: passed
promotion.status: no_candidate
verdicts: 35 reject, 5 watch
```

Top `watch` local:

```text
volatility_expansion_20 ETH/USDT 4h  PF 1.204  DD -15.88%
volatility_expansion_20 DOGE/USDT 4h PF 1.199  DD -35.67%
volatility_expansion_20 XRP/USDT 4h  PF 1.109  DD -28.51%
ema_trend_20_50_100 BTC/USDT 4h      PF 1.019  DD -31.80%
donchian_20_10 BTC/USDT 4h           PF 1.003  DD -30.08%
```

No hubo candidatos promovibles.

## Despliegue en homeserver

Se subio al homeserver el paquete sin `research_lab/storage/`.

Ruta remota:

```text
/home/adrian/freqtrade
```

Se respaldo la version anterior en:

```text
/tmp/adrian_quant_backup/
```

Se compilo el codigo remoto y paso.

Se corrio pipeline remoto completo con promocion:

```text
run_id: 20260617T194833Z-nogit-9505c5
run_manifest.status: success
data_quality_status: passed
promotion.status: no_candidate
verdicts: 35 reject, 5 watch
dashboard: HTTP/1.1 200 OK
```

Servicios remotos verificados:

```text
adrian-quant-lab.service: active
adrian-quant-pipeline.timer: active
adrian-quant-promotion.timer: inactive
```

Siguiente ejecucion automatica:

```text
pipeline timer: 2026-06-18 03:30 CST
```

## Observaciones importantes

No se levanto ningun contenedor paper nuevo. El gate devolvio `no_candidate`.

Esto es correcto: hay edges en `watch`, pero ninguno pasa todos los gates de promocion.

`donchian_20_10 SOL/USDT 4h` sigue teniendo PF interesante, pero su drawdown temporal es demasiado alto, por eso debe seguir rechazado.

## Pendientes tecnicos

Siguientes bloques recomendados:

1. `account_simulator.py`: backtest de cuenta real con cash, equity, exposicion y posiciones simultaneas.
2. tests unitarios y golden datasets.
3. WFO real con seleccion de parametros en train y evaluacion en test.
4. multiple testing: Deflated Sharpe, PBO/CSCV y bootstrap por bloques.
5. parity test entre edge engine y Freqtrade.
6. paper monitor y despromocion automatica.
