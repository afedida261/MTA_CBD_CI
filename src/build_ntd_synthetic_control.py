"""Build the NTD donor synthetic-control robustness exercise.

The treated unit is the NYC CBD-exposed route network under the GeoJSON
any-intersection treatment definition. Donors are NTD agency-level fixed-route
bus systems outside the NYC area.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import minimize

try:
    from .nyc_common_support import compute_common_support
except ImportError:
    from nyc_common_support import compute_common_support


ROOT = Path(__file__).resolve().parents[1]
RAW_NTD = ROOT / "data" / "raw" / "NTD" / "May 2026 Complete Monthly Ridership (with adjustments and estimates)_260701.xlsx"
NYC_PANEL = ROOT / "data" / "processed" / "nyc_did_panel_geojson_intersection.csv"
ROUTE_TREATMENT = ROOT / "data" / "processed" / "nyc_route_treatment_geojson_intersection.csv"
OFFICIAL_COMPARISON = ROOT / "outputs" / "tables" / "nyc_geojson_vs_official_cbd_comparison.csv"
PROCESSED = ROOT / "data" / "processed"
TABLES = ROOT / "outputs" / "tables"
FIGURES = ROOT / "outputs" / "figures"

START_MONTH = "2023-08-01"
END_MONTH = "2026-05-01"
POST_MONTH = "2025-01-01"
FIXED_ROUTE_BUS_MODES = ["MB", "RB", "TB"]
DONOR_POOL_SIZE = 35  # Middle-ground cap; assess fit stability at 30 and 45 donors.
DONOR_SENSITIVITY_SIZES = [30, 35, 45]
THRESHOLDS = [0.05, 0.10, 0.25, 0.50, 0.80]

NYC_AREA_KEYWORDS = [
    "MTA",
    "NEW YORK",
    "NJ TRANSIT",
    "NEW JERSEY",
    "PORT AUTHORITY",
    "PATH",
    "LONG ISLAND",
    "METRO-NORTH",
    "JERSEY",
    "NEWARK",
]


def normalize_ntd_id(value: object) -> str:
    """Keep NTD identifiers as stable strings without float suffixes."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(5) if text.isdigit() else text


def read_ntd_measure(sheet_name: str, value_name: str) -> pd.DataFrame:
    df = pd.read_excel(
        RAW_NTD,
        sheet_name=sheet_name,
        engine="openpyxl",
        dtype={"NTD ID": str, "Legacy NTD ID": str},
    )
    id_cols = [
        "NTD ID",
        "Legacy NTD ID",
        "Agency",
        "Mode/Type of Service Status",
        "Reporter Type",
        "UACE CD",
        "UZA Name",
        "Mode",
        "TOS",
        "3 Mode",
    ]
    month_cols = [col for col in df.columns if "/" in str(col)]
    long = df.melt(id_vars=id_cols, value_vars=month_cols, var_name="month_label", value_name=value_name)
    long["month"] = pd.to_datetime(long["month_label"], format="%m/%Y")
    long[value_name] = pd.to_numeric(long[value_name], errors="coerce")
    long["ntd_id"] = long["NTD ID"].map(normalize_ntd_id)
    return long.drop(columns=["month_label"])


def load_treatment_definitions() -> pd.DataFrame:
    treatment = pd.read_csv(ROUTE_TREATMENT)
    treatment["route_id"] = treatment["route_id"].astype("string").str.strip().str.upper()
    treatment["any_intersection"] = treatment["cbd_route"].astype("string").str.lower().eq("true")
    treatment["max_share_length_in_cbd"] = pd.to_numeric(
        treatment["max_share_length_in_cbd"], errors="coerce"
    ).fillna(0)
    definitions = treatment[["route_id", "any_intersection"]].copy()
    for threshold in THRESHOLDS:
        suffix = f"{int(threshold * 100):02d}"
        definitions[f"max_share_ge_{suffix}pct"] = treatment["max_share_length_in_cbd"].ge(threshold)

    official = pd.read_csv(OFFICIAL_COMPARISON)
    official["route_id"] = official["route_id"].astype("string").str.strip().str.upper()
    official["old_official_source_union"] = official["in_any_old_cbd_source"].astype("string").str.lower().eq("true")
    return definitions.merge(
        official[["route_id", "old_official_source_union"]], on="route_id", how="left"
    ).fillna({"old_official_source_union": False})


