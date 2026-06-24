from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_DECISION_RULES = {
    "version": "lab-rules-v1",
    "candidate": {
        "min_trades": 80,
        "min_profit_factor": 1.15,
        "min_expectancy": 0.0,
        "min_max_drawdown": -0.35,
        "min_score": 60,
    },
    "watch": {
        "min_trades": 50,
        "min_profit_factor": 1.0,
        "min_expectancy": 0.0,
        "min_max_drawdown": -0.45,
    },
    "monte_carlo": {
        "sims": 300,
        "seed": 42,
        "ruin_drawdown": -0.50,
    },
    "stress": {
        "extra_costs": [0.0002, 0.0005, 0.001],
        "maker_offset": 0.001,
        "maker_timeout_bars": 1,
    },
}


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default.copy()
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    return {**default, **loaded}


def load_decision_rules(
    path: str | Path = "research_lab/config/decision_rules.json",
) -> dict[str, Any]:
    return load_json(Path(path), DEFAULT_DECISION_RULES)
