# ConstructIQ Material Estimation

This module will estimate construction material quantities and latest available
online prices for an existing ConstructIQ project.

Planned flow:

1. Read the validated project layout.
2. Collect location, currency, brand, purchase type, and delivery preferences.
3. Calculate quantities with deterministic formulas.
4. Use SerpApi for structured product prices.
5. Use Tavily to verify supplier pages and source details.
6. Return quantities, unit prices, sources, dates, and totals.

The estimator is additive. It does not modify the chat, layout solver, renderer,
or 3D viewer.

## Initial structure

- `schemas.py` — estimation inputs and result models.
- `quantity_calculator.py` — deterministic quantity formulas.
- `price_search.py` — backend price-provider interface.
- `service.py` — orchestration for one project estimate.

API keys will be read from the backend environment and must never be sent to
the browser.

## CLI test

Run the estimator against a saved project:

```powershell
./.venv/Scripts/python.exe -m estimation santhosh-home-3d35ab05df --location Hyderabad
```

Optional flags include `--currency USD`, repeated `--brand UltraTech`,
`--wholesale`, and `--include-delivery`. The quantity output currently covers
bricks/blocks, masonry cement, and steel reinforcement bars. Steel is a
preliminary kg-per-square-foot allowance; exact reinforcement needs structural
drawings. Price providers are added separately.