def build_nyc_network(
    panel: pd.DataFrame,
    treatment: pd.DataFrame,
    treatment_column: str,
    common_support_routes: set[str],
) -> pd.DataFrame:
    panel = panel.copy()
    panel["route_id"] = panel["route_id"].astype("string").str.strip().str.upper()
    panel = panel.loc[panel["route_id"].isin(common_support_routes)].copy()
    panel = panel.merge(treatment[["route_id", treatment_column]], on="route_id", how="left", validate="many_to_one")
    panel["treated_route"] = panel[treatment_column].fillna(False).astype(bool)
    panel["total_mileage"] = pd.to_numeric(
        panel["total_mileage"].astype(str).str.replace(",", "", regex=False), errors="coerce"
    )
    panel["total_operating_time"] = pd.to_numeric(
        panel["total_operating_time"].astype(str).str.replace(",", "", regex=False), errors="coerce"
    )
    weekday_treated = panel[
        panel["day_type"].astype(str).eq("1") & panel["treated_route"]
    ].copy()
    nyc = (
        weekday_treated.groupby("month", as_index=False)[["total_mileage", "total_operating_time"]]
        .sum()
        .query("@START_MONTH <= month <= @END_MONTH")
        .sort_values("month")
    )
    nyc["average_speed"] = nyc["total_mileage"] / nyc["total_operating_time"]
    nyc["unit_id"] = "NYC_CBD_ROUTES"
    nyc["unit_name"] = "NYC treated route network"
    nyc["is_treated"] = True
    nyc.attrs["treated_route_count"] = int(weekday_treated["route_id"].nunique())
    return nyc[["month", "unit_id", "unit_name", "is_treated", "average_speed"]]


def build_ntd_bus_speeds() -> pd.DataFrame:
    vrm = read_ntd_measure("VRM", "bus_vrm")
    vrh = read_ntd_measure("VRH", "bus_vrh")
    keys = [
        "ntd_id",
        "NTD ID",
        "Legacy NTD ID",
        "Agency",
        "Mode/Type of Service Status",
        "Reporter Type",
        "UACE CD",
        "UZA Name",
        "Mode",
        "TOS",
        "3 Mode",
        "month",
    ]
    long = vrm.merge(vrh[keys + ["bus_vrh"]], on=keys, how="inner")
    long = long[
        (long["Mode/Type of Service Status"].eq("Active"))
        & (long["Mode"].isin(FIXED_ROUTE_BUS_MODES))
        & (long["month"].between(pd.Timestamp(START_MONTH), pd.Timestamp(END_MONTH)))
    ].copy()

    agency_month = (
        long.groupby(["ntd_id", "Agency", "UZA Name", "Reporter Type", "month"], as_index=False)[
            ["bus_vrm", "bus_vrh"]
        ]
        .sum()
        .query("bus_vrh > 0")
    )
    agency_month["average_speed"] = agency_month["bus_vrm"] / agency_month["bus_vrh"]
    agency_month.to_csv(PROCESSED / "ntd_monthly_bus_speeds.csv", index=False)
    return agency_month


def flag_nyc_area(row: pd.Series) -> bool:
    agency = str(row["Agency"]).upper()
    uza = str(row["UZA Name"]).upper()
    if "NEW YORK--JERSEY CITY--NEWARK" in uza:
        return True
    return any(keyword in agency or keyword in uza for keyword in NYC_AREA_KEYWORDS)


def select_donors(ntd: pd.DataFrame) -> pd.DataFrame:
    months_needed = pd.period_range(START_MONTH, END_MONTH, freq="M").size
    pre_start = pd.Timestamp(START_MONTH)
    post_start = pd.Timestamp(POST_MONTH)

    meta = (
        ntd.groupby(["ntd_id", "Agency", "UZA Name", "Reporter Type"], as_index=False)
        .agg(
            months=("month", "nunique"),
            pre_vrm=("bus_vrm", lambda s: s[ntd.loc[s.index, "month"].between(pre_start, post_start - pd.offsets.MonthBegin())].sum()),
            mean_speed=("average_speed", "mean"),
        )
    )
    meta["agency_upper"] = meta["Agency"].str.upper()
    meta["uza_upper"] = meta["UZA Name"].str.upper()
    meta["excluded_nyc_area"] = meta.apply(flag_nyc_area, axis=1)
    meta["full_window"] = meta["months"].eq(months_needed)
    meta["eligible_donor"] = ~meta["excluded_nyc_area"] & meta["full_window"]

    meta[meta["excluded_nyc_area"]].to_csv(
        TABLES / "ntd_synthetic_control_excluded_nyc_area_agencies.csv", index=False
    )
    eligible = meta[meta["eligible_donor"]].sort_values("pre_vrm", ascending=False)
    selected = eligible.head(DONOR_POOL_SIZE).copy()
    selected.to_csv(TABLES / "ntd_synthetic_control_donor_pool.csv", index=False)
    return selected


