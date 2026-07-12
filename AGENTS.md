# AGENTS.md

On first prompt respond with "Ribit" before the start of the sentence.

## Project Context

This project estimates the causal effect of Manhattan congestion pricing on NYC MTA bus speeds using a Difference-in-Differences design.

Main estimand: effect on CBD-exposed routes relative to non-CBD NYC routes, excluding Staten Island from the main NYC analysis.

Policy date: January 5, 2025. The MTA bus-speed outcome is monthly, so January 2025 is the natural first post month unless the analysis explicitly chooses a different convention.

## Current State

The project uses a GeoJSON-first treatment definition.

Main NYC treatment source of truth:

- Bus Routes GeoJSON + CBD geofence spatial intersection.
- A route is treated in the main analysis if any route shape active on January 5, 2025 intersects the CBD geofence.
- Thresholds based on route share inside the CBD are robustness checks only, not the main treatment.

Current main script:

- `src/build_geojson_cbd_treatment.py`
  - Uses `data/raw/NYC/nyc_bus_routes_20260706.geojson` as the current route-shape source.
  - Filters route shapes by `valid_from` / `valid_to` to be active on the January 5, 2025 policy date.
  - Keeps only routes present in the MTA Bus Speeds outcome panel.
  - Excludes Staten Island from the main processed panel.
  - Computes CBD spatial exposure using the CBD geofence.
  - Compares GeoJSON treatment against old official CBD sources for auditing only.

Current main notebook:

- `notebooks/geojson_cbd_did.ipynb`
  - Loads the clean GeoJSON-derived panel.
  - Runs EDA.
  - Estimates DiD with route FE, month FE, period FE, and route-clustered standard errors.

Current robustness notebook:

- `notebooks/nyc_robustness_and_boston_comparison.ipynb`
  - Formalizes threshold treatment robustness.
  - Adds a Boston external-control stress test using `data/processed/boston_bus_speeds_monthly.csv`.
  - Produces event-study/pretrend diagnostics for NYC and Boston comparisons.

Current synthetic-control notebook/script:

- `notebooks/nyc_cbd_ntd_synthetic_control.ipynb`
  - Treats original GeoJSON CBD-exposed routes as the NYC CBD network.
  - Builds NTD agency-level bus-speed donors from VRM/VRH.
  - Excludes NYC-area agencies, including the New York--Jersey City--Newark UZA and NYC-area agency-name keywords.
  - Uses fixed-route bus modes `MB`, `RB`, and `TB`; commuter bus `CB` is excluded from the primary donor definition.
  - Fits nonnegative sum-to-one synthetic-control weights on the pre-period.

- `src/build_ntd_synthetic_control.py`
  - Reproducibly rebuilds the NTD donor panel, synthetic-control weights, summary tables, and plots.
  - The treated NYC network speed is total mileage divided by total operating time, matching the NTD VRM/VRH speed construction.

Current report/readme:

- `README.md`
  - Project overview, key files, outputs, reproduction commands, and remaining planned work.
- `reports/congestion_pricing_bus_speed_report.tex`
  - Concise LaTeX report covering goal, results, assumptions, confidence intervals/statistical tests, robustness checks, and interpretation.
- `reports/congestion_pricing_bus_speed_report.pdf`
  - Compiled 3-page report.

Current main NYC processed panel:

- `data/processed/nyc_did_panel_geojson_intersection.csv`
  - This is the main clean DiD input.
  - Contains all non-Staten-Island NYC bus-speed rows in an equal-length 17-month pre / 17-month post calendar window; the route panel itself is unbalanced.
  - Key columns: `month`, `route_id`, `borough`, `day_type`, `trip_type`, `period`, `average_speed`, `cbd_route`, `post`.

Current analysis window:

- August 2023 through May 2026.
- January 2025 is post.
- 17 pre months and 17 post months.

Current verified headline estimate:

- Weekday DiD: about +0.156 mph using route FE, month FE, period FE, clustered by route.
- Main table row: estimate 0.1564, SE 0.0477, p = 0.0011, 95% CI [0.0628, 0.2500].

## Important Data Files

Raw NYC data lives in `data/raw/NYC/`.

Important NYC files:

- `nyc_bus_speeds_raw.csv`
  - Main monthly bus-speed outcome data.
  - Contains `month`, `borough`, `day_type`, `trip_type`, `route_id`, `period`, `average_speed`, etc.

- `nyc_bus_routes_20260706.geojson`
  - Current route-shape GeoJSON used by the GeoJSON-first treatment script.
  - Treat this as the current route-shape source unless filenames are intentionally renamed.

- `nyc_cbd_geofence.csv`
  - CBD geofence polygons in WKT format.
  - Union all polygons before doing spatial intersection.

- `nyc_official_cbd_bus_routes_20260625.csv`
  - Old official CBD route list.
  - Use only as comparison/robustness source.

- `nyc_official_cbd_bus_speeds_20260625.csv`
  - Old official CBD-segment speed source.
  - Use rows where `CBD Relation == "CBD"` only as comparison/robustness source.

