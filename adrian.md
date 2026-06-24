# Adrian - Guia principiante del proyecto cuantitativo con Freqtrade

Estado: junio 2026

Este documento explica, en lenguaje de principiante, que se hizo hasta ahora, que aprendimos, que significan los conceptos importantes y cual es el plan razonable para seguir. La idea central es dejar de "inventar una estrategia" por intuicion y empezar a construir un laboratorio que mida si una idea realmente tiene ventaja.

Nada de esto es asesoria financiera. Es ingenieria de investigacion cuantitativa. El objetivo no es adivinar el mercado, sino crear un proceso disciplinado para probar hipotesis.

---

## 1. Resumen ejecutivo

Hasta ahora se intento construir una estrategia llamada `QuantScalper_SXD`, orientada a comprar rebotes en crypto usando mean reversion, pin-bars, bandas de Bollinger, RSI, filtros de regimen y contexto de BTC/ETH.

La estrategia fue mejorada tecnicamente, pero las pruebas no demostraron un edge robusto.

Resultado importante:

- El codigo ahora es mas serio.
- La infraestructura de validacion mejoro mucho.
- Pero la idea base todavia no gana de forma confiable.
- Por lo tanto, no conviene operar esta estrategia con dinero real.

La conclusion correcta no es "todo fallo". La conclusion correcta es:

> Ya tenemos una primera version del laboratorio, y el laboratorio hizo su trabajo: rechazo una estrategia que no tenia suficiente evidencia.

Eso es bueno. Un mal sistema te dejaria operar algo peligroso. Un buen sistema te dice "no operes" cuando la evidencia no alcanza.

---

## 2. Que podriamos empezar hoy

Hoy no empezaria creando otra estrategia compleja desde cero.

Empezaria con algo mas limpio:

> Construir un laboratorio de estrategias base conocidas.

El primer objetivo no es ganar dinero todavia. El primer objetivo es responder esta pregunta:

> Que tipos de estrategias sobreviven mejor en nuestros datos de crypto?

Para eso, la primera estrategia que implementaria hoy seria:

## Estrategia 1: Donchian Breakout

Es una estrategia clasica de seguimiento de tendencia.

Idea simple:

- Si el precio rompe el maximo de los ultimos N periodos, compra.
- Si el precio cae por debajo del minimo de los ultimos N periodos, vende o cierra.

Ejemplo:

- Timeframe: `1h`
- Compra si el cierre rompe el maximo de las ultimas `20` velas.
- Cierra si pierde el minimo de las ultimas `10` velas.
- Usa filtro BTC para evitar operar altcoins cuando BTC esta claramente debil.

Por que empezaria con Donchian:

- Es facil de entender.
- No depende de patrones subjetivos.
- Es una estrategia documentada historicamente.
- Sirve para medir si crypto esta pagando tendencias en ciertos pares y timeframes.

Despues probaria estas otras estrategias base:

1. Donchian Breakout
2. EMA Trend Following
3. Volatility Breakout
4. RSI Mean Reversion
5. BTC Regime Filter Strategy

La prioridad seria probar primero en timeframes mas tranquilos:

- `1h`
- `4h`
- luego `15m`

No empezaria con `5m`, porque en `5m` los costes, el slippage, el ruido y los problemas de fills destruyen muchas ideas.

---

## 3. Archivos actuales del proyecto

Estos son los archivos importantes creados o modificados:

### `user_data/strategies/QuantScalper_SXD.py`

Es la estrategia para Freqtrade.

Freqtrade usa este archivo para decidir:

- cuando comprar,
- cuando vender,
- cuanto capital usar,
- donde poner stoploss,
- si debe evitar una entrada por mala liquidez,
- si el mercado esta en buen o mal regimen.

Esta estrategia ya no es una version basica. Tiene filtros avanzados:

- regimen continuo,
- volatilidad compuesta,
- volumen normalizado,
- VWAP de sesion,
- orderbook en live/dry-run,
- position sizing dinamico,
- stops y salidas personalizadas.