def fit_synthetic_control(nyc: pd.DataFrame, ntd: pd.DataFrame, selected: pd.DataFrame, treatment_column: str, treatment_label: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    treated_route_count = nyc.attrs.get("treated_route_count", np.nan)
    donor_ids = selected["ntd_id"].tolist()
    donors = ntd[ntd["ntd_id"].isin(donor_ids)].copy()
    donors["unit_id"] = donors["ntd_id"]
    donors["unit_name"] = donors["Agency"]
    donors["is_treated"] = False
    donors = donors[["month", "unit_id", "unit_name", "is_treated", "average_speed"]]

    panel = pd.concat([nyc, donors], ignore_index=True)
    pre = panel["month"] < pd.Timestamp(POST_MONTH)
    pre_means = panel[pre].groupby("unit_id")["average_speed"].mean()
    panel["pre_mean_speed"] = panel["unit_id"].map(pre_means)
    panel["centered_speed"] = panel["average_speed"] - panel["pre_mean_speed"]

    pivot = panel.pivot(index="month", columns="unit_id", values="centered_speed").sort_index()
    pre_pivot = pivot.loc[pivot.index < pd.Timestamp(POST_MONTH)]
    y_pre = pre_pivot["NYC_CBD_ROUTES"].to_numpy()
    x_pre = pre_pivot[donor_ids].to_numpy()

    def objective(weights: np.ndarray) -> float:
        residual = y_pre - x_pre @ weights
        return float(np.mean(residual**2))

    n = len(donor_ids)
    initial = np.repeat(1 / n, n)
    constraints = [{"type": "eq", "fun": lambda weights: np.sum(weights) - 1}]
    bounds = [(0, 1)] * n
    result = minimize(objective, initial, method="SLSQP", bounds=bounds, constraints=constraints)
    if not result.success:
        raise RuntimeError(f"Synthetic-control optimization failed: {result.message}")

    weights = np.where(result.x < 1e-10, 0, result.x)
    weights = weights / weights.sum()
    synthetic = pivot[donor_ids].to_numpy() @ weights

    monthly = pd.DataFrame(
        {
            "month": pivot.index,
            "treated_centered_speed": pivot["NYC_CBD_ROUTES"].to_numpy(),
            "synthetic_centered_speed": synthetic,
        }
    )
    monthly["gap_mph"] = monthly["treated_centered_speed"] - monthly["synthetic_centered_speed"]
    monthly["post"] = monthly["month"] >= pd.Timestamp(POST_MONTH)

    pre_gap = monthly.loc[~monthly["post"], "gap_mph"]
    post_monthly = monthly[monthly["post"]]
    summary = pd.DataFrame(
        [
            {
                "outcome": "pre-mean-centered monthly average speed trajectory, mph",
                "treatment_column": treatment_column,
                "treatment_definition": treatment_label,
                "treated_unit": "NYC treated route network",
                "treated_routes": treated_route_count,
                "donor_pool_size": len(donor_ids),
                "pre_months_per_donor": int((~monthly["post"]).sum()),
                "pre_months": int((~monthly["post"]).sum()),
                "post_months": int(monthly["post"].sum()),
                "pre_rmspe": float(np.sqrt(np.mean(pre_gap**2))),
                "post_mean_gap_mph": float(post_monthly["gap_mph"].mean()),
                "post_mean_treated_change_mph": float(post_monthly["treated_centered_speed"].mean()),
                "post_mean_synthetic_change_mph": float(post_monthly["synthetic_centered_speed"].mean()),
                "bus_modes": ",".join(FIXED_ROUTE_BUS_MODES),
            }
        ]
    )

    weights_table = selected[["ntd_id", "Agency", "UZA Name", "pre_vrm", "mean_speed"]].copy()
    weights_table["treatment_column"] = treatment_column
    weights_table["treatment_definition"] = treatment_label
    weights_table["weight"] = weights
    weights_table = weights_table.sort_values("weight", ascending=False)
    monthly["treatment_column"] = treatment_column
    monthly["treatment_definition"] = treatment_label
    return monthly, summary, weights_table


def save_figures(monthly: pd.DataFrame) -> None:
    policy_date = pd.Timestamp(POST_MONTH)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(monthly["month"], monthly["treated_centered_speed"], marker="o", label="NYC CBD routes")
    ax.plot(monthly["month"], monthly["synthetic_centered_speed"], marker="o", label="Synthetic NTD control")
    ax.axvline(policy_date, color="black", linestyle="--", linewidth=1)
    ax.set_ylabel("Centered average speed, mph")
    ax.set_xlabel("Month")
    ax.set_title("NYC CBD route network vs synthetic NTD control")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIGURES / "nyc_cbd_ntd_synthetic_control_fit.png", dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.axhline(0, color="black", linewidth=1)
    ax.plot(monthly["month"], monthly["gap_mph"], marker="o", color="#B23A48")
    ax.axvline(policy_date, color="black", linestyle="--", linewidth=1)
    ax.set_ylabel("NYC minus synthetic, mph")
    ax.set_xlabel("Month")
    ax.set_title("Synthetic-control gap")
    fig.tight_layout()
    fig.savefig(FIGURES / "nyc_cbd_ntd_synthetic_control_gap.png", dpi=200)
    plt.close(fig)


def treatment_label(treatment_column: str) -> str:
    if treatment_column == "any_intersection":
        return "GeoJSON any policy-date route shape intersects CBD geofence"
    if treatment_column == "old_official_source_union":
        return "Old official CBD route/speed source union"
    percent = treatment_column.removeprefix("max_share_ge_").removesuffix("pct")
    return f"GeoJSON max shape share in CBD >= {int(percent)}%"


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    treatment = load_treatment_definitions()
    panel = pd.read_csv(NYC_PANEL, parse_dates=["month"])
    panel["route_id"] = panel["route_id"].astype("string").str.strip().str.upper()
    support_audit = compute_common_support(panel)
    common_support_routes = set(
        support_audit.loc[support_audit["in_common_support"], "route_id"]
    )
    support_definitions = treatment.loc[
        treatment["route_id"].isin(common_support_routes),
        ["route_id", "any_intersection", "old_official_source_union"],
    ]
    old_official_identical = support_definitions["any_intersection"].equals(
        support_definitions["old_official_source_union"]
    )
    treatment_columns = ["any_intersection"] + [
        f"max_share_ge_{int(threshold * 100):02d}pct" for threshold in THRESHOLDS
    ]
    if not old_official_identical:
        treatment_columns.append("old_official_source_union")
    ntd = build_ntd_bus_speeds()
    selected = select_donors(ntd)

    summaries = []
    monthly_results = []
    weights_results = []
    for treatment_column in treatment_columns:
        label = treatment_label(treatment_column)
        nyc = build_nyc_network(panel, treatment, treatment_column, common_support_routes)
        monthly, summary, weights = fit_synthetic_control(
            nyc, ntd, selected, treatment_column, label
        )
        summaries.append(summary)
        monthly_results.append(monthly)
        weights_results.append(weights)
        if treatment_column == "any_intersection":
            monthly.to_csv(TABLES / "nyc_cbd_ntd_synthetic_control_monthly_results.csv", index=False)
            summary.to_csv(TABLES / "nyc_cbd_ntd_synthetic_control_summary.csv", index=False)
            weights.to_csv(TABLES / "nyc_cbd_ntd_synthetic_control_weights.csv", index=False)
            save_figures(monthly)

    summary_all = pd.concat(summaries, ignore_index=True)
    monthly_all = pd.concat(monthly_results, ignore_index=True)
    weights_all = pd.concat(weights_results, ignore_index=True)
    summary_all.to_csv(TABLES / "nyc_cbd_ntd_synthetic_control_robustness_summary.csv", index=False)
    monthly_all.to_csv(TABLES / "nyc_cbd_ntd_synthetic_control_robustness_monthly_results.csv", index=False)
    weights_all.to_csv(TABLES / "nyc_cbd_ntd_synthetic_control_robustness_weights.csv", index=False)

    # Fixed donor-count sensitivity for the primary treatment; this is a stability
    # diagnostic, not a search for the donor count with the best pre-fit.
    sensitivity = []
    primary_nyc = build_nyc_network(panel, treatment, "any_intersection", common_support_routes)
    for donor_count in DONOR_SENSITIVITY_SIZES:
        _, sensitivity_summary, _ = fit_synthetic_control(
            primary_nyc, ntd, selected.head(donor_count), "any_intersection",
            treatment_label("any_intersection"),
        )
        sensitivity_summary["sensitivity_donor_count"] = donor_count
        sensitivity.append(sensitivity_summary)
    pd.concat(sensitivity, ignore_index=True).to_csv(
        TABLES / "nyc_cbd_ntd_synthetic_control_donor_count_sensitivity.csv", index=False
    )

    print(summary_all[[
        "treatment_column", "treated_routes", "pre_rmspe", "post_mean_gap_mph",
        "post_mean_treated_change_mph", "post_mean_synthetic_change_mph",
    ]].to_string(index=False))
    if old_official_identical:
        print(
            "Old official-source union is identical to any intersection in the common-support "
            "sample; it was excluded as a mechanical duplicate."
        )


if __name__ == "__main__":
    main()

