from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BOSTON_RAW = ROOT / "data" / "raw" / "Boston"
PROCESSED = ROOT / "data" / "processed"
OUTPUT_TABLES = ROOT / "outputs" / "tables"

ARRIVAL_DIR_PATTERN = "MBTA_Bus_Arrival_Departure_Times_*"
ARRIVAL_FILE_PATTERN = "MBTA-Bus-Arrival-Departure-Times_*.csv"
STOP_EVENTS = BOSTON_RAW / "mbtabus" / "MBTABUSSTOPS_PT_EVENTS.dbf"

MONTHLY_OUTPUT = PROCESSED / "boston_bus_speeds_monthly.csv"
AUDIT_OUTPUT = OUTPUT_TABLES / "boston_bus_speed_build_audit.csv"

POLICY_MONTH = pd.Timestamp("2025-01-01")
MIN_SEGMENT_MINUTES = 0.25
MAX_SEGMENT_MINUTES = 120
MIN_SEGMENT_MILES = 0.01
MAX_SEGMENT_SPEED_MPH = 80

ARRIVAL_COLUMNS = [
    "service_date",
    "route_id",
    "direction_id",
    "half_trip_id",
    "stop_id",
    "time_point_order",
    "actual",
]


def normalize_route_id(series: pd.Series) -> pd.Series:
    values = series.astype("string").str.strip().str.upper()
    numeric = values.str.fullmatch(r"\d+")
    values.loc[numeric] = values.loc[numeric].str.lstrip("0").replace("", "0")
    return values


