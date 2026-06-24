#!/usr/bin/env python3
import argparse
import calendar
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


def equity_curve(returns: np.ndarray, start: float = 1.0) -> np.ndarray:
    return start * np.cumprod(1.0 + returns)


def max_drawdown(equity: np.ndarray):
    if len(equity) == 0:
        return 0.0, 0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    end = int(dd.argmin())
    start = int(np.argmax(equity[: end + 1])) if end > 0 else 0
    return float(dd.min()), end - start


def ulcer_index(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd_pct = 100.0 * (equity - peak) / peak
    return float(np.sqrt(np.mean(dd_pct**2)))


def metrics(returns: np.ndarray, periods_per_year: float = 0.0) -> dict:
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n == 0:
        return {}
    wins = r[r > 0]
    losses = r[r < 0]
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    expectancy = float(r.mean())
    std = float(r.std(ddof=1)) if n > 1 else 0.0
    downside = float(losses.std(ddof=1)) if len(losses) > 1 else 0.0
    ann = np.sqrt(periods_per_year) if periods_per_year and periods_per_year > 0 else 1.0
    eq = equity_curve(r)
    mdd, mdd_dur = max_drawdown(eq)
    total_ret = float(eq[-1] - 1.0)
    cagr = np.nan
    if periods_per_year and periods_per_year > 0:
        years = max(n / periods_per_year, 1e-9)
        cagr = float(eq[-1] ** (1.0 / years) - 1.0)
    calmar_base = cagr if not np.isnan(cagr) else total_ret
    return {
        "trades": n,
        "win_rate": float(len(wins) / n),
        "expectancy": expectancy,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "payoff": float(
            (wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else np.inf
        ),
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else np.inf,
        "total_return": total_ret,
        "max_drawdown": mdd,
        "mdd_duration_trades": mdd_dur,
        "sharpe": float(expectancy / std * ann) if std > 0 else 0.0,
        "sortino": float(expectancy / downside * ann) if downside > 0 else 0.0,
        "calmar": float(calmar_base / abs(mdd)) if mdd != 0 else np.inf,
        "mar": float(calmar_base / abs(mdd)) if mdd != 0 else np.inf,
        "ulcer_index": ulcer_index(eq),
        "sqn": float(expectancy / std * np.sqrt(n)) if std > 0 else 0.0,
        "cagr": cagr,
        "annualized": bool(periods_per_year and periods_per_year > 0),
    }


def print_metrics(m: dict):
    if not m:
        print("No trades.")
        return
    print("-" * 72)
    print(f"Trades                 : {m['trades']}")
    print(f"Win rate               : {m['win_rate']:.2%}")
    print(f"Expectancy/trade       : {m['expectancy']:+.4%}")
    print(f"Avg win / Avg loss     : {m['avg_win']:+.3%} / {m['avg_loss']:+.3%}")
    print(f"Payoff ratio           : {m['payoff']:.2f}")
    print(f"Profit factor          : {m['profit_factor']:.2f}")
    print(f"Total return           : {m['total_return']:+.2%}")
    if not np.isnan(m.get("cagr", np.nan)):
        print(f"CAGR                   : {m['cagr']:+.2%}")
    print(f"Max drawdown           : {m['max_drawdown']:.2%} ({m['mdd_duration_trades']} trades)")
    label = "annual" if m.get("annualized") else "per-trade"
    print(f"Sharpe / Sortino {label:9s}: {m['sharpe']:.2f} / {m['sortino']:.2f}")
    print(f"Calmar / MAR           : {m['calmar']:.2f} / {m['mar']:.2f}")
    print(f"Ulcer index            : {m['ulcer_index']:.2f}")
    print(f"SQN                    : {m['sqn']:.2f}")
    print("-" * 72)


def monte_carlo(
    returns: np.ndarray, sims: int, method: str, ruin_dd: float, seed: int = 42
) -> dict:
    rng = np.random.default_rng(seed)
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) == 0:
        return {}
    finals = np.empty(sims)
    mdds = np.empty(sims)
    for i in range(sims):
        sample = (
            rng.choice(r, size=len(r), replace=True)
            if method == "bootstrap"
            else rng.permutation(r)
        )
        eq = equity_curve(sample)
        finals[i] = eq[-1] - 1.0
        mdds[i] = max_drawdown(eq)[0]
    pct = lambda a, q: float(np.percentile(a, q))
    return {
        "sims": sims,
        "method": method,
        "final_p05": pct(finals, 5),
        "final_p50": pct(finals, 50),
        "final_p95": pct(finals, 95),
        "mdd_p50": pct(mdds, 50),
        "mdd_p95": pct(mdds, 5),
        "mdd_worst": float(mdds.min()),
        "prob_loss": float((finals < 0).mean()),
        "ruin_dd": ruin_dd,
        "prob_ruin": float((mdds <= ruin_dd).mean()),
    }


def print_mc(mc: dict):
    if not mc:
        print("Monte Carlo: no trades.")
        return
    print(f"Monte Carlo ({mc['method']}, {mc['sims']} sims)")
    print("-" * 72)
    print(
        f"Final return p05/p50/p95 : {mc['final_p05']:+.2%} / {mc['final_p50']:+.2%} / {mc['final_p95']:+.2%}"
    )
    print(f"Max DD p50/p95           : {mc['mdd_p50']:.2%} / {mc['mdd_p95']:.2%}")
    print(f"Worst DD                 : {mc['mdd_worst']:.2%}")
    print(f"Prob ending negative     : {mc['prob_loss']:.2%}")
    print(f"Prob ruin DD<={mc['ruin_dd']:.0%}      : {mc['prob_ruin']:.2%}")
    print("-" * 72)


def stress_test(
    returns: np.ndarray, cost_steps: list[float], noise_steps: list[float], sims: int, seed: int = 7
):
    rng = np.random.default_rng(seed)
    r = np.asarray(returns, dtype=float)
    rows = []
    for cost in cost_steps:
        for noise in noise_steps:
            finals = []
            mdds = []
            for _ in range(sims):
                perturbed = r - cost + rng.normal(0.0, noise, size=len(r))
                eq = equity_curve(perturbed)
                finals.append(eq[-1] - 1.0)
                mdds.append(max_drawdown(eq)[0])
            rows.append(
                {
                    "extra_cost": cost,
                    "noise_sigma": noise,
                    "expectancy": float((r - cost).mean()),
                    "final_p50": float(np.percentile(finals, 50)),
                    "final_p05": float(np.percentile(finals, 5)),
                    "mdd_p95": float(np.percentile(mdds, 5)),
                }
            )
    return pd.DataFrame(rows)


def edge_decay(returns: np.ndarray, window: int, baseline_window: int):
    r = pd.Series(np.asarray(returns, dtype=float)).dropna().reset_index(drop=True)
    if len(r) < max(window, baseline_window) + 5:
        return pd.DataFrame()
    baseline = r.iloc[:baseline_window]
    mu = baseline.mean()
    sd = baseline.std(ddof=1)
    se = sd / np.sqrt(window) if sd > 0 else np.nan
    roll = r.rolling(window).mean()
    z = (roll - mu) / se if se and not np.isnan(se) else pd.Series(np.nan, index=r.index)
    out = pd.DataFrame({"trade": np.arange(len(r)), "rolling_expectancy": roll, "z_vs_baseline": z})
    return out.dropna()


def add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def walk_forward_windows(
    start: str, end: str, train_months: int, test_months: int, step_months: int
):
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    windows = []
    cur = s
    while True:
        tr_start = cur
        tr_end = add_months(tr_start, train_months)
        te_end = add_months(tr_end, test_months)
        if te_end > e:
            break
        windows.append(
            {
                "train": f"{tr_start:%Y%m%d}-{tr_end:%Y%m%d}",
                "test": f"{tr_end:%Y%m%d}-{te_end:%Y%m%d}",
            }
        )
        cur = add_months(cur, step_months)
    return windows


def print_wfo(windows: list[dict]):
    print("Walk-forward commands")
    print("-" * 72)
    for i, w in enumerate(windows, 1):
        print(f"Fold {i:02d}")
        print(
            "  freqtrade hyperopt --strategy QuantScalper_SXD "
            "--config config.kucoin.example.json --spaces buy sell "
            f"--timerange {w['train']} --enable-protections"
        )
        print(
            "  freqtrade backtesting --strategy QuantScalper_SXD "
            "--config config.kucoin.example.json "
            f"--timerange {w['test']} --enable-protections "
            f"--export trades --export-filename user_data/backtest_results/wfo_fold_{i:02d}.json"
        )
    print("-" * 72)


def extract_profit_ratios(data) -> list[float]:
    if isinstance(data, list):
        vals = []
        for item in data:
            vals.extend(extract_profit_ratios(item))
        return vals
    if isinstance(data, dict):
        if "profit_ratio" in data:
            return [float(data["profit_ratio"])]
        vals = []
        for value in data.values():
            vals.extend(extract_profit_ratios(value))
        return vals
    return []


def load_trades(path: Path) -> np.ndarray:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            candidates = [
                name
                for name in zf.namelist()
                if name.endswith(".json")
                and not name.endswith("_config.json")
                and not name.endswith(".meta.json")
            ]
            if not candidates:
                raise ValueError(f"No backtest JSON found inside {path}")
            data = json.loads(zf.read(candidates[0]))
        vals = extract_profit_ratios(data)
        if not vals:
            raise ValueError(f"No profit_ratio values found in {path}")
        return np.asarray(vals, dtype=float)
    if path.suffix == ".json":
        with path.open() as f:
            data = json.load(f)
        vals = extract_profit_ratios(data)
        if not vals:
            raise ValueError(f"No profit_ratio values found in {path}")
        return np.asarray(vals, dtype=float)
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    col = next(
        (c for c in ("profit_ratio", "profit_pct", "profit_percent") if c in df.columns), None
    )
    if col is None:
        raise ValueError("CSV needs profit_ratio or profit_pct/profit_percent")
    vals = df[col].to_numpy(dtype=float)
    return vals / 100.0 if col in ("profit_pct", "profit_percent") else vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=Path, nargs="*")
    ap.add_argument("--mc", type=int, default=10000)
    ap.add_argument("--mc-method", choices=["bootstrap", "permute"], default="bootstrap")
    ap.add_argument("--ruin-dd", type=float, default=-0.50)
    ap.add_argument("--periods-per-year", type=float, default=0.0)
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--stress-sims", type=int, default=2000)
    ap.add_argument("--edge-decay", action="store_true")
    ap.add_argument("--edge-window", type=int, default=50)
    ap.add_argument("--edge-baseline", type=int, default=200)
    ap.add_argument("--wfo", default=None, help="start:end, e.g. 2022-01-01:2026-06-01")
    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--test-months", type=int, default=3)
    ap.add_argument("--step-months", type=int, default=3)
    args = ap.parse_args()

    if args.wfo:
        start, end = args.wfo.split(":")
        print_wfo(
            walk_forward_windows(start, end, args.train_months, args.test_months, args.step_months)
        )

    if args.trades:
        returns = np.concatenate([load_trades(path) for path in args.trades])
        print("=" * 72)
        print("Validation")
        print("=" * 72)
        print_metrics(metrics(returns, args.periods_per_year))
        print_mc(monte_carlo(returns, args.mc, args.mc_method, args.ruin_dd))

        if args.stress:
            sdf = stress_test(
                returns,
                cost_steps=[0.0, 0.0005, 0.0010, 0.0020],
                noise_steps=[0.0, 0.0005, 0.0010, 0.0020],
                sims=args.stress_sims,
            )
            print("Stress test: extra cost / return noise")
            print(sdf.to_string(index=False, float_format=lambda x: f"{x:+.4%}"))

        if args.edge_decay:
            edf = edge_decay(returns, args.edge_window, args.edge_baseline)
            if edf.empty:
                print("Edge decay: insufficient trades.")
            else:
                tail = edf.tail(10)
                print("Edge decay tail")
                print(tail.to_string(index=False, float_format=lambda x: f"{x:+.4%}"))
                alerts = edf[edf["z_vs_baseline"] < -2.0]
                print(f"Edge decay alerts z<-2: {len(alerts)}")

    if not args.wfo and not args.trades:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
