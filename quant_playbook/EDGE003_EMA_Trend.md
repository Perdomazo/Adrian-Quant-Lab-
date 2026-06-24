# EDGE003 - EMA Trend Following

## Estado

`watch`

## Hipótesis

Una estructura tendencial simple `close > EMA20 > EMA50 > EMA100` puede capturar tendencias lentas en crypto.

## Reglas exactas

Entrada:

- `close > ema_20`
- `ema_20 > ema_50`
- `ema_50 > ema_100`
- `ema_50_slope > 0`
- `bear_prob < 0.35`

Salida:

- `close < ema_50`
- o `bear_prob > 0.55`

## Resultado inicial

El edge mejora en `4h`, pero el agregado inicial no supera todavía los filtros fuertes de drawdown y robustez.

## Conclusión

Mantener en watchlist, no promover.