Pero aunque el codigo sea mas sofisticado, el backtest salio negativo. Eso enseña una leccion clave:

> Codigo avanzado no significa edge.

### `research_edge_study.py`

Este archivo sirve para estudiar si una idea tiene ventaja antes de convertirla en estrategia.

Mide cosas como:

- probabilidad de rebote despues de una señal,
- expectativa neta despues de costes,
- diferencia entre maker y taker,
- cuantas señales aparecen,
- cuantos fills habria tenido una orden maker,
- que pasa si quitamos cada filtro.

Este archivo es importante porque evita construir una estrategia completa alrededor de una idea que no funciona.

### `quant_validation.py`

Este archivo valida resultados despues del backtest.

Calcula metricas como:

- Profit Factor,
- Expectancy,
- Sharpe,
- Sortino,
- Max Drawdown,
- Monte Carlo,
- stress test de costes,
- edge decay.

Este archivo responde una pregunta distinta:

> Si ya tengo trades, que tan buenos, robustos o peligrosos son?

### `config.kucoin.example.json`

Es la configuracion de Freqtrade para correr la estrategia.

Define cosas como:

- exchange: KuCoin,
- pares: SOL/USDT, XRP/USDT, DOGE/USDT,
- capital simulado,
- comisiones,
- modo dry-run,
- tipo de orden,
- rutas de datos.

---

## 4. Que aprendimos de QuantScalper_SXD

La hipotesis original era:

> Cuando una altcoin toca la banda inferior de Bollinger, tiene RSI bajo y forma una vela tipo pin-bar, puede rebotar.

Eso es mean reversion.

Mean reversion significa:

> Comprar cuando el precio se ha alejado demasiado hacia abajo, esperando que vuelva a una zona normal.

El problema es que en crypto muchas caidas no son "rebotes baratos". A veces son el inicio de una tendencia bajista fuerte. Eso se llama comprar un cuchillo cayendo.

La estrategia intento evitar eso con:

- filtro de BTC,
- filtro de ETH,
- crash block,
- regimen de mercado,
- volumen,
- pin-bar,
- orderbook,
- stoploss.

Pero las pruebas mostraron que eso no fue suficiente.

Resultados aproximados del backtest limpio:

- Trades: `162`
- Resultado total de wallet: alrededor de `-24%`
- Winrate: alrededor de `29.6%`
- Profit Factor: alrededor de `0.26`
- Max Drawdown: alrededor de `24%`

Interpretacion:

- La estrategia perdio dinero.
- Gano pocas veces.
- Las ganancias no compensaron las perdidas.
- El trailing stop fue la mayor fuente de salidas negativas.
- La idea base no quedo demostrada.

Conclusion:

> No deberiamos operar QuantScalper_SXD con dinero real en su estado actual.

---

## 5. Conceptos fundamentales

Esta seccion explica los conceptos del area desde cero.

## 5.1 Estrategia

Una estrategia es un conjunto de reglas.

Ejemplo:

- Si pasa A, compra.
- Si pasa B, vende.
- Si pierdes X%, corta la perdida.
- Si ganas Y%, toma ganancia.

Una estrategia no es una prediccion. Es un sistema de decisiones.

## 5.2 Edge

Edge significa ventaja estadistica.

Una estrategia tiene edge si, despues de muchas operaciones y despues de costes, su expectativa es positiva.

Ejemplo simple:

- Pierdes 1 dolar cuando fallas.
- Ganas 2 dolares cuando aciertas.
- Aciertas 40% de las veces.

Expectancy:

```text
(0.40 * 2) - (0.60 * 1) = 0.80 - 0.60 = +0.20
```

Eso tiene edge.

Pero si las comisiones y slippage comen esos 0.20, el edge desaparece.

## 5.3 Expectancy

Expectancy es la ganancia o perdida esperada por trade.

Formula simple:

