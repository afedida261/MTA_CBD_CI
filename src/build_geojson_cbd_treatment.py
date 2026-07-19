from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import wkt
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
NYC_RAW = ROOT / "data" / "raw" / "NYC"
PROCESSED = ROOT / "data" / "processed"
OUTPUT_TABLES = ROOT / "outputs" / "tables"

BUS_ROUTES_GEOJSON = NYC_RAW / "nyc_bus_routes_20260706.geojson"
CBD_GEOFENCE = NYC_RAW / "nyc_cbd_geofence.csv"
CBD_BUS_ROUTES = NYC_RAW / "nyc_official_cbd_bus_routes_20260625.csv"
CBD_BUS_SPEEDS = NYC_RAW / "nyc_official_cbd_bus_speeds_20260625.csv"
BUS_SPEEDS = NYC_RAW / "nyc_bus_speeds_raw.csv"

ROUTE_TREATMENT_OUTPUT = PROCESSED / "nyc_route_treatment_geojson_intersection.csv"
SHAPE_AUDIT_OUTPUT = OUTPUT_TABLES / "nyc_route_shape_cbd_spatial_audit.csv"
COMPARISON_OUTPUT = OUTPUT_TABLES / "nyc_geojson_vs_official_cbd_comparison.csv"
COMBINED_PANEL_OUTPUT = PROCESSED / "nyc_did_panel_geojson_intersection.csv"
CBD_PANEL_OUTPUT = PROCESSED / "nyc_did_panel_geojson_intersection_treated_routes.csv"
NON_CBD_PANEL_OUTPUT = PROCESSED / "nyc_did_panel_geojson_intersection_control_routes.csv"

PROJECTED_CRS = "EPSG:2263"  # NAD83 / New York Long Island, feet.
ANALYSIS_START = pd.Timestamp("2023-08-01")
POLICY_MONTH = pd.Timestamp("2025-01-01")
POLICY_DATE = pd.Timestamp("2025-01-05")
ANALYSIS_END = pd.Timestamp("2026-05-01")
IN_CBD_SHARE_THRESHOLD = 0.80  # Diagnostic only; treatment is any CBD intersection.

REQUIRED_GEOJSON_COLUMNS = {
    "route_id",
    "route_short_name",
    "route_type",
    "trip_type",
    "direction_id",
    "shape_id",
    "valid_from",
    "valid_to",
    "in_effect",
    "geometry",
}


