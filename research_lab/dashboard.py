from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st


STORAGE = Path("research_lab/storage")
SUMMARY = STORAGE / "results" / "edge_summary.parquet"
TRADES = STORAGE / "results" / "edge_trades.parquet"
WALK_FORWARD = STORAGE / "results" / "walk_forward_summary.parquet"
SEASON = STORAGE / "results" / "season_summary.parquet"
RUN_HISTORY = STORAGE / "results" / "run_history.parquet"
ACCOUNT_OOS_AGGREGATE = STORAGE / "results" / "account_oos_aggregate.parquet"
ACCOUNT_OOS_SUMMARY = STORAGE / "results" / "account_oos_summary.parquet"
ACCOUNT_OOS_EQUITY = STORAGE / "results" / "account_oos_equity.parquet"
ACCOUNT_CANDIDATE_SUMMARY = STORAGE / "results" / "account_candidate_summary.parquet"
PROMOTION_DECISION = STORAGE / "results" / "promotion_decision.json"
FEATURES_MANIFEST = STORAGE / "features_manifest.parquet"
OHLCV_MANIFEST = STORAGE / "ohlcv_manifest.parquet"


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:.2%}"


def num(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:.2f}"


@st.cache_data(ttl=30)
def load_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


@st.cache_data(ttl=30)
def load_json_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else "{}"


def apply_filters(summary: pd.DataFrame) -> pd.DataFrame:
    with st.sidebar:
        st.header("Filtros")
        edges = sorted(summary["edge"].dropna().unique())
        pairs = sorted(summary["pair"].dropna().unique())
        timeframes = sorted(summary["timeframe"].dropna().unique())
        selected_edges = st.multiselect("Edges", edges, default=edges)
        selected_pairs = st.multiselect("Pares", pairs, default=pairs)
        selected_timeframes = st.multiselect("Timeframes", timeframes, default=timeframes)
        min_trades = st.slider("Trades minimos", 0, 1000, 30, 10)
        st.divider()
        st.caption("Criterio de candidato")
        st.caption("PF >= 1.15, expectancy > 0, drawdown > -35%, trades >= 80")

    return summary[
        summary["edge"].isin(selected_edges)
        & summary["pair"].isin(selected_pairs)
        & summary["timeframe"].isin(selected_timeframes)
        & (summary["trades"] >= min_trades)
    ].copy()


def add_display_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["win_rate", "expectancy", "total_return", "max_drawdown", "avg_win", "avg_loss"]:
        if col in out:
            out[f"{col}_display"] = out[col].map(pct)
    if "profit_factor" in out:
        out["profit_factor_display"] = out["profit_factor"].map(num)
    return out


def metric_card(col, label: str, value: str, help_text: str | None = None) -> None:
    col.metric(label, value, help=help_text)