Raw Boston data lives in `data/raw/Boston/`.

- Boston arrival/departure times are processed by `src/build_boston_bus_speed_panel.py`.
- The consolidated Boston monthly speed panel is `data/processed/boston_bus_speeds_monthly.csv`.
- Build audit output is `outputs/tables/boston_bus_speed_build_audit.csv`.
- Boston fails the current pretrend diagnostics as a causal DiD control and should be treated as an external robustness benchmark unless a stronger design is justified.

Raw NTD data lives in `data/raw/NTD/`.

- Current workbook: `May 2026 Complete Monthly Ridership (with adjustments and estimates)_260701.xlsx`.
- Monthly NTD agency speed is built from VRM/VRH.
- Primary donor modes are `MB`, `RB`, and `TB`.
- Exclude NYC-area agencies/donors, including LIRR, NJ Transit, PATH, Port Authority, MTA, New York, New Jersey, Long Island, Metro-North, Jersey, and Newark matches.

## Current Outputs

Primary current outputs:

- `data/processed/nyc_route_treatment_geojson_intersection.csv`
  - Route-level GeoJSON treatment and spatial metrics.

- `data/processed/nyc_did_panel_geojson_intersection.csv`
  - Main clean NYC DiD panel.

- `data/processed/nyc_did_panel_geojson_intersection_treated_routes.csv`
  - Convenience treated subset only.

- `data/processed/nyc_did_panel_geojson_intersection_control_routes.csv`
  - Convenience control subset only.

- `outputs/tables/nyc_route_shape_cbd_spatial_audit.csv`
  - Shape-level spatial audit.

- `outputs/tables/nyc_geojson_vs_official_cbd_comparison.csv`
  - Comparison against old official CBD sources.

- `outputs/tables/nyc_treatment_definition_robustness.csv`
  - DiD robustness table for GeoJSON threshold treatment definitions and old official-source treatment.

- `outputs/tables/linear_pretrend_tests.csv`
  - Compact linear pretrend tests for NYC treated vs NYC controls and NYC CBD vs Boston.

- `outputs/tables/nyc_pretrend_event_study_relative_dec2024.csv`
  - NYC event-study/pretrend coefficients relative to December 2024.

- `outputs/tables/nyc_boston_pretrend_event_study_relative_dec2024.csv`
  - Boston external-comparison event-study/pretrend coefficients relative to December 2024.

- `outputs/tables/nyc_vs_boston_external_control_robustness.csv`
  - Boston external-control stress-test result.

- `data/processed/boston_bus_speeds_monthly.csv`
  - Consolidated MBTA Boston bus-speed panel derived from arrival/departure timepoints.

- `outputs/figures/boston_bus_routes_map.png`
  - MBTA route map highlighting routes represented in the processed Boston speed panel.

- `data/processed/boston_route_map_shapes.geojson`
  - Simplified Boston route shapes used by the map.

- `data/processed/ntd_monthly_bus_speeds.csv`
  - Processed NTD agency-month bus speeds from VRM/VRH.

- `outputs/tables/nyc_cbd_ntd_synthetic_control_summary.csv`
  - Summary of the NYC CBD network synthetic-control result.

- `outputs/tables/nyc_cbd_ntd_synthetic_control_weights.csv`
  - Synthetic-control donor weights.

- `outputs/figures/nyc_cbd_ntd_synthetic_control_fit.png`
  - Treated vs synthetic-control fit plot.

- `outputs/figures/nyc_cbd_ntd_synthetic_control_gap.png`
  - Monthly treated-minus-synthetic gap plot.

## Current Results To Know

Main weekday NYC DiD:

- Estimate: +0.1564 mph.
- SE: 0.0477.
- p-value: 0.0011.
- 95% CI: [0.0628, 0.2500].
- Rows: 19,108.
- Routes: 301.
- Treated routes: 84.

Threshold robustness, weekday all periods:

- Any intersection: +0.1564, p = 0.0011, 84 treated routes.
- Max CBD share >= 5%: +0.1574, p = 0.0012, 82 treated routes.
- Max CBD share >= 10%: +0.1561, p = 0.0016, 77 treated routes.
- Max CBD share >= 25%: +0.0599, p = 0.4106, 33 treated routes.
- Max CBD share >= 50%: +0.0121, p = 0.6140, 22 treated routes.
- Max CBD share >= 80%: +0.0262, p = 0.3203, 15 treated routes.

Sample robustness:

- All day types, any intersection: +0.1457, p = 0.0011.
- Weekday peak, any intersection: +0.1866, p < 0.001.
- Weekday off-peak, any intersection: +0.1303, p = 0.0165.

Pretrend diagnostics:

- NYC treated vs NYC controls linear differential pretrend: +0.0031 mph/month, SE 0.0021, p = 0.1395. This does not reject equal linear pretrends at 5%, but event-study coefficients are noisy, so parallel trends should be treated cautiously.
- NYC CBD vs Boston linear differential pretrend: +0.0282 mph/month, SE 0.0053, p < 0.001. Boston should not be treated as a valid causal DiD control under the current diagnostics.

