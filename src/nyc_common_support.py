"""Shared common-support sample construction for the NYC analyses."""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

POLICY_MONTH = pd.Timestamp("2025-01-01")


def compute_common_support(panel: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the route-level propensity-score trimming in the main notebook."""
    data = panel.copy()
    data["month"] = pd.to_datetime(data["month"])
    data["route_id"] = data["route_id"].astype("string").str.strip().str.upper()
    data["treated"] = data["cbd_route"].astype(bool).astype(int)
    data["average_speed"] = pd.to_numeric(data["average_speed"], errors="coerce")

    pre_weekday = data.loc[
        data["day_type"].astype(str).eq("1")
        & data["month"].lt(POLICY_MONTH)
        & data["average_speed"].notna()
    ].copy()
    pre_route_month = (
        pre_weekday.groupby(["route_id", "month"], as_index=False)["average_speed"].mean()
    )
    pre_slopes = (
        pre_route_month.sort_values(["route_id", "month"])
        .groupby("route_id")["average_speed"]
        .apply(lambda values: np.polyfit(np.arange(len(values)), values, 1)[0])
        .rename("pre_speed_slope")
    )
    route_overlap = (
        pre_weekday.groupby("route_id", as_index=False)
        .agg(
            treated=("treated", "first"),
            borough=("borough", "first"),
            pre_mean_speed=("average_speed", "mean"),
            pre_sd_speed=("average_speed", "std"),
        )
        .merge(pre_slopes, on="route_id", how="left")
    )

    for column in ["pre_mean_speed", "pre_sd_speed", "pre_speed_slope"]:
        route_overlap[f"z_{column}"] = (
            route_overlap[column] - route_overlap[column].mean()
        ) / route_overlap[column].std()

    propensity_model = smf.glm(
        "treated ~ z_pre_mean_speed + z_pre_sd_speed + z_pre_speed_slope + C(borough)",
        data=route_overlap,
        family=sm.families.Binomial(),
    ).fit()
    route_overlap["propensity_score"] = propensity_model.predict(route_overlap)

    treated_scores = route_overlap.loc[route_overlap["treated"].eq(1), "propensity_score"]
    control_scores = route_overlap.loc[route_overlap["treated"].eq(0), "propensity_score"]
    support_low = max(treated_scores.min(), control_scores.min())
    support_high = min(treated_scores.max(), control_scores.max())
    route_overlap["support_low"] = support_low
    route_overlap["support_high"] = support_high
    route_overlap["in_common_support"] = route_overlap["propensity_score"].between(
        support_low, support_high
    )
    return route_overlap
