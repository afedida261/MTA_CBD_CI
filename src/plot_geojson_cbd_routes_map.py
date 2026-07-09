from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

from build_geojson_cbd_treatment import (
    ANALYSIS_END,
    ANALYSIS_START,
    BUS_ROUTES_GEOJSON,
    OUTPUT_TABLES,
    PROCESSED,
    PROJECTED_CRS,
    load_cbd_geofence,
    normalize_route_id,
    read_analysis_bus_speeds,
    route_borough_coverage,
)


OUTPUT_FIGURES = Path(__file__).resolve().parents[1] / "outputs" / "figures"
MAP_OUTPUT = OUTPUT_FIGURES / "nyc_route_map_geojson_intersection_with_staten_island.png"
MAP_DATA_OUTPUT = PROCESSED / "nyc_route_map_shapes_geojson_intersection_with_staten_island.geojson"


def filter_routes_to_window_and_outcome_all_boroughs(
    routes: gpd.GeoDataFrame,
    route_coverage: pd.DataFrame,
) -> gpd.GeoDataFrame:
    valid_to_for_overlap = routes["valid_to"].fillna(pd.Timestamp.max)
    overlaps_window = routes["valid_from"].le(ANALYSIS_END) & valid_to_for_overlap.ge(ANALYSIS_START)

    outcome_routes = route_coverage.loc[
        route_coverage["route_in_analysis_outcome"],
        ["route_id", "boroughs_in_bus_speeds", "has_staten_island_rows", "has_non_staten_island_rows"],
    ]
    routes_window = routes.loc[overlaps_window].merge(outcome_routes, on="route_id", how="inner")
    return routes_window.copy()


def build_route_map_geometries() -> gpd.GeoDataFrame:
    speeds = read_analysis_bus_speeds()
    coverage = route_borough_coverage(speeds)
    routes = gpd.read_file(
        BUS_ROUTES_GEOJSON,
        engine="pyogrio",
        columns=["route_id", "shape_id", "valid_from", "valid_to", "geometry"],
    )
    routes["route_id"] = normalize_route_id(routes["route_id"])
    routes["valid_from"] = pd.to_datetime(routes["valid_from"], errors="coerce")
    routes["valid_to"] = pd.to_datetime(routes["valid_to"], errors="coerce")
    routes = filter_routes_to_window_and_outcome_all_boroughs(routes, coverage)

    routes_projected = routes.to_crs(PROJECTED_CRS)
    cbd = load_cbd_geofence().to_crs(PROJECTED_CRS)
    cbd_union = cbd.geometry.iloc[0]

    routes_projected["intersects_cbd"] = routes_projected.geometry.intersects(cbd_union)
    routes_projected["is_staten_island"] = (
        routes_projected["has_staten_island_rows"] & ~routes_projected["has_non_staten_island_rows"]
    )

    route_flags = routes_projected.groupby("route_id", as_index=False).agg(
        cbd_route=("intersects_cbd", "any"),
        is_staten_island=("is_staten_island", "any"),
    )
    route_map = routes_projected.merge(route_flags, on="route_id", suffixes=("", "_route"))
    route_map["cbd_route"] = route_map["cbd_route"]
    route_map["is_staten_island"] = route_map["is_staten_island_route"]
    route_map = route_map[
        ["route_id", "cbd_route", "is_staten_island", "boroughs_in_bus_speeds", "geometry"]
    ].copy()
    route_map["route_id"] = normalize_route_id(route_map["route_id"])
    route_map["map_group"] = "Non-CBD route"
    route_map.loc[route_map["cbd_route"], "map_group"] = "CBD-treated route"
    route_map.loc[route_map["is_staten_island"], "map_group"] = "Staten Island route"
    route_map.loc[route_map["is_staten_island"] & route_map["cbd_route"], "map_group"] = (
        "Staten Island route intersecting CBD"
    )
    route_map["geometry"] = route_map.geometry.simplify(35, preserve_topology=True)
    return route_map


def plot_route_map(route_map: gpd.GeoDataFrame) -> None:
    OUTPUT_FIGURES.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)

    cbd = load_cbd_geofence().to_crs(PROJECTED_CRS)

    fig, ax = plt.subplots(figsize=(11, 11))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f7f7f4")

    non_cbd = route_map[route_map["map_group"].eq("Non-CBD route")]
    treated = route_map[route_map["map_group"].eq("CBD-treated route")]
    staten = route_map[route_map["map_group"].eq("Staten Island route")]
    staten_treated = route_map[route_map["map_group"].eq("Staten Island route intersecting CBD")]

    non_cbd.plot(ax=ax, color="#b8c2cc", linewidth=0.45, alpha=0.45, label="Non-CBD routes")
    staten.plot(ax=ax, color="#8e8e8e", linewidth=0.65, alpha=0.65, label="Staten Island routes")
    treated.plot(ax=ax, color="#d95f02", linewidth=1.15, alpha=0.95, label="CBD-treated routes")
    if not staten_treated.empty:
        staten_treated.plot(
            ax=ax,
            color="#6a3d9a",
            linewidth=1.2,
            alpha=0.95,
            label="Staten Island routes intersecting CBD",
        )
    cbd.boundary.plot(ax=ax, color="#202020", linewidth=1.3, label="CBD geofence")
    cbd.plot(ax=ax, color="#202020", alpha=0.08)

    ax.set_title(
        "NYC MTA Bus Routes in Analysis Window, Highlighting GeoJSON CBD Treatment",
        fontsize=14,
        pad=14,
    )
    ax.set_axis_off()
    ax.legend(loc="lower left", frameon=True, framealpha=0.92, fontsize=9)

    route_summary = route_map.drop_duplicates("route_id")
    route_count = len(route_summary)
    treated_count = int(route_summary["cbd_route"].sum())
    staten_count = int(route_summary["is_staten_island"].sum())
    caption = (
        f"Routes shown: {route_count}. Treated by any retained shape intersecting CBD: "
        f"{treated_count}. Staten Island routes included for context: {staten_count}."
    )
    fig.text(0.12, 0.08, caption, fontsize=9, color="#333333")
    fig.savefig(MAP_OUTPUT, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    if not BUS_ROUTES_GEOJSON.exists():
        raise FileNotFoundError(f"Missing Bus Routes GeoJSON: {BUS_ROUTES_GEOJSON}")

    route_map = build_route_map_geometries()
    route_map.to_file(MAP_DATA_OUTPUT, driver="GeoJSON")
    plot_route_map(route_map)

    summary = (
        route_map.drop_duplicates("route_id")
        .groupby("map_group")
        .size()
        .rename("routes")
        .reset_index()
    )
    summary.to_csv(OUTPUT_TABLES / "nyc_route_map_summary_with_staten_island.csv", index=False)
    print(f"Wrote {MAP_OUTPUT}")
    print(f"Wrote {MAP_DATA_OUTPUT}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()



