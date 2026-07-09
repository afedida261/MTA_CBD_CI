# Congestion Pricing and NYC Bus Speeds

This project estimates the effect of Manhattan congestion pricing on NYC MTA bus speeds. The main design is a Difference-in-Differences comparison of CBD-exposed routes against non-CBD NYC routes.

## Current Design

- Policy date: January 5, 2025.
- Monthly post period starts in January 2025.
- Analysis window: August 2023 through May 2026.
- Main sample: weekday, non-Staten-Island NYC bus routes.
- Main treatment: `cbd_route = True` when any retained route shape intersects the CBD geofence.
- Main model: average speed on `did = cbd_route * post`, with route, month, and period fixed effects and route-clustered standard errors.

The current headline estimate is about +0.156 mph for CBD-exposed routes relative to non-CBD NYC routes.

## Key Files

- `src/build_geojson_cbd_treatment.py`: builds the GeoJSON-first NYC treatment and DiD panel.
- `src/run_nyc_robustness_checks.py`: runs threshold, sample, official-source, and Boston robustness checks.
- `src/build_boston_bus_speed_panel.py`: builds the processed Boston monthly bus-speed panel.
- `src/build_ntd_synthetic_control.py`: builds the NTD agency-level synthetic-control robustness exercise.
- `notebooks/geojson_cbd_did.ipynb`: main NYC DiD notebook.
- `notebooks/nyc_robustness_and_boston_comparison.ipynb`: threshold robustness and Boston external-control checks.
- `notebooks/nyc_cbd_ntd_synthetic_control.ipynb`: NTD synthetic-control robustness notebook.
- `reports/congestion_pricing_bus_speed_report.tex`: concise LaTeX report.
- `reports/congestion_pricing_bus_speed_report.pdf`: compiled report.

## Main Data Outputs

- `data/processed/nyc_did_panel_geojson_intersection.csv`: main NYC DiD panel.
- `data/processed/nyc_route_treatment_geojson_intersection.csv`: route-level treatment and CBD exposure metrics.
- `data/processed/boston_bus_speeds_monthly.csv`: processed Boston bus-speed panel.
- `data/processed/ntd_monthly_bus_speeds.csv`: NTD agency-month bus-speed donor panel.

## Main Result Tables

- `outputs/tables/nyc_treatment_definition_robustness.csv`
- `outputs/tables/nyc_vs_boston_external_control_robustness.csv`
- `outputs/tables/linear_pretrend_tests.csv`
- `outputs/tables/nyc_cbd_ntd_synthetic_control_summary.csv`
- `outputs/tables/nyc_cbd_ntd_synthetic_control_weights.csv`

## Remaining Planned Work

From `todo.txt`, the main remaining items are:

- Expand event-study / pretrend diagnostics and summarize whether NYC parallel trends are credible.
- Treat Boston as an external robustness benchmark unless a defensible comparison design is established.
- Keep threshold-based CBD treatment definitions as robustness checks, not the main treatment.
- Interpret the NTD synthetic-control exercise as descriptive support rather than a replacement for the route-level NYC DiD.

## Reproducing Core Outputs

Use the project virtual environment when possible:

```powershell
.venv\Scripts\python.exe src\build_geojson_cbd_treatment.py
.venv\Scripts\python.exe src\run_nyc_robustness_checks.py
.venv\Scripts\python.exe src\build_boston_bus_speed_panel.py
.venv\Scripts\python.exe src\build_ntd_synthetic_control.py
```

Compile the report with:

```powershell
cd reports
pdflatex.exe -interaction=nonstopmode -halt-on-error congestion_pricing_bus_speed_report.tex
```
