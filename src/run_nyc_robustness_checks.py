from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

try:
    from .nyc_common_support import compute_common_support
except ImportError:
    from nyc_common_support import compute_common_support

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OUTPUT_TABLES = ROOT / "outputs" / "tables"

PANEL_PATH = PROCESSED / "nyc_did_panel_geojson_intersection.csv"
ROUTE_TREATMENT_PATH = PROCESSED / "nyc_route_treatment_geojson_intersection.csv"
OFFICIAL_COMPARISON_PATH = OUTPUT_TABLES / "nyc_geojson_vs_official_cbd_comparison.csv"
ROBUSTNESS_OUTPUT = OUTPUT_TABLES / "nyc_treatment_definition_robustness.csv"
COMMON_SUPPORT_OUTPUT = PROCESSED / "nyc_common_support_routes.csv"
OFFICIAL_EQUIVALENCE_OUTPUT = OUTPUT_TABLES / "nyc_old_official_treatment_equivalence_audit.csv"

THRESHOLDS = [0.05, 0.10, 0.25, 0.50, 0.80]


def load_panel() -> pd.DataFrame:
    panel = pd.read_csv(PANEL_PATH)
    panel["month"] = pd.to_datetime(panel["month"])
    panel["route_id"] = panel["route_id"].astype("string").str.strip().str.upper()
    panel["post"] = panel["post"].astype(bool)
    panel["day_type"] = panel["day_type"].astype("string")
    panel["period"] = panel["period"].astype("string")
    panel["average_speed"] = pd.to_numeric(panel["average_speed"], errors="coerce")
    return panel.dropna(subset=["average_speed"]).copy()


def load_treatment_definitions() -> pd.DataFrame:
    treatment = pd.read_csv(ROUTE_TREATMENT_PATH)
    treatment["route_id"] = treatment["route_id"].astype("string").str.strip().str.upper()
    treatment["any_intersection"] = treatment["cbd_route"].astype(bool)
    treatment["max_share_length_in_cbd"] = pd.to_numeric(
        treatment["max_share_length_in_cbd"], errors="coerce"
    ).fillna(0)

    definitions = treatment[["route_id", "any_intersection"]].copy()
    for threshold in THRESHOLDS:
        suffix = f"{int(threshold * 100):02d}"
        definitions[f"max_share_ge_{suffix}pct"] = treatment["max_share_length_in_cbd"].ge(threshold)

    official = pd.read_csv(OFFICIAL_COMPARISON_PATH)
    official["route_id"] = official["route_id"].astype("string").str.strip().str.upper()
    official["old_official_source_union"] = official["in_any_old_cbd_source"].astype(bool)
    return definitions.merge(
        official[["route_id", "old_official_source_union"]], on="route_id", how="left"
    ).fillna({"old_official_source_union": False})