```text
Expectancy = (Winrate * Ganancia promedio) - (Lossrate * Perdida promedio)
```

Una estrategia puede tener winrate bajo y aun ganar dinero si sus ganancias son mucho mayores que sus perdidas.

Tambien puede tener winrate alto y perder dinero si sus perdidas son enormes.

## 5.4 Profit Factor

Profit Factor compara ganancias brutas contra perdidas brutas.

```text
Profit Factor = Ganancias totales / Perdidas totales
```

Interpretacion general:

- PF menor a `1.0`: pierde dinero.
- PF `1.0`: casi neutral antes de otros riesgos.
- PF `1.2` a `1.4`: puede ser interesante si es robusto.
- PF mayor a `2.0`: sospechoso si viene de sobreoptimizacion o pocos trades.

No buscamos un PF perfecto. Buscamos uno creible y estable.

## 5.5 Backtest

Un backtest prueba una estrategia sobre datos historicos.

Sirve para responder:

> Que habria pasado si hubiera operado estas reglas en el pasado?

Pero tiene peligros:

- Puedes sobreajustar parametros al pasado.
- Puedes ignorar costes reales.
- Puedes asumir fills imposibles.
- Puedes creer que el futuro sera igual al pasado.

Un backtest positivo no prueba que ganaras dinero. Solo dice que la idea merece mas investigacion.

## 5.6 Walk-forward

Walk-forward es una forma mas seria de probar.

En vez de optimizar todo sobre el mismo periodo, divides el tiempo:

```text
Entrenar en 2022 -> Probar en 2023
Entrenar en 2023 -> Probar en 2024
Entrenar en 2024 -> Probar en 2025
```

La idea es simular como trabajarias en la vida real:

- solo conoces el pasado,
- eliges parametros,
- luego ves como funciona en el futuro.

Si una estrategia solo funciona en el periodo donde fue optimizada, probablemente esta sobreajustada.

## 5.7 Overfitting

Overfitting significa ajustar demasiado al pasado.

Ejemplo:

Pruebas miles de combinaciones:

- RSI 31,
- wick 0.62,
- ATR 2.7,
- horario especifico,
- par especifico,
- fecha especifica.

Eventualmente encontraras algo que gano en el pasado por suerte.

Pero cuando lo operas en vivo, falla.

Regla practica:

> Mientras mas parametros optimices, mas facil es engañarte.

## 5.8 Monte Carlo

Monte Carlo consiste en simular muchos futuros posibles usando los trades historicos.

Ejemplo:

Tienes 200 trades. Los reordenas miles de veces para ver:

- peor racha probable,
- drawdown probable,
- probabilidad de terminar negativo,
- probabilidad de ruina.

Esto ayuda porque una estrategia puede verse bien en promedio, pero tener rachas de perdida muy dificiles de soportar.

## 5.9 Drawdown

Drawdown es la caida desde un maximo de capital hasta un minimo.

Ejemplo:

- Tu cuenta sube a 1000.
- Luego cae a 750.
- Drawdown = 25%.

El drawdown importa porque mide dolor real.

Una estrategia puede ser rentable a largo plazo, pero si tiene drawdowns demasiado grandes, tal vez no puedas operarla psicologicamente.

## 5.10 Slippage

Slippage es la diferencia entre el precio que esperabas y el precio real donde se ejecuta la orden.

Ejemplo:

- Quieres comprar a 100.
- Tu orden se ejecuta a 100.20.
- Ese 0.20% extra es slippage.

En estrategias de poco margen, el slippage puede destruir todo el edge.

## 5.11 Maker y taker

Una orden maker agrega liquidez al libro.

Ejemplo:

- Pones una orden limit esperando que el precio llegue.
- Si alguien te compra o vende contra esa orden, fuiste maker.

Ventaja:

- normalmente pagas menos comision.

Desventaja:

- tal vez nunca se llena.

Una orden taker quita liquidez.

Ejemplo:

- Compras al precio disponible ahora mismo.

Ventaja:

