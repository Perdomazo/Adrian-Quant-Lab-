from __future__ import annotations

import pandas as pd

from research_lab.validation_windows import build_temporal_folds


def test_empty_dates_produce_no_folds():
    assert build_temporal_folds(pd.Series([], dtype="datetime64[ns]")) == []


def test_insufficient_history_produces_no_folds():
    dates = pd.date_range("2025-01-01", periods=100, freq="D", tz="UTC")
    assert build_temporal_folds(dates, train_months=18, test_months=6, step_months=6) == []


def test_fold_lengths_and_step_are_month_based():
    dates = pd.date_range("2023-01-01", "2026-01-10", freq="D", tz="UTC")
    folds = build_temporal_folds(dates, train_months=18, test_months=6, step_months=6)

    assert folds[0].train_end == folds[0].train_start + pd.DateOffset(months=18)
    assert folds[0].test_end == folds[0].test_start + pd.DateOffset(months=6)
    assert folds[1].train_start == folds[0].train_start + pd.DateOffset(months=6)


def test_no_test_window_after_available_end():
    dates = pd.date_range("2023-01-01", "2025-01-01", freq="D", tz="UTC")
    folds = build_temporal_folds(dates, train_months=18, test_months=6, step_months=6)

    assert len(folds) == 1
    assert folds[0].test_end <= dates.max()


def test_fold_dates_are_utc():
    dates = pd.date_range("2023-01-01", "2026-01-10", freq="D")
    folds = build_temporal_folds(dates)

    assert folds
    assert str(folds[0].train_start.tz) == "UTC"
    assert str(folds[0].test_end.tz) == "UTC"


def test_folds_are_deterministic_for_unsorted_input():
    dates = pd.Series(pd.date_range("2023-01-01", "2026-01-10", freq="D", tz="UTC"))
    first = build_temporal_folds(dates.sample(frac=1.0, random_state=7))
    second = build_temporal_folds(dates.sample(frac=1.0, random_state=99))

    assert first == second
