from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TemporalFold:
    fold: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def build_temporal_folds(
    dates: pd.Series | list[pd.Timestamp],
    train_months: int = 18,
    test_months: int = 6,
    step_months: int = 6,
) -> list[TemporalFold]:
    values = pd.Series(pd.to_datetime(dates, utc=True)).dropna().sort_values()
    if values.empty:
        return []

    start = pd.Timestamp(values.min()).tz_convert("UTC")
    end = pd.Timestamp(values.max()).tz_convert("UTC")

    folds: list[TemporalFold] = []
    fold_number = 1
    train_start = start

    while True:
        train_end = train_start + pd.DateOffset(months=train_months)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=test_months)
        if test_end > end:
            break

        folds.append(
            TemporalFold(
                fold=fold_number,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        fold_number += 1
        train_start = train_start + pd.DateOffset(months=step_months)

    return folds