- entras casi seguro.

Desventaja:

- pagas mas comision y slippage.

Leccion:

> Maker no siempre es mejor. Si las mejores operaciones no te llenan, puedes destruir el edge.

Por eso agregamos un estudio maker vs taker.

## 5.12 Fill rate

Fill rate es el porcentaje de ordenes que realmente se ejecutan.

Ejemplo:

- Hay 100 señales.
- Pones 100 ordenes maker.
- Solo 35 se llenan.

Fill rate = 35%.

Eso cambia completamente el resultado real de la estrategia.

## 5.13 Regimen de mercado

Regimen significa el tipo de mercado actual.

Ejemplos:

- tendencia alcista,
- tendencia bajista,
- rango lateral,
- alta volatilidad,
- baja volatilidad.

Un error comun es pensar que una estrategia debe operar siempre.

En realidad muchas estrategias solo funcionan en ciertos regimenes.

Ejemplo:

- Mean reversion puede funcionar en rango.
- Breakout puede funcionar en tendencia.
- Momentum puede funcionar en mercados fuertes.

Por eso el plan ahora es medir estrategias por regimen.

## 5.14 Regimen continuo

Antes se usaba algo discreto:

```text
0 = bear extreme
1 = bear
2 = high vol
3 = range
4 = bull slow
5 = bull impulsive
```

Eso puede ser brusco.

Ahora usamos una idea mas suave:

```text
Bull = 72%
Range = 20%
Bear = 8%
```

Esto evita cambios violentos entre una vela y otra.

## 5.15 Volatilidad

Volatilidad mide cuanto se mueve el precio.

ATR es una medida comun, pero no conviene depender solo de ATR.

Por eso se agregaron varias medidas:

- ATR percentage,
- realized volatility,
- Parkinson volatility,
- EWMA volatility.

La idea es no basar stoploss, sizing y trailing en una sola variable.

## 5.16 Position sizing

Position sizing significa decidir cuanto capital usar en cada trade.

No es lo mismo entrar con:

- 5 dolares,
- 50 dolares,
- 500 dolares.

Una estrategia mediocre con buen sizing puede sobrevivir.

Una estrategia buena con sizing agresivo puede destruir la cuenta.

En este proyecto el sizing deberia depender de:

- volatilidad,
- confianza de la señal,
- riesgo maximo permitido,
- drawdown actual,
- calidad historica del edge.

## 5.17 Kelly reducido

Kelly es una formula para calcular cuanto apostar segun tu ventaja.

Pero Kelly completo suele ser muy agresivo.

Por eso se usa Kelly reducido:

- 25% Kelly,
- 10% Kelly,
- o incluso menos.

Idea:

> Si la ventaja es incierta, arriesga poco.

## 5.18 Feature importance

Feature importance significa medir que aporta cada filtro.

Ejemplo:

La estrategia usa:

- RSI,
- pin-bar,
- BTC filter,
- volumen,
- regimen.

Pero tal vez uno de esos filtros no ayuda.

Entonces se hace una prueba:

- estrategia completa,
- estrategia sin RSI,
- estrategia sin volumen,
- estrategia sin BTC,
- estrategia sin pin-bar.

Si quitar un filtro mejora la expectativa, ese filtro probablemente estaba estorbando.

## 5.19 Edge decay

Edge decay significa que una ventaja deja de funcionar con el tiempo.

Ejemplo:

- Una estrategia funciona en 2022 y 2023.
- Empieza a perder en 2024.
- Sigue perdiendo en 2025.

Puede ser que el mercado cambio.

Un sistema serio debe detectar cuando el edge observado se aleja del edge historico.

---

## 6. Por que no conviene seguir tuneando QuantScalper ahora

Porque el problema principal no parece ser un parametro.

No parece que el error sea simplemente:

- RSI 32 en vez de 30,
- ATR 2.2 en vez de 2.8,
- wick 0.60 en vez de 0.65.