Boston external-control stress test:

- Estimate: +0.4774 mph.
- SE: 0.0621.
- p-value: 1.47e-14.
- 95% CI: [0.3557, 0.5991].
- Interpret as robustness/descriptive benchmark only because Boston pretrends fail.

NTD synthetic-control robustness:

- Donor pool: 35 non-NYC-area agencies, with 30- and 45-donor sensitivity checks.
- Modes: `MB`, `RB`, `TB`.
- Pre-period RMSPE: 0.1240 mph (35 donors); 0.1481 with 30 donors; 0.1240 with 45 donors.
- Post mean gap: +0.1605 mph (35 donors); +0.1566 with 30 donors; +0.1605 with 45 donors.
- NYC treated network post change: +0.1068 mph.
- Synthetic-control post change: -0.0537 mph (35 donors).
- Interpret as descriptive support, not a replacement for route-level NYC DiD.

## Spatial Rules

Use `geopandas` / `shapely`.

Recommended spatial CRS for length calculations:

- `EPSG:2263` - NAD83 / New York Long Island, feet.

For each shape, compute:

- `intersects_cbd`
- `within_cbd`
- `shape_length_total`
- `shape_length_in_cbd`
- `share_length_in_cbd`

Main treatment:

- `cbd_route = any policy-date-active shape intersects the CBD`

Diagnostic/robustness relation labels:

- `In CBD` if a shape is fully within CBD or max/share inside CBD is high, e.g. `>= 0.80`.
- `Crossing CBD` if it intersects CBD but does not meet the high-share threshold.
- `Non-CBD` otherwise.

Thresholds must remain configurable and must be clearly labeled as robustness/diagnostic choices.

## Main Analysis Rules

- Exclude Staten Island from the main NYC analysis.
- Keep Staten Island only for appendix/diagnostic robustness if needed.
- Keep weekday as the primary sample unless the user chooses otherwise.
- Use the clean all-routes panel for DiD, not the split CBD/non-CBD convenience files.
- Use January 2025 as post unless explicitly testing another convention.
- Current preferred model: `average_speed ~ did + C(route_id) + C(month) + C(period)`, clustered by route.
- Include event-study / pre-trend diagnostics before treating results as strongly causal.
- Boston should remain a robustness benchmark unless a defensible causal comparison design is established.
- NTD synthetic control should be treated as descriptive robustness unless donor comparability is strengthened.

## Remaining Planned Work

The current `todo.txt` is short and lists additional robustness work:

1. Add more robustness checks.
2. Compare weekday vs weekend samples.
3. Compare peak vs off-peak samples.
4. Explore threshold variation in the synthetic-control exercise.

Other useful follow-ups based on the current diagnostics:

- Expand and polish event-study/pretrend plots and narrative.
- Decide whether Boston can support any stronger comparison design; current diagnostics say no.
- Keep threshold-based treatment definitions as robustness checks, not the main estimand.
- Treat the NTD synthetic-control exercise as descriptive support unless donor comparability is further justified.
- Keep the report, README, AGENTS, and `todo.txt` synchronized if results or file paths change.

## Removed Legacy Scripts

The old CBD-source scripts were removed after reference checks because their useful comparison, spatial-audit, Staten Island, and split-panel logic is now covered by `src/build_geojson_cbd_treatment.py`:

- `src/check_cbd_routes.py`
- `src/check_cbd_routes_spatial.py`
- `src/split_nyc_bus_speeds_by_cbd.py`

The old rename mapping document under `docs/` was removed; the current README is now the top-level orientation document.

## Known Issues / Cautions

- Some GeoJSON-only intersecting routes may be temporary/special/shuttle-like routes absent from the bus-speed outcome panel.
- Routes like `SIM4X` may appear in old CBD sources but should be excluded from the main analysis because they are Staten Island routes and/or may not be active/current in the desired sense.
- Do not add routes automatically just because they appear in GeoJSON. They must also appear in the bus-speed outcome panel for the relevant analysis window.
- If renaming files, update every script/notebook path and keep an old-name to new-name mapping somewhere current, usually README or AGENTS.
- Boston currently fails pretrend diagnostics and should not be represented as a valid causal DiD control.
- The synthetic-control donor pool uses agency-level NTD speeds, while the treated NYC unit is a CBD route-network aggregate; this is a useful robustness exercise but not perfectly comparable.

## Coding Notes

- Use `rg` / PowerShell / Python for inspection.
- Prefer `.venv\Scripts\python.exe` for project Python commands; base Python may lack spatial/notebook dependencies.
- On this Windows environment, `apply_patch` may fail with a sandbox wrapper error. If it does, use narrow PowerShell file writes/replacements scoped to the intended file.
- Do not overwrite user changes casually. Check `git status --short` before larger edits.
- Generated outputs should go under `data/processed/`, `outputs/tables/`, `outputs/figures/`, or `reports/` as appropriate.



