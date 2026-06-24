from __future__ import annotations

import argparse
import json
import resource
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_lab.run_manager import atomic_write_json
from research_lab.warehouse import refresh_duckdb, write_parquet_atomic


DEEP_VALIDATION_VERSION = "deep-validation-v1"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def peak_memory_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return float(usage / 1024)


def drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    return float(dd.min())


def profit_factor(values: np.ndarray) -> float:
    wins = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses <= 0:
        return 999.0 if wins > 0 else 0.0
    return float(wins / losses)


def sample_blocks(
    values: np.ndarray, block_size: int, target_len: int, rng: np.random.Generator
) -> np.ndarray:
    if len(values) <= block_size:
        return rng.choice(values, size=target_len, replace=True)
    chunks: list[np.ndarray] = []
    sampled = 0
    while sampled < target_len:
        start = int(rng.integers(0, len(values) - block_size + 1))
        chunk = values[start : start + block_size]
        chunks.append(chunk)
        sampled += len(chunk)
    return np.concatenate(chunks)[:target_len]


def monte_carlo(values: np.ndarray, sims: int, seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    finals = np.empty(sims, dtype=float)
    max_dds = np.empty(sims, dtype=float)
    for idx in range(sims):
        sample = rng.choice(values, size=len(values), replace=True)
        equity = np.cumprod(1.0 + sample)
        finals[idx] = equity[-1] - 1.0
        max_dds[idx] = drawdown(equity)
    return {
        "mc_final_p05": float(np.percentile(finals, 5)),
        "mc_final_p50": float(np.percentile(finals, 50)),
        "mc_final_p95": float(np.percentile(finals, 95)),
        "mc_prob_negative": float(np.mean(finals < 0)),
        "mc_max_dd_p95": float(np.percentile(max_dds, 5)),
    }


def block_bootstrap(values: np.ndarray, sims: int, block_size: int, seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    finals = np.empty(sims, dtype=float)
    max_dds = np.empty(sims, dtype=float)
    for idx in range(sims):
        sample = sample_blocks(values, block_size, len(values), rng)
        equity = np.cumprod(1.0 + sample)
        finals[idx] = equity[-1] - 1.0
        max_dds[idx] = drawdown(equity)
    return {
        "block_bootstrap_block_size": float(block_size),
        "block_final_p05": float(np.percentile(finals, 5)),
        "block_final_p50": float(np.percentile(finals, 50)),
        "block_final_p95": float(np.percentile(finals, 95)),
        "block_prob_negative": float(np.mean(finals < 0)),
        "block_max_dd_p95": float(np.percentile(max_dds, 5)),
    }


def stress_metrics(return_on_stake: np.ndarray, cost_bps: list[float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for bps in cost_bps:
        adjusted = return_on_stake - (bps / 10000.0)
        token = int(bps)
        out[f"stress_pf_{token}bps"] = profit_factor(adjusted)
        out[f"stress_expectancy_{token}bps"] = float(np.mean(adjusted))
    return out


def validate_group(
    group: pd.DataFrame,
    sims: int,
    block_size: int,
    seed: int,
    initial_cash: float,
) -> dict[str, Any]:
    pnl_returns = group["pnl"].astype(float).to_numpy() / initial_cash
    stake_returns = group["return_on_stake"].astype(float).to_numpy()
    equity = np.cumprod(1.0 + pnl_returns)
    row = {
        "trades": len(group),
        "total_return": float(equity[-1] - 1.0) if len(equity) else 0.0,
        "profit_factor": profit_factor(group["pnl"].astype(float).to_numpy()),
        "max_drawdown": drawdown(equity),
        "expectancy_on_cash": float(np.mean(pnl_returns)) if len(pnl_returns) else 0.0,
        "expectancy_on_stake": float(np.mean(stake_returns)) if len(stake_returns) else 0.0,
        **monte_carlo(pnl_returns, sims, seed),
        **block_bootstrap(pnl_returns, sims, block_size, seed + 17),
        **stress_metrics(stake_returns, [2.0, 5.0, 10.0]),
    }
    return row


def add_bh_fdr(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    out = summary.copy()
    p = out["mc_prob_negative"].fillna(1.0).clip(0.0, 1.0).to_numpy(dtype=float)
    order = np.argsort(p)
    q = np.empty(len(p), dtype=float)
    prev = 1.0
    n = len(p)
    for rank, idx in reversed(list(enumerate(order, start=1))):
        value = min(prev, p[idx] * n / rank)
        q[idx] = value
        prev = value
    out["fdr_q_mc_prob_negative"] = q
    return out


def copy_to_run_dirs(storage: Path, run_id: str, files: list[str]) -> None:
    results = storage / "results"
    for target_dir in [results / "runs" / run_id, results / "latest"]:
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            source = results / name
            if source.exists():
                shutil.copy2(source, target_dir / name)


def run_deep_validation(
    storage: Path,
    run_id: str,
    sims: int,
    block_size: int,
    seed: int,
    initial_cash: float,
    research_seconds: float | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    started = time.perf_counter()
    results = storage / "results"
    trades_path = results / "account_trades.parquet"
    if not trades_path.exists():
        summary = pd.DataFrame()
        missing_manifest: dict[str, Any] = {
            "version": DEEP_VALIDATION_VERSION,
            "status": "failed",
            "run_id": run_id,
            "generated_at": utc_now(),
            "error": "missing_account_trades",
            "research_seconds": research_seconds,
            "deep_validation_seconds": round(time.perf_counter() - started, 3),
            "peak_memory_mb": peak_memory_mb(),
        }
        atomic_write_json(results / "deep_validation_manifest.json", missing_manifest)
        return summary, missing_manifest

    trades = pd.read_parquet(trades_path)
    if run_id and "run_id" in trades.columns:
        trades = trades[trades["run_id"].astype(str) == str(run_id)].copy()
    rows = []
    required = {"edge", "timeframe", "pnl", "return_on_stake"}
    missing = sorted(required - set(trades.columns))
    if not trades.empty and not missing:
        for (edge, timeframe), group in trades.groupby(["edge", "timeframe"], dropna=False):
            metrics = validate_group(group, sims, block_size, seed, initial_cash)
            metrics.update(
                {
                    "run_id": run_id,
                    "edge": edge,
                    "timeframe": timeframe,
                    "sims": sims,
                    "seed": seed,
                    "deep_validation_version": DEEP_VALIDATION_VERSION,
                }
            )
            rows.append(metrics)

    summary = add_bh_fdr(pd.DataFrame(rows))
    write_parquet_atomic(summary, results / "deep_validation_summary.parquet")
    manifest: dict[str, Any] = {
        "version": DEEP_VALIDATION_VERSION,
        "status": "passed" if not summary.empty and not missing else "failed",
        "run_id": run_id,
        "generated_at": utc_now(),
        "sims": sims,
        "block_size": block_size,
        "seed": seed,
        "summary_rows": len(summary),
        "missing_columns": missing,
        "research_seconds": research_seconds,
        "deep_validation_seconds": round(time.perf_counter() - started, 3),
        "total_seconds": (
            round((research_seconds or 0.0) + (time.perf_counter() - started), 3)
            if research_seconds is not None
            else None
        ),
        "peak_memory_mb": peak_memory_mb(),
        "implemented_tests": [
            "trade_monte_carlo_bootstrap",
            "trade_block_bootstrap",
            "cost_sensitivity",
            "benjamini_hochberg_fdr_proxy",
        ],
        "not_yet_implemented": [
            "deflated_sharpe_ratio",
            "pbo_cscv",
            "parameter_sensitivity_grid",
            "version_comparison",
        ],
    }
    atomic_write_json(results / "deep_validation_manifest.json", manifest)
    copy_to_run_dirs(
        storage,
        run_id,
        ["deep_validation_summary.parquet", "deep_validation_manifest.json"],
    )
    if (storage / "ohlcv_manifest.parquet").exists():
        refresh_duckdb(storage)
    return summary, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Monthly deep validation from account trades")
    parser.add_argument("--storage", default="research_lab/storage", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--block-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=17062026)
    parser.add_argument("--initial-cash", type=float, default=1000.0)
    parser.add_argument("--research-seconds", type=float, default=None)
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    summary, manifest = run_deep_validation(
        args.storage,
        args.run_id,
        args.sims,
        args.block_size,
        args.seed,
        args.initial_cash,
        args.research_seconds,
    )
    if summary.empty:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(
            summary[
                [
                    "edge",
                    "timeframe",
                    "trades",
                    "total_return",
                    "profit_factor",
                    "mc_prob_negative",
                    "block_prob_negative",
                    "fdr_q_mc_prob_negative",
                ]
            ].to_string(index=False)
        )
    if args.fail_on_error and manifest["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