El problema mas serio es que la narrativa base no mostro ventaja robusta.

Narrativa base:

> Comprar pin-bars de rebote en altcoins.

El estudio mostro:

- pocas señales,
- expectancy debil o negativa,
- poca robustez,
- dependencia fuerte del par,
- resultados sensibles a costes.

Seguir optimizando esa idea puede llevar a overfitting.

Mejor decision:

> Guardar QuantScalper como experimento aprendido y pasar a estudiar estrategias base conocidas.

---

## 7. Plan de trabajo recomendado

## Fase 1 - Laboratorio minimo

Objetivo:

Crear un proceso repetible para probar estrategias.

Debe poder responder:

- que estrategia se probo,
- en que par,
- en que timeframe,
- con que parametros,
- con que costes,
- cuantos trades hizo,
- cual fue su expectancy,
- cual fue su drawdown,
- si sobrevivio Monte Carlo,
- si funciono fuera de muestra.

Output ideal:

Una tabla comparativa.

Ejemplo:

| Estrategia | Par | TF | Trades | PF | Expectancy | Max DD | MC negativo | Veredicto |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Donchian | BTC | 1h | 450 | 1.25 | +0.08% | 18% | 22% | Revisar |
| RSI MR | DOGE | 15m | 900 | 0.92 | -0.03% | 35% | 91% | Rechazar |

## Fase 2 - Estrategias base

Implementar estrategias conocidas con cambios minimos:

1. Donchian Breakout
2. EMA Trend Following
3. Volatility Breakout
4. RSI Mean Reversion
5. BTC Regime Filter

No se optimiza agresivamente al inicio.

Primero se busca entender:

- donde funcionan,
- donde fallan,
- que regimen les ayuda,
- que pares son mas sanos,
- que timeframes son menos ruidosos.

## Fase 3 - Walk-forward

Para las estrategias que parezcan prometedoras:

- entrenar en una ventana,
- probar en la siguiente,
- repetir en varios años,
- agregar todos los tramos test.

Solo importa el resultado fuera de muestra.

## Fase 4 - Robustez

Antes de considerar dry-run:

- Monte Carlo,
- stress de costes,
- sensibilidad de parametros,
- resultados por regimen,
- resultados por par,
- drawdown duration,
- edge decay.

## Fase 5 - Dry-run

Solo si una estrategia sobrevive:

- backtest,
- walk-forward,
- Monte Carlo,
- costes conservadores,
- sensibilidad,
- suficientes trades,

entonces se pasa a dry-run.

Dry-run significa operar en tiempo real sin dinero real.

Esto prueba:

- si el bot corre bien,
- si las señales aparecen como esperamos,
- si hay errores de datos,
- si los fills simulados son realistas,
- si la estrategia se comporta parecido al backtest.

## Fase 6 - Capital real minimo

Solo despues de dry-run.

Y con capital pequeno.

La meta no es ganar mucho al inicio.

La meta es verificar que:

- las ordenes se ejecutan bien,
- el slippage real no mata el edge,
- el bot no falla,
- el exchange responde bien,
- el drawdown real es tolerable.

---

## 8. Metricas minimas para aprobar una estrategia

No hay reglas perfectas, pero estos filtros son razonables:

## Requisitos minimos

- Profit Factor mayor a `1.15`.
- Expectancy positiva despues de costes.
- Minimo `200` trades para estrategias frecuentes.
- Minimo `80` trades para estrategias lentas.
- Max Drawdown compatible con el tamano de cuenta.
- Resultado positivo en walk-forward.
- Monte Carlo no debe mostrar ruina facil.
- Sensibilidad estable: cambiar parametros un poco no debe destruir todo.

## Mejor zona objetivo

Para una estrategia realista:

- PF entre `1.2` y `1.4`.
- Drawdown controlado.
- Muchas muestras.
- Poca dependencia de un solo par.
- Buen comportamiento fuera de muestra.

No perseguir:

