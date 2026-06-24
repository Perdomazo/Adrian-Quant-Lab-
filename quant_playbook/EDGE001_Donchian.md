# EDGE001 - Donchian Breakout

## Estado

`watch`

## Hipótesis

Los rompimientos de máximos recientes capturan tendencias de varios días en crypto, especialmente en `4h`, pero pueden sufrir drawdowns altos.

## Mercado

- Exchange: KuCoin
- Pares iniciales: BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT
- Timeframes: 1h, 4h
- Periodo: 2022-01-01 a 2026-06-17
- Tipo: breakout / trend following

## Reglas exactas

Entrada:

- `close > donchian_high_20.shift(1)`
- `volume_pct_100 > 0.35`

Salida:

- `close < donchian_low_10.shift(1)`
- stop/take-profit dinámico por `composite_vol`
- `max_hold = 120` barras

Costes:

- fee base 0.10%
- slippage base 0.05%

## Resultado agregado

Primer hallazgo: el edge es débil en `1h`, pero aparece más interesante en `4h`.

Caso destacado inicial:

| Par | TF | Trades | PF | Expectancy | Max DD | Lectura |
|---|---:|---:|---:|---:|---:|---|
| SOL/USDT | 4h | 186 | 1.20 | +0.548% | -48.2% | Interesante, pero DD demasiado alto |

## Walk-forward / Temporal OOS

Lectura inicial:

- SOL/USDT 4h tuvo 5/5 folds positivos.
- Aun así el drawdown agregado es demasiado alto para paper automático.

## Conclusión

Vale la pena seguir investigando Donchian en `4h`, pero no aprobarlo todavía.

## Próxima pregunta

H001: ¿Donchian mejora si solo opera con volatilidad alta y BTC en régimen no bajista?