def direction_to_code(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.strip().str.upper()
    return cleaned.map({"OUTBOUND": 0, "INBOUND": 1}).astype("Int64")


def period_from_hour(hour: pd.Series) -> pd.Series:
    return np.select(
        [
            hour.between(6, 9, inclusive="left"),
            hour.between(15, 19, inclusive="left"),
        ],
        ["Peak", "Peak"],
        default="Off-Peak",
    )


def day_type_from_date(service_date: pd.Series) -> pd.Series:
    weekday = service_date.dt.weekday
    return np.where(weekday < 5, "Weekday", np.where(weekday == 5, "Saturday", "Sunday"))


def actual_seconds_since_midnight(actual: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(actual, utc=True, errors="coerce")
    base = pd.Timestamp("1900-01-01", tz="UTC")
    return (parsed - base).dt.total_seconds()


def find_arrival_files() -> list[Path]:
    files: list[Path] = []
    for directory in sorted(BOSTON_RAW.glob(ARRIVAL_DIR_PATTERN)):
        if directory.is_dir():
            files.extend(sorted(directory.glob(ARRIVAL_FILE_PATTERN)))
    return files


def load_stop_measure_lookup() -> pd.DataFrame:
    if not STOP_EVENTS.exists():
        raise FileNotFoundError(f"Missing Boston stop event table: {STOP_EVENTS}")

    stops = gpd.read_file(STOP_EVENTS)
    stops = pd.DataFrame(stops.drop(columns="geometry", errors="ignore"))
    stops["route_key"] = normalize_route_id(stops["MBTA_ROUTE"])
    stops["direction_code"] = pd.to_numeric(stops["DIRECTION"], errors="coerce").astype("Int64")
    stops["stop_id"] = stops["STOP_ID"].astype("string").str.strip()
    stops["measure_miles"] = pd.to_numeric(stops["MEASURE"], errors="coerce")
    stops = stops.dropna(subset=["route_key", "direction_code", "stop_id", "measure_miles"])

    return (
        stops.groupby(["route_key", "direction_code", "stop_id"], as_index=False)
        .agg(measure_miles=("measure_miles", "median"))
    )


def build_segments(monthly_rows: pd.DataFrame, stop_measures: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    audit: dict[str, int] = {"raw_rows": len(monthly_rows)}

    df = monthly_rows.copy()
    df["service_date"] = pd.to_datetime(df["service_date"], errors="coerce")
    df["month"] = df["service_date"].dt.to_period("M").dt.to_timestamp()
    df["route_id"] = df["route_id"].astype("string").str.strip().str.upper()
    df["route_key"] = normalize_route_id(df["route_id"])
    df["direction_code"] = direction_to_code(df["direction_id"])
    df["half_trip_id"] = df["half_trip_id"].astype("string").str.strip()
    df["stop_id"] = df["stop_id"].astype("string").str.strip()
    df["time_point_order"] = pd.to_numeric(df["time_point_order"], errors="coerce")
    df["actual_seconds"] = actual_seconds_since_midnight(df["actual"])

    required = [
        "service_date",
        "month",
        "route_key",
        "direction_code",
        "half_trip_id",
        "stop_id",
        "time_point_order",
        "actual_seconds",
    ]
    df = df.dropna(subset=required)
    df = df[df["half_trip_id"].ne("")]
    audit["usable_timepoint_rows"] = len(df)

    df = df.merge(
        stop_measures,
        on=["route_key", "direction_code", "stop_id"],
        how="left",
        validate="many_to_one",
    )
    audit["timepoint_rows_with_measure"] = int(df["measure_miles"].notna().sum())
    df = df.dropna(subset=["measure_miles"])

    sort_cols = ["service_date", "route_key", "direction_code", "half_trip_id", "time_point_order"]
    df = df.sort_values(sort_cols)
    group_cols = ["service_date", "route_key", "direction_code", "half_trip_id"]
    df["next_stop_id"] = df.groupby(group_cols)["stop_id"].shift(-1)
    df["next_order"] = df.groupby(group_cols)["time_point_order"].shift(-1)
    df["next_actual_seconds"] = df.groupby(group_cols)["actual_seconds"].shift(-1)
    df["next_measure_miles"] = df.groupby(group_cols)["measure_miles"].shift(-1)

    segments = df[df["next_stop_id"].notna()].copy()
    segments["elapsed_minutes"] = (segments["next_actual_seconds"] - segments["actual_seconds"]) / 60
    segments["segment_miles"] = (segments["next_measure_miles"] - segments["measure_miles"]).abs()
    segments["segment_speed_mph"] = segments["segment_miles"] / (segments["elapsed_minutes"] / 60)
    segments["start_hour"] = np.floor(segments["actual_seconds"] / 3600).astype(int) % 24
    segments["period"] = period_from_hour(segments["start_hour"])
    segments["day_type"] = day_type_from_date(segments["service_date"])
    segments["post"] = segments["month"].ge(POLICY_MONTH)

    valid = (
        segments["next_order"].gt(segments["time_point_order"])
        & segments["elapsed_minutes"].between(MIN_SEGMENT_MINUTES, MAX_SEGMENT_MINUTES, inclusive="both")
        & segments["segment_miles"].ge(MIN_SEGMENT_MILES)
        & segments["segment_speed_mph"].between(0, MAX_SEGMENT_SPEED_MPH, inclusive="right")
    )
    audit["candidate_segments"] = len(segments)
    audit["valid_segments"] = int(valid.sum())
    return segments.loc[valid].copy(), audit


def aggregate_segments(segments: pd.DataFrame) -> pd.DataFrame:
    if segments.empty:
        return pd.DataFrame()

    grouped = (
        segments.groupby(["month", "route_key", "direction_id", "day_type", "period", "post"], as_index=False)
        .agg(
            segment_count=("segment_miles", "size"),
            trip_count=("half_trip_id", "nunique"),
            service_days=("service_date", "nunique"),
            total_distance_miles=("segment_miles", "sum"),
            total_runtime_hours=("elapsed_minutes", lambda values: values.sum() / 60),
            mean_segment_speed_mph=("segment_speed_mph", "mean"),
            median_segment_speed_mph=("segment_speed_mph", "median"),
        )
    )
    grouped["average_speed"] = grouped["total_distance_miles"] / grouped["total_runtime_hours"]
    grouped = grouped.rename(columns={"route_key": "route_id"})
    columns = [
        "month",
        "route_id",
        "direction_id",
        "day_type",
        "period",
        "post",
        "average_speed",
        "mean_segment_speed_mph",
        "median_segment_speed_mph",
        "segment_count",
        "trip_count",
        "service_days",
        "total_distance_miles",
        "total_runtime_hours",
    ]
    return grouped[columns].sort_values(["month", "route_id", "direction_id", "day_type", "period"])


def process_file(path: Path, stop_measures: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    monthly_rows = pd.read_csv(path, usecols=ARRIVAL_COLUMNS, dtype="string")
    segments, audit = build_segments(monthly_rows, stop_measures)
    monthly = aggregate_segments(segments)

    audit_row: dict[str, object] = {
        "source_file": str(path.relative_to(ROOT)),
        **audit,
        "monthly_panel_rows": len(monthly),
        "routes": monthly["route_id"].nunique() if not monthly.empty else 0,
        "months": monthly["month"].nunique() if not monthly.empty else 0,
    }
    return monthly, audit_row


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    OUTPUT_TABLES.mkdir(parents=True, exist_ok=True)

    files = find_arrival_files()
    if not files:
        raise FileNotFoundError(f"No Boston arrival/departure CSVs found under {BOSTON_RAW}")

    stop_measures = load_stop_measure_lookup()
    monthly_parts: list[pd.DataFrame] = []
    audit_rows: list[dict[str, object]] = []

    for path in files:
        print(f"Processing {path.relative_to(ROOT)}")
        monthly, audit = process_file(path, stop_measures)
        monthly_parts.append(monthly)
        audit_rows.append(audit)

    monthly_panel = pd.concat(monthly_parts, ignore_index=True)
    audit = pd.DataFrame(audit_rows)

    monthly_panel.to_csv(MONTHLY_OUTPUT, index=False)
    audit.to_csv(AUDIT_OUTPUT, index=False)

    print("Boston bus speed panel build complete")
    print(f"Source files: {len(files)}")
    print(f"Rows: {len(monthly_panel):,}")
    print(f"Months: {monthly_panel['month'].nunique():,}")
    print(f"Routes: {monthly_panel['route_id'].nunique():,}")
    print(f"Average speed range: {monthly_panel['average_speed'].min():.2f} to {monthly_panel['average_speed'].max():.2f} mph")
    print("Wrote:")
    print(MONTHLY_OUTPUT)
    print(AUDIT_OUTPUT)


if __name__ == "__main__":
    main()