- PF `3.0` con pocos trades,
- curvas perfectas,
- parametros hiper precisos,
- ganancias enormes en un periodo corto.

Eso suele ser overfitting.

---

## 9. Como pensar como investigador cuantitativo

El orden correcto es:

```text
Idea
  ↓
Hipotesis medible
  ↓
Estudio de edge
  ↓
Estrategia simple
  ↓
Backtest
  ↓
Walk-forward
  ↓
Monte Carlo
  ↓
Dry-run
  ↓
Capital real pequeno
```

El orden incorrecto es:

```text
Idea
  ↓
Bot complejo
  ↓
Hyperopt
  ↓
Backtest bonito
  ↓
Dinero real
```

La mayoria de principiantes hacen el segundo camino.

Nosotros queremos hacer el primero.

---

## 10. Que significa ganar en este proyecto

Ganar no significa encontrar una estrategia que duplique la cuenta rapido.

Ganar significa construir un sistema que:

- rechaza malas ideas,
- mide costes,
- detecta overfitting,
- compara estrategias,
- separa train/test,
- identifica regimenes,
- permite mejorar de forma incremental.

Si en 6 meses se consigue una estrategia con:

- PF mayor o igual a `1.3`,
- drawdown razonable,
- walk-forward positivo,
- Monte Carlo aceptable,
- dry-run estable,

eso seria un resultado muy serio.

Aunque el retorno anual parezca "solo" 10% a 20% en backtest realista, eso puede ser mucho mas valioso que una curva exagerada que colapsa en vivo.

---

## 11. Tareas concretas para el proximo sprint

## Sprint 1

Crear primera estrategia base:

- `DonchianBreakout_SXD.py`

Probarla en:

- BTC/USDT,
- ETH/USDT,
- SOL/USDT,
- XRP/USDT,
- DOGE/USDT.

Timeframes iniciales:

- `1h`,
- luego `4h`,
- despues `15m`.

Metricas:

- Profit Factor,
- Expectancy,
- Max Drawdown,
- cantidad de trades,
- Monte Carlo,
- resultados por par.

## Sprint 2

Crear segunda estrategia base:

- `EMATrend_SXD.py`

Compararla contra Donchian.

Pregunta:

> En crypto, para nuestros datos, funciona mejor romper maximos o seguir cruces/tendencias de EMA?

## Sprint 3

Crear una tabla comparativa automatica.

Idealmente:

- correr varios backtests,
- guardar resultados,
- leer resultados con `quant_validation.py`,
- producir una tabla final.

## Sprint 4

Agregar filtros de regimen:

- BTC fuerte/debil,
- volatilidad alta/baja,
- tendencia/rango.

La pregunta no es:

> El filtro suena logico?

La pregunta correcta es:

> El filtro mejora la expectancy fuera de muestra?

---

## 12. Comandos utiles

Activar entorno:

```bash
source .venv/bin/activate
```

Ver si Freqtrade detecta estrategias:

```bash
freqtrade list-strategies --config config.kucoin.example.json
```

Backtest de una estrategia:

```bash
freqtrade backtesting \
  --config config.kucoin.example.json \
  --strategy QuantScalper_SXD \
  --timerange 20220101-20260616 \
  --enable-protections \
  --export trades
```

Validar trades exportados:

```bash
python quant_validation.py \
  --trades user_data/backtest_results/archivo.zip \
  --mc 5000 \
  --stress \
  --edge-decay
```

Estudio de edge:

```bash
python research_edge_study.py \
  --data-dir user_data/data/kucoin \
  --pairs SOL/USDT XRP/USDT DOGE/USDT \
  --target 0.015 \
  --horizon 24
```

---

## 13. Decision actual

Decision sobre `QuantScalper_SXD`:

> No operar.

Motivo:

- backtest negativo,
- expectancy negativa,
- Monte Carlo malo,
- Profit Factor demasiado bajo,
- edge no demostrado.

Decision sobre el proyecto:

> Continuar, pero como laboratorio cuantitativo.