def equity_frame(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.sort_values("exit_date").copy()
    out["equity"] = (1.0 + out["net_return"]).cumprod()
    out["drawdown"] = out["equity"] / out["equity"].cummax() - 1.0
    return out


st.set_page_config(page_title="Adrian Quant Lab", layout="wide")
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.4rem; }
    div[data-testid="stMetric"] {
        border: 1px solid rgba(120, 120, 120, 0.22);
        border-radius: 8px;
        padding: 0.75rem 0.85rem;
        background: rgba(250, 250, 250, 0.03);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Adrian Quant Lab")
st.caption("Warehouse, edges, walk-forward y dashboard personal para investigar antes de operar.")

summary = load_parquet(SUMMARY)
trades = load_parquet(TRADES)
wfo = load_parquet(WALK_FORWARD)
season = load_parquet(SEASON)
run_history = load_parquet(RUN_HISTORY)
account_oos_aggregate = load_parquet(ACCOUNT_OOS_AGGREGATE)
account_oos_summary = load_parquet(ACCOUNT_OOS_SUMMARY)
account_oos_equity = load_parquet(ACCOUNT_OOS_EQUITY)
account_candidate_summary = load_parquet(ACCOUNT_CANDIDATE_SUMMARY)
promotion_decision = load_json_text(PROMOTION_DECISION)
features_manifest = load_parquet(FEATURES_MANIFEST)
ohlcv_manifest = load_parquet(OHLCV_MANIFEST)

if summary.empty:
    st.warning("No hay resultados todavia. Ejecuta primero el pipeline.")
    st.code(
        "source .venv/bin/activate\n"
        "python -m research_lab.warehouse migrate --source user_data/data/kucoin\n"
        "python -m research_lab.warehouse resample --source-timeframe 1h --target-timeframe 4h\n"
        "python -m research_lab.warehouse features --timeframes 1h,4h\n"
        "python -m research_lab.edge_engine --timeframes 1h,4h --walk-forward\n",
        language="bash",
    )
    st.stop()

filtered = apply_filters(summary)

if filtered.empty:
    st.info("No hay resultados con esos filtros.")
    st.stop()

filtered = filtered.sort_values(["profit_factor", "expectancy", "trades"], ascending=False)
best = filtered.iloc[0]
candidate_count = (
    int((filtered.get("verdict", "") == "candidate").sum()) if "verdict" in filtered else 0
)
watch_count = int((filtered.get("verdict", "") == "watch").sum()) if "verdict" in filtered else 0

top1, top2, top3, top4, top5, top6 = st.columns(6)
metric_card(top1, "Configuraciones", f"{len(filtered)}")
metric_card(top2, "Candidatos", f"{candidate_count}")
metric_card(top3, "Watchlist", f"{watch_count}")
metric_card(top4, "Mejor PF", num(best["profit_factor"]))
metric_card(top5, "Mejor score", num(best.get("score")))
metric_card(top6, "Total trades", f"{int(filtered['trades'].sum()):,}")

tabs = st.tabs(
    [
        "Overview",
        "Robustez",
        "Heatmap",
        "Walk-forward",
        "Cuenta OOS",
        "Liga",
        "Trades",
        "Warehouse",
    ]
)

with tabs[0]:
    st.subheader("Ranking de edges")
    display = add_display_columns(filtered)
    rank_cols = [
        "verdict",
        "score",
        "edge",
        "pair",
        "timeframe",
        "trades",
        "entry_signals",
        "expected_maker_fill_rate",
        "profit_factor",
        "win_rate",
        "expectancy",
        "total_return",
        "max_drawdown",
        "mc_prob_negative",
        "stress_pf_5bps",
        "sharpe_trade",
    ]
    rank_cols = [col for col in rank_cols if col in display.columns]
    st.dataframe(
        display[rank_cols],
        width="stretch",
        hide_index=True,
        column_config={
            "profit_factor": st.column_config.NumberColumn("PF", format="%.3f"),
            "score": st.column_config.NumberColumn("Score", format="%.1f"),
            "win_rate": st.column_config.NumberColumn("Win rate", format="%.2f"),
            "expected_maker_fill_rate": st.column_config.NumberColumn(
                "Maker fill est.", format="%.2f"
            ),
            "expectancy": st.column_config.NumberColumn("Expectancy", format="%.4f"),
            "total_return": st.column_config.NumberColumn("Total return", format="%.3f"),
            "max_drawdown": st.column_config.NumberColumn("Max DD", format="%.3f"),
            "mc_prob_negative": st.column_config.NumberColumn("MC prob neg.", format="%.2f"),
            "stress_pf_5bps": st.column_config.NumberColumn("PF +5bps", format="%.3f"),
            "sharpe_trade": st.column_config.NumberColumn("Sharpe/trade", format="%.2f"),
        },
    )

    st.subheader("Lectura rapida")
    st.write(
        "Un resultado no es operable solo por estar arriba del ranking. Primero debe sobrevivir "
        "a walk-forward, costes conservadores, Monte Carlo y suficiente cantidad de trades."
    )

with tabs[1]:
    st.subheader("Robustez")
    robust_cols = [
        "verdict",
        "score",
        "edge",
        "pair",
        "timeframe",
        "trades",
        "activity_state",
        "entry_signals",
        "signal_to_trade_rate",
        "expected_maker_fill_rate",
        "profit_factor",
        "stress_pf_2bps",
        "stress_pf_5bps",
        "stress_pf_10bps",
        "mc_prob_negative",
        "mc_prob_ruin",
        "mc_max_dd_p95",
        "drawdown_duration_bars",
        "tested_combinations",
        "rules_version",
    ]
    robust_cols = [col for col in robust_cols if col in filtered.columns]
    st.dataframe(
        filtered[robust_cols],
        width="stretch",
        hide_index=True,
        column_config={
            "score": st.column_config.NumberColumn("Score", format="%.1f"),
            "signal_to_trade_rate": st.column_config.NumberColumn("Signal->trade", format="%.2f"),
            "expected_maker_fill_rate": st.column_config.NumberColumn(
                "Maker fill est.", format="%.2f"
            ),
            "profit_factor": st.column_config.NumberColumn("PF", format="%.3f"),
            "stress_pf_2bps": st.column_config.NumberColumn("PF +2bps", format="%.3f"),
            "stress_pf_5bps": st.column_config.NumberColumn("PF +5bps", format="%.3f"),
            "stress_pf_10bps": st.column_config.NumberColumn("PF +10bps", format="%.3f"),
            "mc_prob_negative": st.column_config.NumberColumn("MC prob neg.", format="%.2f"),
            "mc_prob_ruin": st.column_config.NumberColumn("MC prob ruin", format="%.2f"),
            "mc_max_dd_p95": st.column_config.NumberColumn("MC DD p95", format="%.3f"),
        },
    )
    st.caption(
        "Walk-forward actual: temporal_oos_fixed_params. Mide estabilidad temporal con "
        "parámetros fijos; todavía no optimiza en train."
    )

with tabs[2]:
    st.subheader("Mapa comparativo")
    left, right = st.columns([1, 1])
    with left:
        selected_edge = st.selectbox("Edge", sorted(filtered["edge"].unique()))
    with right:
        metric = st.selectbox(
            "Metrica",
            ["profit_factor", "expectancy", "total_return", "max_drawdown", "trades"],
        )
    heat = filtered[filtered["edge"] == selected_edge].pivot_table(
        index="pair",
        columns="timeframe",
        values=metric,
        aggfunc="median",
    )
    st.dataframe(heat, width="stretch")

    chart_data = filtered[filtered["edge"] == selected_edge].copy()
    chart_data["label"] = chart_data["pair"] + " " + chart_data["timeframe"]
    st.bar_chart(chart_data.set_index("label")[[metric]])

with tabs[3]:
    st.subheader("Walk-forward")
    if wfo.empty:
        st.info("No hay walk-forward todavia. Ejecuta el edge engine con `--walk-forward`.")
    else:
        wf = wfo[
            wfo["edge"].isin(filtered["edge"].unique())
            & wfo["pair"].isin(filtered["pair"].unique())
            & wfo["timeframe"].isin(filtered["timeframe"].unique())
        ].copy()
        if wf.empty:
            st.info("No hay folds con los filtros actuales.")
        else:
            agg = (
                wf.groupby(["edge", "pair", "timeframe"], as_index=False)
                .agg(
                    folds=("fold", "count"),
                    positive_folds=("expectancy", lambda x: int((x > 0).sum())),
                    median_pf=("profit_factor", "median"),
                    median_expectancy=("expectancy", "median"),
                    total_test_trades=("trades", "sum"),
                    worst_dd=("max_drawdown", "min"),
                )
                .sort_values(["median_pf", "median_expectancy"], ascending=False)
            )
            agg["positive_rate"] = agg["positive_folds"] / agg["folds"].replace(0, pd.NA)
            st.dataframe(
                agg,
                width="stretch",
                hide_index=True,
                column_config={
                    "median_pf": st.column_config.NumberColumn("Median PF", format="%.3f"),
                    "median_expectancy": st.column_config.NumberColumn(
                        "Median expectancy", format="%.4f"
                    ),
                    "positive_rate": st.column_config.NumberColumn("Positive folds", format="%.2f"),
                    "worst_dd": st.column_config.NumberColumn("Worst DD", format="%.3f"),
                },
            )

            option = st.selectbox(
                "Detalle por fold",
                [
                    f"{row.edge} | {row.pair} | {row.timeframe}"
                    for row in agg.itertuples(index=False)
                ],
            )
            edge_name, pair_name, timeframe_name = [part.strip() for part in option.split("|")]
            fold_detail = wf[
                (wf["edge"] == edge_name)
                & (wf["pair"] == pair_name)
                & (wf["timeframe"] == timeframe_name)
            ].sort_values("fold")
            st.dataframe(fold_detail, width="stretch", hide_index=True)
            st.line_chart(fold_detail.set_index("test_end")[["expectancy", "profit_factor"]])

with tabs[4]:
    st.subheader("Cuenta OOS")
    st.markdown("### Decision de cuenta")
    if account_candidate_summary.empty:
        st.info("No hay account_candidate_summary.parquet todavia.")
    else:
        candidate_view = account_candidate_summary[
            account_candidate_summary["edge"].isin(filtered["edge"].unique())
            & account_candidate_summary["timeframe"].isin(filtered["timeframe"].unique())
        ].copy()
        if candidate_view.empty:
            st.info("No hay decisiones de cuenta con los filtros actuales.")
        else:
            decision_cols = [
                "eligibility_status",
                "account_verdict",
                "edge",
                "timeframe",
                "candidate_id",
                "full_trades",
                "full_profit_factor",
                "full_max_drawdown",
                "oos_folds",
                "oos_positive_fold_rate",
                "oos_median_profit_factor",
                "oos_worst_drawdown",
                "oos_min_pair_coverage_rate",
                "oos_total_trades",
                "consecutive_candidate_runs",
                "failed_rules",
            ]
            decision_cols = [col for col in decision_cols if col in candidate_view.columns]
            st.dataframe(candidate_view[decision_cols], width="stretch", hide_index=True)

    if account_oos_aggregate.empty:
        st.info("No hay Account OOS todavia. Ejecuta el pipeline research completo.")
    else:
        oos = account_oos_aggregate[
            account_oos_aggregate["edge"].isin(filtered["edge"].unique())
            & account_oos_aggregate["timeframe"].isin(filtered["timeframe"].unique())
        ].copy()
        if oos.empty:
            st.info("No hay resultados OOS con los filtros actuales.")
        else:
            oos = oos.sort_values(["positive_fold_rate", "median_oos_return"], ascending=False)
            cols = [
                "edge",
                "timeframe",
                "folds",
                "positive_fold_rate",
                "median_oos_return",
                "median_oos_profit_factor",
                "worst_oos_drawdown",
                "min_pair_coverage_rate",
                "total_oos_trades",
            ]
            cols = [col for col in cols if col in oos.columns]
            st.dataframe(
                oos[cols],
                width="stretch",
                hide_index=True,
                column_config={
                    "positive_fold_rate": st.column_config.NumberColumn(
                        "Positive folds", format="%.2f"
                    ),
                    "median_oos_return": st.column_config.NumberColumn(
                        "Median return", format="%.3f"
                    ),
                    "median_oos_profit_factor": st.column_config.NumberColumn(
                        "Median PF", format="%.3f"
                    ),
                    "worst_oos_drawdown": st.column_config.NumberColumn("Worst DD", format="%.3f"),
                    "min_pair_coverage_rate": st.column_config.NumberColumn(
                        "Min pair coverage", format="%.2f"
                    ),
                },
            )

            if not account_oos_summary.empty:
                fold_summary = account_oos_summary[
                    account_oos_summary["edge"].isin(oos["edge"].unique())
                    & account_oos_summary["timeframe"].isin(oos["timeframe"].unique())
                ].copy()
                selected = st.selectbox(
                    "Detalle cuenta OOS",
                    [
                        f"{row.edge} | {row.timeframe}"
                        for row in oos[["edge", "timeframe"]]
                        .drop_duplicates()
                        .itertuples(index=False)
                    ],
                )
                selected_edge, selected_timeframe = [part.strip() for part in selected.split("|")]
                detail = fold_summary[
                    (fold_summary["edge"] == selected_edge)
                    & (fold_summary["timeframe"] == selected_timeframe)
                ].sort_values("fold")
                if detail.empty:
                    st.info("No hay detalle de folds para esta seleccion.")
                else:
                    st.dataframe(detail, width="stretch", hide_index=True)
                    st.bar_chart(
                        detail.set_index("fold")[["total_return", "profit_factor", "max_drawdown"]]
                    )

                if not account_oos_equity.empty and not detail.empty:
                    fold = st.selectbox("Fold equity", sorted(detail["fold"].unique()))
                    equity_detail = account_oos_equity[
                        (account_oos_equity["edge"] == selected_edge)
                        & (account_oos_equity["timeframe"] == selected_timeframe)
                        & (account_oos_equity["fold"] == fold)
                    ].copy()
                    if not equity_detail.empty:
                        equity_detail["date"] = pd.to_datetime(equity_detail["date"], utc=True)
                        chart1, chart2 = st.columns(2)
                        with chart1:
                            st.caption("Equity del fold")
                            st.line_chart(equity_detail.set_index("date")[["equity"]])
                        with chart2:
                            st.caption("Drawdown del fold")
                            st.line_chart(equity_detail.set_index("date")[["drawdown"]])

with tabs[5]:
    st.subheader("Liga por temporada")
    if season.empty:
        st.info("No hay season_summary.parquet todavia. Ejecuta el edge engine.")
    else:
        league = season[
            season["edge"].isin(filtered["edge"].unique())
            & season["pair"].isin(filtered["pair"].unique())
            & season["timeframe"].isin(filtered["timeframe"].unique())
        ].copy()
        if league.empty:
            st.info("No hay temporadas con esos filtros.")
        else:
            metric = st.selectbox(
                "Metrica temporada",
                ["profit_factor", "expectancy", "score", "max_drawdown"],
                key="league_metric",
            )
            pivot = league.pivot_table(
                index=["edge", "pair", "timeframe"], columns="year", values=metric, aggfunc="median"
            )
            st.dataframe(pivot, width="stretch")
            st.dataframe(
                league.sort_values(["year", "score"], ascending=[True, False]),
                width="stretch",
                hide_index=True,
            )

with tabs[6]:
    st.subheader("Detalle de trades")
    if trades.empty:
        st.info("No hay archivo de trades.")
    else:
        labels = [
            f"{row.edge} | {row.pair} | {row.timeframe} | "
            f"PF {row.profit_factor:.2f} | {row.trades} trades"
            for row in filtered.itertuples(index=False)
        ]
        selected_label = st.selectbox("Seleccion", labels)
        selected_edge, selected_pair, selected_timeframe = [
            part.strip() for part in selected_label.split("|")[:3]
        ]

        selected_trades = trades[
            (trades["edge"] == selected_edge)
            & (trades["pair"] == selected_pair)
            & (trades["timeframe"] == selected_timeframe)
        ]

        if selected_trades.empty:
            st.info("No hay trades para esta seleccion.")
        else:
            eq = equity_frame(selected_trades)
            c1, c2, c3, c4 = st.columns(4)
            metric_card(c1, "Trades", f"{len(eq):,}")
            metric_card(c2, "Expectancy", pct(eq["net_return"].mean()))
            metric_card(c3, "Win rate", pct((eq["net_return"] > 0).mean()))
            metric_card(c4, "Max DD", pct(eq["drawdown"].min()))

            chart1, chart2 = st.columns(2)
            with chart1:
                st.caption("Equity")
                st.line_chart(eq.set_index("exit_date")[["equity"]])
            with chart2:
                st.caption("Drawdown")
                st.line_chart(eq.set_index("exit_date")[["drawdown"]])

            hist_col, exit_col = st.columns(2)
            with hist_col:
                st.caption("Retornos netos por trade")
                st.bar_chart(eq["net_return"].mul(100), x_label="Trade", y_label="Retorno neto %")
            with exit_col:
                st.caption("Razones de salida")
                exits = (
                    eq["exit_reason"]
                    .value_counts()
                    .rename_axis("exit_reason")
                    .reset_index(name="count")
                )
                st.dataframe(exits, width="stretch", hide_index=True)

            st.dataframe(eq, width="stretch", hide_index=True)

with tabs[7]:
    st.subheader("Warehouse")
    st.caption("Datos disponibles para investigacion.")
    c1, c2, c3 = st.columns(3)
    metric_card(c1, "OHLCV files", f"{len(ohlcv_manifest)}")
    metric_card(c2, "Feature files", f"{len(features_manifest)}")
    metric_card(c3, "Storage", str(STORAGE))

    with st.expander("OHLCV manifest", expanded=False):
        st.dataframe(ohlcv_manifest, width="stretch", hide_index=True)

    with st.expander("Features manifest", expanded=False):
        st.dataframe(features_manifest, width="stretch", hide_index=True)

    with st.expander("Run history", expanded=False):
        st.dataframe(run_history, width="stretch", hide_index=True)

    with st.expander("Promotion decision", expanded=True):
        st.code(promotion_decision, language="json")

    st.subheader("Comandos")
    st.code(
        "source .venv/bin/activate\n"
        "bash research_lab/scripts/run_pipeline.sh\n"
        "streamlit run research_lab/dashboard.py --server.address 0.0.0.0 --server.port 8501 "
        "--server.headless true --browser.gatherUsageStats false\n",
        language="bash",
    )