def normalize_route_id(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.upper()


def unique_join(values: pd.Series) -> str:
    cleaned = sorted({str(value) for value in values.dropna() if str(value).strip()})
    return "; ".join(cleaned)


def read_bus_speeds() -> pd.DataFrame:
    speeds = pd.read_csv(BUS_SPEEDS)
    speeds.columns = speeds.columns.str.strip()
    speeds["route_id"] = normalize_route_id(speeds["route_id"])
    speeds["borough"] = speeds["borough"].astype("string").str.strip()
    speeds["month"] = pd.to_datetime(speeds["month"], errors="coerce")
    speeds["average_speed"] = pd.to_numeric(speeds["average_speed"], errors="coerce")
    return speeds


def read_analysis_bus_speeds() -> pd.DataFrame:
    speeds = read_bus_speeds()
    mask = speeds["month"].between(ANALYSIS_START, ANALYSIS_END, inclusive="both")
    return speeds.loc[mask].copy()


def route_borough_coverage(analysis_speeds: pd.DataFrame) -> pd.DataFrame:
    coverage = (
        analysis_speeds.groupby("route_id", as_index=False)
        .agg(
            boroughs_in_bus_speeds=("borough", unique_join),
            appears_in_outcome_window=("route_id", "size"),
            has_non_staten_island_rows=(
                "borough",
                lambda values: bool(values.ne("Staten Island").any()),
            ),
            has_staten_island_rows=(
                "borough",
                lambda values: bool(values.eq("Staten Island").any()),
            ),
        )
    )
    coverage["route_in_analysis_outcome"] = True
    return coverage


def load_cbd_geofence() -> gpd.GeoDataFrame:
    geofence = pd.read_csv(CBD_GEOFENCE)
    geometries = [wkt.loads(value) for value in geofence["polygon"].dropna()]
    if not geometries:
        raise ValueError(f"No CBD geofence polygons found in {CBD_GEOFENCE}")
    return gpd.GeoDataFrame(geometry=[unary_union(geometries)], crs="EPSG:4326")


def read_bus_routes_geojson() -> gpd.GeoDataFrame:
    if not BUS_ROUTES_GEOJSON.exists():
        raise FileNotFoundError(f"Missing Bus Routes GeoJSON: {BUS_ROUTES_GEOJSON}")

    routes = gpd.read_file(BUS_ROUTES_GEOJSON, engine="pyogrio")
    missing = sorted(REQUIRED_GEOJSON_COLUMNS - set(routes.columns))
    if missing:
        raise ValueError(f"GeoJSON is missing required columns: {missing}")

    routes["route_id"] = normalize_route_id(routes["route_id"])
    routes["route_short_name"] = routes["route_short_name"].astype("string").str.strip()
    routes["route_type"] = routes["route_type"].astype("string").str.strip()
    routes["trip_type"] = routes["trip_type"].astype("string").str.strip()
    routes["direction_id"] = routes["direction_id"].astype("string").str.strip()
    routes["shape_id"] = routes["shape_id"].astype("string").str.strip()
    routes["in_effect"] = routes["in_effect"].astype("string").str.strip().str.lower()
    routes["valid_from"] = pd.to_datetime(routes["valid_from"], errors="coerce")
    routes["valid_to"] = pd.to_datetime(routes["valid_to"], errors="coerce")
    return routes


def filter_routes_to_window_and_outcome(
    routes: gpd.GeoDataFrame,
    route_coverage: pd.DataFrame,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    # Freeze exposure at policy onset so post-policy route changes cannot determine
    # treatment. Open-ended shapes are treated as active through the policy date.
    valid_to = routes["valid_to"].fillna(pd.Timestamp.max)
    active_at_policy = routes["valid_from"].le(POLICY_DATE) & valid_to.ge(POLICY_DATE)
    routes_window = routes.loc[active_at_policy].copy()

    outcome_routes = route_coverage.loc[
        route_coverage["route_in_analysis_outcome"], ["route_id", "has_non_staten_island_rows"]
    ]
    routes_window = routes_window.merge(outcome_routes, on="route_id", how="left")
    routes_window["in_analysis_outcome"] = routes_window["has_non_staten_island_rows"].notna()
    routes_window["included_in_main_analysis"] = (
        routes_window["in_analysis_outcome"] & routes_window["has_non_staten_island_rows"]
    )

    dropped = (
        routes_window.loc[~routes_window["included_in_main_analysis"]]
        .groupby("route_id", as_index=False)
        .agg(
            dropped_shape_count=("shape_id", "size"),
            dropped_has_outcome=("in_analysis_outcome", "any"),
            dropped_reason=(
                "included_in_main_analysis",
                lambda values: "GeoJSON route absent from non-Staten-Island outcome window",
            ),
        )
    )
    main_routes = routes_window.loc[routes_window["included_in_main_analysis"]].copy()
    return main_routes, dropped


def build_shape_audit(routes: gpd.GeoDataFrame, cbd: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    routes_projected = routes.to_crs(PROJECTED_CRS)
    cbd_union = cbd.to_crs(PROJECTED_CRS).geometry.iloc[0]

    routes_projected["intersects_cbd"] = routes_projected.geometry.intersects(cbd_union)
    routes_projected["within_cbd"] = routes_projected.geometry.within(cbd_union)
    routes_projected["distance_to_cbd_km"] = (
        routes_projected.geometry.distance(cbd_union) * 0.3048 / 1_000
    )
    routes_projected["shape_length_total"] = routes_projected.geometry.length
    routes_projected["shape_length_in_cbd"] = routes_projected.geometry.intersection(cbd_union).length
    routes_projected["share_length_in_cbd"] = (
        routes_projected["shape_length_in_cbd"] / routes_projected["shape_length_total"]
    ).fillna(0)
    routes_projected["high_share_in_cbd"] = (
        routes_projected["share_length_in_cbd"] >= IN_CBD_SHARE_THRESHOLD
    )

    columns = [
        "route_id",
        "route_short_name",
        "route_type",
        "trip_type",
        "direction_id",
        "shape_id",
        "valid_from",
        "valid_to",
        "in_effect",
        "intersects_cbd",
        "within_cbd",
        "distance_to_cbd_km",
        "shape_length_total",
        "shape_length_in_cbd",
        "share_length_in_cbd",
        "high_share_in_cbd",
        "geometry",
    ]
    return routes_projected[columns].copy()


def infer_route_relation(row: pd.Series) -> str:
    if not bool(row["any_shape_intersects_cbd"]):
        return "Non-CBD"
    if bool(row["any_shape_within_cbd"]) or bool(row["any_high_share_shape_in_cbd"]):
        return "In CBD"
    return "Crossing CBD"


def build_route_treatment(
    shape_audit: gpd.GeoDataFrame,
    route_coverage: pd.DataFrame,
) -> pd.DataFrame:
    route_treatment = (
        pd.DataFrame(shape_audit.drop(columns="geometry"))
        .groupby("route_id", as_index=False)
        .agg(
            route_short_names=("route_short_name", unique_join),
            route_types=("route_type", unique_join),
            trip_types=("trip_type", unique_join),
            in_effect_values=("in_effect", unique_join),
            any_shape_intersects_cbd=("intersects_cbd", "any"),
            any_shape_within_cbd=("within_cbd", "any"),
            any_high_share_shape_in_cbd=("high_share_in_cbd", "any"),
            distance_to_cbd_km=("distance_to_cbd_km", "min"),
            max_share_length_in_cbd=("share_length_in_cbd", "max"),
            total_length_in_cbd=("shape_length_in_cbd", "sum"),
            total_shape_length=("shape_length_total", "sum"),
            number_of_shapes=("shape_id", "size"),
            first_valid_from=("valid_from", "min"),
            last_valid_to=("valid_to", "max"),
        )
    )
    route_treatment["total_share_length_in_cbd"] = (
        route_treatment["total_length_in_cbd"] / route_treatment["total_shape_length"]
    ).fillna(0)

    route_treatment = route_treatment.merge(route_coverage, on="route_id", how="left")
    route_treatment["cbd_route"] = route_treatment["any_shape_intersects_cbd"]
    route_treatment["geojson_cbd_relation_inferred"] = route_treatment.apply(
        infer_route_relation, axis=1
    )
    route_treatment["treatment_definition"] = "Any route shape active on 2025-01-05 intersects CBD geofence"

    ordered_columns = [
        "route_id",
        "cbd_route",
        "geojson_cbd_relation_inferred",
        "treatment_definition",
        "any_shape_intersects_cbd",
        "any_shape_within_cbd",
        "any_high_share_shape_in_cbd",
        "distance_to_cbd_km",
        "max_share_length_in_cbd",
        "total_share_length_in_cbd",
        "total_length_in_cbd",
        "total_shape_length",
        "number_of_shapes",
        "route_short_names",
        "route_types",
        "trip_types",
        "in_effect_values",
        "first_valid_from",
        "last_valid_to",
        "boroughs_in_bus_speeds",
        "appears_in_outcome_window",
        "has_non_staten_island_rows",
        "has_staten_island_rows",
    ]
    return route_treatment[ordered_columns].sort_values("route_id").reset_index(drop=True)


def read_official_cbd_sources() -> pd.DataFrame:
    cbd_routes = pd.read_csv(CBD_BUS_ROUTES)
    cbd_routes["route_id"] = normalize_route_id(cbd_routes["Route ID"])
    cbd_routes["cbd_bus_routes_relation"] = cbd_routes["CBD Relation"].astype("string").str.strip()
    cbd_routes = (
        cbd_routes.groupby("route_id", as_index=False)
        .agg(cbd_bus_routes_relation=("cbd_bus_routes_relation", unique_join))
        .assign(in_cbd_bus_routes=True)
    )

    cbd_speeds = pd.read_csv(CBD_BUS_SPEEDS, usecols=["Route ID", "CBD Relation"])
    cbd_speeds["route_id"] = normalize_route_id(cbd_speeds["Route ID"])
    cbd_speeds["cbd_relation_norm"] = normalize_route_id(cbd_speeds["CBD Relation"])
    cbd_speeds = (
        cbd_speeds.groupby("route_id", as_index=False)
        .agg(
            cbd_bus_speeds_relations=("cbd_relation_norm", unique_join),
            has_cbd_segment_in_cbd_bus_speeds=(
                "cbd_relation_norm",
                lambda values: bool(values.eq("CBD").any()),
            ),
        )
        .assign(in_cbd_bus_speeds=True)
    )

    route_ids = sorted(set(cbd_routes["route_id"]) | set(cbd_speeds["route_id"]))
    official = pd.DataFrame({"route_id": route_ids})
    official = official.merge(cbd_routes, on="route_id", how="left")
    official = official.merge(cbd_speeds, on="route_id", how="left")
    for column in ["in_cbd_bus_routes", "in_cbd_bus_speeds", "has_cbd_segment_in_cbd_bus_speeds"]:
        official[column] = official[column].where(official[column].notna(), False).astype(bool)
    return official


def build_comparison(
    route_treatment: pd.DataFrame,
    route_coverage: pd.DataFrame,
    dropped_geojson_routes: pd.DataFrame,
) -> pd.DataFrame:
    official = read_official_cbd_sources()
    route_ids = sorted(set(route_treatment["route_id"]) | set(official["route_id"]))
    comparison = pd.DataFrame({"route_id": route_ids})
    comparison = comparison.merge(
        route_treatment[
            [
                "route_id",
                "cbd_route",
                "geojson_cbd_relation_inferred",
                "max_share_length_in_cbd",
                "total_share_length_in_cbd",
                "boroughs_in_bus_speeds",
                "has_staten_island_rows",
            ]
        ],
        on="route_id",
        how="left",
    )
    comparison = comparison.merge(official, on="route_id", how="left")
    comparison = comparison.merge(
        route_coverage[
            [
                "route_id",
                "route_in_analysis_outcome",
                "has_non_staten_island_rows",
                "has_staten_island_rows",
                "boroughs_in_bus_speeds",
            ]
        ].rename(
            columns={
                "boroughs_in_bus_speeds": "all_outcome_window_boroughs",
                "has_staten_island_rows": "all_outcome_has_staten_island_rows",
            }
        ),
        on="route_id",
        how="left",
    )
    comparison = comparison.merge(dropped_geojson_routes, on="route_id", how="left")

    bool_columns = [
        "cbd_route",
        "in_cbd_bus_routes",
        "in_cbd_bus_speeds",
        "has_cbd_segment_in_cbd_bus_speeds",
        "route_in_analysis_outcome",
        "has_non_staten_island_rows",
        "has_staten_island_rows",
        "all_outcome_has_staten_island_rows",
        "dropped_has_outcome",
    ]
    for column in bool_columns:
        comparison[column] = comparison[column].where(comparison[column].notna(), False).astype(bool)

    comparison["in_any_old_cbd_source"] = (
        comparison["in_cbd_bus_routes"] | comparison["has_cbd_segment_in_cbd_bus_speeds"]
    )
    comparison["comparison_flag"] = ""
    comparison.loc[
        comparison["cbd_route"] & ~comparison["in_any_old_cbd_source"],
        "comparison_flag",
    ] = "GeoJSON CBD route absent from old CBD sources"
    comparison.loc[
        ~comparison["cbd_route"] & comparison["in_any_old_cbd_source"],
        "comparison_flag",
    ] = "Old CBD source route not treated by GeoJSON main rule"
    comparison.loc[
        comparison["in_any_old_cbd_source"] & ~comparison["route_in_analysis_outcome"],
        "comparison_flag",
    ] = "Old CBD source route absent from outcome window"
    comparison.loc[
        comparison["all_outcome_has_staten_island_rows"] & ~comparison["has_non_staten_island_rows"],
        "comparison_flag",
    ] = "Excluded from main analysis: Staten Island only"

    return comparison.sort_values("route_id").reset_index(drop=True)


def build_panel(
    analysis_speeds: pd.DataFrame,
    route_treatment: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    main_panel = analysis_speeds.loc[analysis_speeds["borough"].ne("Staten Island")].copy()
    treatment_columns = [
        "route_id",
        "cbd_route",
        "geojson_cbd_relation_inferred",
        "any_shape_intersects_cbd",
        "any_shape_within_cbd",
        "any_high_share_shape_in_cbd",
        "distance_to_cbd_km",
        "max_share_length_in_cbd",
        "total_share_length_in_cbd",
        "number_of_shapes",
    ]
    combined = main_panel.merge(route_treatment[treatment_columns], on="route_id", how="left")
    combined["cbd_route"] = combined["cbd_route"].where(combined["cbd_route"].notna(), False).astype(bool)
    for column in ["any_shape_intersects_cbd", "any_shape_within_cbd", "any_high_share_shape_in_cbd"]:
        combined[column] = combined[column].where(combined[column].notna(), False).astype(bool)
    for column in [
        "distance_to_cbd_km",
        "max_share_length_in_cbd",
        "total_share_length_in_cbd",
        "number_of_shapes",
    ]:
        combined[column] = pd.to_numeric(combined[column], errors="coerce").fillna(0)
    combined["geojson_cbd_relation_inferred"] = combined[
        "geojson_cbd_relation_inferred"
    ].fillna("Non-CBD")
    combined["post"] = combined["month"].ge(POLICY_MONTH)

    cbd = combined.loc[combined["cbd_route"]].copy()
    non_cbd = combined.loc[~combined["cbd_route"]].copy()
    return combined, cbd, non_cbd


def assert_quality_checks(
    combined: pd.DataFrame,
    route_treatment: pd.DataFrame,
) -> None:
    if combined["borough"].eq("Staten Island").any():
        raise AssertionError("Main panel contains Staten Island rows")

    treated = route_treatment.loc[route_treatment["cbd_route"]]
    if not treated["any_shape_intersects_cbd"].all():
        raise AssertionError("At least one treated route does not intersect CBD")

    untreated = route_treatment.loc[~route_treatment["cbd_route"]]
    if untreated["any_shape_intersects_cbd"].any():
        raise AssertionError("At least one untreated route intersects CBD")

    pre_months = combined.loc[combined["month"].lt(POLICY_MONTH), "month"].nunique()
    post_months = combined.loc[combined["month"].ge(POLICY_MONTH), "month"].nunique()
    if pre_months != post_months:
        raise AssertionError(f"Unequal calendar window: {pre_months} pre months, {post_months} post months")


def print_set(title: str, values: set[str]) -> None:
    print(f"\n{title} ({len(values)}):")
    print(", ".join(sorted(values)) if values else "None")


def print_summary(
    route_treatment: pd.DataFrame,
    shape_audit: gpd.GeoDataFrame,
    comparison: pd.DataFrame,
    combined: pd.DataFrame,
    cbd: pd.DataFrame,
    non_cbd: pd.DataFrame,
) -> None:
    pre_months = combined.loc[combined["month"].lt(POLICY_MONTH), "month"].nunique()
    post_months = combined.loc[combined["month"].ge(POLICY_MONTH), "month"].nunique()
    old_cbd = comparison["in_any_old_cbd_source"]

    print("GeoJSON CBD treatment build complete")
    print(f"Bus Routes GeoJSON: {BUS_ROUTES_GEOJSON}")
    print(f"Analysis window: {ANALYSIS_START.date()} through {ANALYSIS_END.date()}")
    print(f"Policy month treated as post: {POLICY_MONTH.date()}")
    print(f"Pre months: {pre_months}; post months: {post_months}")
    print("Treatment rule: cbd_route = any route shape active on 2025-01-05 intersects CBD geofence")
    route_month_counts = combined.groupby("route_id")["month"].nunique()
    print(
        "Calendar window has equal pre/post months; route panel is not required to be balanced. "
        f"Observed months per route range from {route_month_counts.min()} to {route_month_counts.max()}."
    )
    print(f"Retained route shapes: {len(shape_audit):,}")
    print(f"Route treatment rows: {len(route_treatment):,}")
    print(f"CBD routes: {int(route_treatment['cbd_route'].sum()):,}")
    print(f"Non-CBD routes: {int((~route_treatment['cbd_route']).sum()):,}")
    print(f"Combined main panel rows: {len(combined):,}")
    print(f"CBD panel rows: {len(cbd):,}")
    print(f"Non-CBD panel rows: {len(non_cbd):,}")

    print("\nRows by borough and CBD flag:")
    print(combined.groupby(["borough", "cbd_route"]).size().rename("rows").to_string())
    print("\nRows by day_type and CBD flag:")
    print(combined.groupby(["day_type", "cbd_route"]).size().rename("rows").to_string())
    print("\nRows by period and CBD flag:")
    print(combined.groupby(["period", "cbd_route"]).size().rename("rows").to_string())

    geo_cbd_routes = set(route_treatment.loc[route_treatment["cbd_route"], "route_id"])
    old_cbd_routes = set(comparison.loc[old_cbd, "route_id"])
    outcome_routes = set(comparison.loc[comparison["route_in_analysis_outcome"], "route_id"])
    staten_only = set(
        comparison.loc[
            comparison["all_outcome_has_staten_island_rows"]
            & ~comparison["has_non_staten_island_rows"],
            "route_id",
        ]
    )
    print_set("GeoJSON CBD routes missing from old CBD sources", geo_cbd_routes - old_cbd_routes)
    print_set("Old CBD source routes not treated by GeoJSON", old_cbd_routes - geo_cbd_routes)
    print_set("Old CBD source routes absent from outcome window", old_cbd_routes - outcome_routes)
    print_set("Staten Island only routes excluded from main analysis", staten_only)

    print("\nWrote:")
    for path in [
        ROUTE_TREATMENT_OUTPUT,
        SHAPE_AUDIT_OUTPUT,
        COMPARISON_OUTPUT,
        COMBINED_PANEL_OUTPUT,
        CBD_PANEL_OUTPUT,
        NON_CBD_PANEL_OUTPUT,
    ]:
        print(path)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)

    analysis_speeds = read_analysis_bus_speeds()
    coverage = route_borough_coverage(analysis_speeds)
    routes = read_bus_routes_geojson()
    routes_filtered, dropped_geojson_routes = filter_routes_to_window_and_outcome(routes, coverage)
    cbd_geofence = load_cbd_geofence()
    shape_audit = build_shape_audit(routes_filtered, cbd_geofence)
    route_treatment = build_route_treatment(shape_audit, coverage)
    comparison = build_comparison(route_treatment, coverage, dropped_geojson_routes)
    combined, cbd, non_cbd = build_panel(analysis_speeds, route_treatment)

    assert_quality_checks(combined, route_treatment)

    route_treatment.to_csv(ROUTE_TREATMENT_OUTPUT, index=False)
    pd.DataFrame(shape_audit.drop(columns="geometry")).to_csv(SHAPE_AUDIT_OUTPUT, index=False)
    comparison.to_csv(COMPARISON_OUTPUT, index=False)
    combined.to_csv(COMBINED_PANEL_OUTPUT, index=False)
    cbd.to_csv(CBD_PANEL_OUTPUT, index=False)
    non_cbd.to_csv(NON_CBD_PANEL_OUTPUT, index=False)

    print_summary(route_treatment, shape_audit, comparison, combined, cbd, non_cbd)


if __name__ == "__main__":
    main()