Proximo paso recomendado:

> Implementar Donchian Breakout como primera estrategia base y compararla limpiamente contra QuantScalper.

Ese es el mejor punto de partida para hoy.

---

## 14. Nueva direccion: Adrian Quant Lab

Despues de revisar la recomendacion sobre tu homeserver, la direccion correcta del proyecto queda mas clara.

No queremos construir solamente un bot.

Queremos construir una fabrica de investigacion.

Nombre practico:

```text
Adrian Quant Lab
```

La idea es que tu homeserver haga trabajo repetitivo por ti:

```text
Descargar datos
  -> Convertir a Parquet
  -> Calcular features
  -> Ejecutar edges
  -> Guardar metricas
  -> Mostrar dashboard
```

Esto encaja muy bien con tu servidor:

- Debian 13
- Docker
- Tailscale
- Freqtrade
- 4 GB RAM
- CPU disponible
- bots ya corriendo

El servidor no es una maquina para HFT ni investigacion tick-by-tick pesada, pero si es suficiente para:

- OHLCV 5m, 15m, 1h, 4h
- Parquet
- DuckDB
- pandas
- Streamlit
- ejecuciones nocturnas
- dashboard personal

## 14.1 Lo que ya se construyo

Se creo el modulo:

```text
research_lab/
```

Contiene:

```text
research_lab/
├── indicators.py
├── warehouse.py
├── edge_engine.py
├── dashboard.py
├── scripts/
│   └── run_pipeline.sh
├── systemd/
│   ├── adrian-quant-lab.service
│   ├── adrian-quant-pipeline.service
│   └── adrian-quant-pipeline.timer
└── storage/
```

## 14.2 Warehouse

El warehouse convierte los datos de Freqtrade a Parquet.

Antes:

```text
user_data/data/kucoin/BTC_USDT-1h.feather
```

Ahora:

```text
research_lab/storage/ohlcv/kucoin/BTC_USDT/1h.parquet
```

Ventaja:

- Parquet es mas eficiente que CSV.
- Es rapido.
- Comprime bien.
- DuckDB lo lee directamente.
- Sirve para investigacion repetida.

## 14.3 Feature Generator

El feature generator calcula indicadores una sola vez.

Features actuales:

- RSI
- ATR
- EMAs
- Bollinger Bands
- Donchian Channels
- session VWAP
- volumen normalizado
- realized volatility
- Parkinson volatility
- EWMA volatility
- composite volatility
- bull probability
- bear probability
- range probability
- regime score

Esto evita que cada estrategia recalcule lo mismo.

## 14.4 Edge Engine

El edge engine ejecuta preguntas simples.

Por ahora tiene cuatro edges base:

1. `donchian_20_10`
2. `ema_trend_20_50_100`
3. `rsi_mean_reversion_25`
4. `volatility_expansion_20`

Estos no son estrategias finales.

Son controles de laboratorio.

Sirven para responder:

> Que tipo de comportamiento paga mejor en nuestros datos?

## 14.5 Dashboard

Tambien se creo una GUI con Streamlit:

```text
research_lab/dashboard.py
```

Permite ver:

- ranking de edges
- pares
- timeframes
- Profit Factor
- expectancy
- drawdown
- curva de equity
- distribucion de trades
- razones de salida

Esto es importante porque no quieres vivir abriendo CSV.

Quieres abrir una pagina desde:

- laptop 1
- laptop 2
- PC de escritorio
- telefono si estas en Tailscale

## 14.6 Como abrir la GUI

Comando:

```bash
source .venv/bin/activate
streamlit run research_lab/dashboard.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false
```

Desde Tailscale:

```text
http://homeserver:8501
```

O:

```text
http://IP_TAILSCALE:8501
```

## 14.7 Primer resultado del laboratorio

Se corrio el primer ranking en `1h` usando:

- BTC/USDT
- ETH/USDT
- SOL/USDT
- XRP/USDT
- DOGE/USDT