def samples(panel: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    weekday = panel.loc[panel["day_type"].eq("1")].copy()
    return [
        ("weekday_all_periods", weekday),
        ("all_day_types_all_periods", panel.copy()),
        ("weekday_peak", weekday.loc[weekday["period"].eq("Peak")].copy()),
        ("weekday_off_peak", weekday.loc[weekday["period"].eq("Off-Peak")].copy()),
    ]


def formula_for(data: pd.DataFrame) -> str:
    terms = ["did", "C(route_id)", "C(month)"]
    if data["period"].nunique() > 1:
        terms.append("C(period)")
    if data["day_type"].nunique() > 1:
        terms.append("C(day_type)")
    return "average_speed ~ " + " + ".join(terms)


def estimate_did(data: pd.DataFrame, treatment_column: str) -> dict[str, float | int | str]:
    df = data.copy()
    df["treated"] = df[treatment_column].fillna(False).astype(bool)
    df["did"] = df["treated"].astype(int) * df["post"].astype(int)
    treated_routes = int(df.loc[df["treated"], "route_id"].nunique())
    control_routes = int(df.loc[~df["treated"], "route_id"].nunique())
    base = {
        "rows": len(df),
        "routes": int(df["route_id"].nunique()),
        "treated_routes": treated_routes,
        "control_routes": control_routes,
    }
    if treated_routes == 0 or df["did"].nunique() < 2:
        return {
            "estimate_mph": np.nan, "std_error": np.nan, "p_value": np.nan,
            "ci_low": np.nan, "ci_high": np.nan, **base,
        }

    model = smf.ols(formula_for(df), data=df).fit(
        cov_type="cluster", cov_kwds={"groups": df["route_id"]}
    )
    estimate = float(model.params["did"])
    se = float(model.bse["did"])
    return {
        "estimate_mph": estimate,
        "std_error": se,
        "p_value": float(model.pvalues["did"]),
        "ci_low": estimate - 1.96 * se,
        "ci_high": estimate + 1.96 * se,
        **base,
    }


def treatment_label(column: str) -> str:
    if column == "any_intersection":
        return "GeoJSON any policy-date route shape intersects CBD"
    if column == "old_official_source_union":
        return "Old official CBD route/speed source union"
    percent = column.removeprefix("max_share_ge_").removesuffix("pct")
    return f"GeoJSON max shape share in CBD >= {int(percent)}%"


def main() -> None:
    OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)
    panel = load_panel()
    support_audit = compute_common_support(panel)
    support_audit.to_csv(COMMON_SUPPORT_OUTPUT, index=False)
    common_support_routes = set(
        support_audit.loc[support_audit["in_common_support"], "route_id"]
    )
    panel = panel.loc[panel["route_id"].isin(common_support_routes)].copy()
    definitions = load_treatment_definitions()
    panel = panel.merge(definitions, on="route_id", how="left", validate="many_to_one")

    route_definitions = panel[
        ["route_id", "any_intersection", "old_official_source_union"]
    ].drop_duplicates("route_id")
    mismatches = route_definitions.loc[
        route_definitions["any_intersection"].ne(route_definitions["old_official_source_union"])
    ]
    old_official_identical = mismatches.empty
    pd.DataFrame([{
        "analysis_routes": len(route_definitions),
        "geojson_treated_routes": int(route_definitions["any_intersection"].sum()),
        "old_official_treated_routes": int(route_definitions["old_official_source_union"].sum()),
        "mismatched_routes": len(mismatches),
        "identical_in_common_support_sample": old_official_identical,
        "interpretation": (
            "Mechanical duplicate; excluded from robustness estimates"
            if old_official_identical
            else "Distinct treatment definition; retained as robustness estimate"
        ),
    }]).to_csv(OFFICIAL_EQUIVALENCE_OUTPUT, index=False)

    treatment_columns = ["any_intersection"] + [
        f"max_share_ge_{int(threshold * 100):02d}pct" for threshold in THRESHOLDS
    ]
    if not old_official_identical:
        treatment_columns.append("old_official_source_union")

    rows: list[dict[str, object]] = []
    for sample_name, sample_df in samples(panel):
        for treatment_column in treatment_columns:
            rows.append({
                "sample": sample_name,
                "treatment_column": treatment_column,
                "treatment_definition": treatment_label(treatment_column),
                "common_support_sample": True,
                **estimate_did(sample_df, treatment_column),
            })

    results = pd.DataFrame(rows)
    results.to_csv(ROBUSTNESS_OUTPUT, index=False)
    print("NYC robustness checks complete")
    print(f"Rows written: {len(results)}")
    print(f"Common-support routes: {len(common_support_routes)}")
    print(f"Wrote {ROBUSTNESS_OUTPUT}")
    if old_official_identical:
        print(
            "Old official-source union is identical to the main treatment in the common-support "
            "sample; it was excluded as a mechanical duplicate."
        )
    print(results.loc[
        results["sample"].eq("weekday_all_periods"),
        ["treatment_column", "treated_routes", "estimate_mph", "std_error", "p_value"],
    ].to_string(index=False))


if __name__ == "__main__":
    main()

