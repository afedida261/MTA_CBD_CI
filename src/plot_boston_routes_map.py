from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

from build_boston_bus_speed_panel import MONTHLY_OUTPUT, normalize_route_id


ROOT = Path(__file__).resolve().parents[1]
BOSTON_RAW = ROOT / "data" / "raw" / "Boston"
PROCESSED = ROOT / "data" / "processed"
OUTPUT_FIGURES = ROOT / "outputs" / "figures"
OUTPUT_TABLES = ROOT / "outputs" / "tables"

BOSTON_ROUTES_SHP = BOSTON_RAW / "mbtabus" / "MBTABUSROUTES_ARC.shp"
MAP_OUTPUT = OUTPUT_FIGURES / "boston_bus_routes_map.png"
MAP_SHAPES_OUTPUT = PROCESSED / "boston_route_map_shapes.geojson"
MAP_SUMMARY_OUTPUT = OUTPUT_TABLES / "boston_route_map_summary.csv"


def load_speed_panel_routes() -> set[str]:
    if not MONTHLY_OUTPUT.exists():
        raise FileNotFoundError(
            f"Missing Boston speed panel: {MONTHLY_OUTPUT}. Run src/build_boston_bus_speed_panel.py first."
        )

    panel = pd.read_csv(MONTHLY_OUTPUT, usecols=["route_id"])
    return set(normalize_route_id(panel["route_id"]).dropna().unique())


def build_route_map() -> gpd.GeoDataFrame:
    if not BOSTON_ROUTES_SHP.exists():
        raise FileNotFoundError(f"Missing Boston route shapefile: {BOSTON_ROUTES_SHP}")

    speed_panel_routes = load_speed_panel_routes()
    routes = gpd.read_file(BOSTON_ROUTES_SHP)
    routes["route_id"] = normalize_route_id(routes["MBTA_ROUTE"])
    routes["in_speed_panel"] = routes["route_id"].isin(speed_panel_routes)
    routes["map_group"] = "GIS route not in speed panel"
    routes.loc[routes["in_speed_panel"], "map_group"] = "Route in Boston speed panel"
    routes["geometry"] = routes.geometry.simplify(20, preserve_topology=True)
    return routes[
        ["route_id", "DIRECTION", "ROUTE_DESC", "SHAPE_LEN", "in_speed_panel", "map_group", "geometry"]
    ].copy()


def plot_route_map(route_map: gpd.GeoDataFrame) -> None:
    OUTPUT_FIGURES.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 11))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f7f7f4")

    not_panel = route_map[~route_map["in_speed_panel"]]
    in_panel = route_map[route_map["in_speed_panel"]]

    not_panel.plot(
        ax=ax,
        color="#b7b7b7",
        linewidth=0.65,
        alpha=0.55,
        label="GIS routes not in speed panel",
    )
    in_panel.plot(
        ax=ax,
        color="#0b6e69",
        linewidth=0.95,
        alpha=0.9,
        label="Routes in Boston speed panel",
    )

    ax.set_title("MBTA Boston Bus Routes Used for the Processed Speed Panel", fontsize=14, pad=14)
    ax.set_axis_off()
    ax.legend(loc="lower left", frameon=True, framealpha=0.92, fontsize=9)

    route_summary = route_map.drop_duplicates("route_id")
    caption = (
        f"GIS routes shown: {len(route_summary)}. "
        f"Routes represented in processed Boston speed panel: {int(route_summary['in_speed_panel'].sum())}."
    )
    fig.text(0.12, 0.08, caption, fontsize=9, color="#333333")
    fig.savefig(MAP_OUTPUT, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    route_map = build_route_map()
    route_map.to_file(MAP_SHAPES_OUTPUT, driver="GeoJSON")

    summary = (
        route_map.drop_duplicates("route_id")
        .groupby("map_group")
        .size()
        .rename("routes")
        .reset_index()
    )
    summary.to_csv(MAP_SUMMARY_OUTPUT, index=False)
    plot_route_map(route_map)

    print("Boston route map build complete")
    print(f"Wrote {MAP_OUTPUT}")
    print(f"Wrote {MAP_SHAPES_OUTPUT}")
    print(f"Wrote {MAP_SUMMARY_OUTPUT}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