Resultado importante:

> Ninguno de los edges base queda aprobado todavia.

Los mejores resultados iniciales siguen teniendo Profit Factor menor que `1.0`.

Eso significa:

- el laboratorio funciona,
- las reglas actuales son demasiado simples o mal calibradas,
- todavia no hay edge operable,
- ahora podemos mejorar desde evidencia, no desde intuicion.

Esto es exactamente lo que queriamos.

Despues se agrego `4h` derivado desde los datos `1h`, y el resultado cambio de forma importante.

Primeros resultados relevantes en `4h`:

| Edge | Par | TF | Trades | PF | Expectancy | Max DD | Lectura |
|---|---:|---:|---:|---:|---:|---:|---|
| volatility_expansion_20 | ETH/USDT | 4h | 104 | 1.20 | +0.136% | -15.6% | Candidato inicial |
| volatility_expansion_20 | DOGE/USDT | 4h | 107 | 1.20 | +0.247% | -31.1% | Candidato inicial |
| donchian_20_10 | SOL/USDT | 4h | 186 | 1.20 | +0.548% | -48.2% | Edge interesante, riesgo alto |

Lectura correcta:

> `4h` parece mucho mas prometedor que `1h`, pero todavia no es permiso para operar.

Motivo:

- falta Monte Carlo por edge,
- falta sensibilidad de parametros,
- falta comparar costes mas agresivos,
- falta separar mejor train/test con optimizacion real,
- algunos drawdowns siguen siendo demasiado altos.

Walk-forward inicial:

- `donchian_20_10` en SOL/USDT `4h`: 5 de 5 folds positivos, pero drawdown alto.
- `volatility_expansion_20` en XRP/USDT `4h`: 4 de 5 folds positivos, muestra pequena.
- `volatility_expansion_20` en ETH/USDT `4h`: 3 de 5 folds positivos.

Esto ya nos da una pista:

> La investigacion debe moverse hacia edges de breakout/volatility expansion en `4h`, no scalping mean reversion en `5m`.

## 14.8 Como montar esto en el homeserver

Se agregaron plantillas `systemd`.

Servicio de dashboard:

```text
research_lab/systemd/adrian-quant-lab.service
```

Pipeline nocturno:

```text
research_lab/systemd/adrian-quant-pipeline.service
research_lab/systemd/adrian-quant-pipeline.timer
```

Instalacion sugerida:

```bash
sudo cp research_lab/systemd/adrian-quant-*.service /etc/systemd/system/
sudo cp research_lab/systemd/adrian-quant-pipeline.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adrian-quant-lab.service
sudo systemctl enable --now adrian-quant-pipeline.timer
```

Antes de activar, verificar la ruta:

```text
/home/adrian/freqtrade
```

Si el proyecto esta en otra ruta, cambiar:

- `WorkingDirectory`
- `PATH`
- `ExecStart`

Estado actual:

> Ya esta montado en el homeserver.

Servicios activos:

```text
adrian-quant-lab.service
adrian-quant-pipeline.timer
```

Dashboard:

```text
http://homeserver:8501
```

El timer nocturno queda programado a las:

```text
03:30
```

El pipeline remoto ya fue probado una vez y genero:

- 40 configuraciones de resumen,
- 200 folds de walk-forward,
- features `1h` y `4h`,
- resultados visibles desde la GUI.

## 14.9 Siguiente trabajo tecnico

El siguiente trabajo real es mejorar el laboratorio, no operar.

Prioridades:

1. Agregar timeframe `4h`.
2. Agregar `15m` despues.
3. Agregar walk-forward automatico al edge engine.
4. Agregar Monte Carlo por edge.
5. Agregar filtros de regimen BTC.
6. Agregar reporte diario.
7. Agregar alerta Telegram.
8. Crear nuevas familias de edges.

El objetivo final:

> Que cada noche el homeserver actualice datos, ejecute edges, regenere metricas y te muestre que ideas merecen investigacion.
