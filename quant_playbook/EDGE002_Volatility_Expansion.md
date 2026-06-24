# EDGE002 - Volatility Expansion

## Estado

`candidate inicial`

## Hipótesis

Los rompimientos acompañados por expansión de volatilidad y volumen tienen mejor comportamiento que los breakouts simples en crypto.

## Mercado

- Exchange: KuCoin
- Pares iniciales: BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT
- Timeframes: 1h, 4h
- Periodo: 2022-01-01 a 2026-06-17
- Tipo: breakout / volatility expansion

## Reglas exactas

Entrada:

- `close > donchian_high_20.shift(1)`
- `close > session_vwap`
- `composite_vol / median(composite_vol, 100) > 1.15`
- `volume_pct_100 > 0.60`
- `bear_prob < 0.40`

Salida:

- `close < ema_20`
- o `close < donchian_high_55.shift(1)`
- stop/take-profit dinámico por `composite_vol`
- `max_hold = 96` barras

## Resultado agregado

Primeros candidatos iniciales:

| Par | TF | Trades | PF | Expectancy | Max DD | Lectura |
|---|---:|---:|---:|---:|---:|---|
| ETH/USDT | 4h | 104 | 1.20 | +0.136% | -15.6% | Mejor perfil inicial |
| DOGE/USDT | 4h | 107 | 1.20 | +0.247% | -31.1% | Prometedor, más riesgo |

## Walk-forward / Temporal OOS

Lectura inicial:

- XRP/USDT 4h tuvo 4/5 folds positivos, pero pocos trades.
- ETH/USDT 4h tuvo 3/5 folds positivos.
- DOGE/USDT 4h tuvo 3/5 folds positivos.

## Conclusión

Es el edge más interesante del primer lote, especialmente en `4h`. Todavía requiere Monte Carlo, stress y observación durante varios días antes de paper.

## Próxima pregunta

H002: ¿Volatility Expansion mejora si se filtra por BTC/ETH alcista o por `vol_rank_200`?
