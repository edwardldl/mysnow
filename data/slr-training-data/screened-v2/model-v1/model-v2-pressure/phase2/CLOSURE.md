# Phase 2 — CLOSURE

Recorded 2026-10-29 (PDT).

**Phase 2 is cancelled by the user, before any Phase 2 build or judging
compute started.** Specifically dropped from scope:

- **Step 2 — the P-surf variant** (surface-relative feature builder, its
  judging run, and the P-geometry fallback). No P-surf code was ever
  written into this repository; preliminary drafts existed only outside
  the workspace and were discarded.
- **Step 3 — the elevation-cleanup ablation** (the 500 m drop variant and
  the conditional repair variant). Never built or judged.

**Reason / new direction:** the user chose instead to pursue a
new-feature fetch — adding vertical velocity (w), cloud liquid water
content (clwc) and precipitation accumulations to the feature set. That
work is tracked separately and is not part of this closure.

**What remains as the Phase 2 record:** the Step 1 diagnostics in this
directory (`diagnostics.md`, `valid_levels_by_region.csv`,
`validity_share_by_level_region.csv`, `loro_delta_by_region.csv`,
`elevation_suspects.csv`, `diag_numbers.json`; commit `2d0053b`). Their
findings stand as analysis only: H1 (filled below-ground levels) fits the
Rockies/Sierra LORO regressions but not Alaska/PNW; the 500 m
elevation-suspect set (33 stations, 1,273 events) mixes provable metadata
errors with steep-terrain grid-orography mismatches. Neither hypothesis
was tested by a model run, and no conclusion about them should be drawn
beyond what the diagnostics show.

**Related completion, for the record:** the pressure-level judging that
Phase 2 was queued behind ran to completion (P-core, P-mask, no-time and
obs-hour variants plus the final report, `PRESSURE_MODEL_REPORT.md` /
`verdict_pressure.json` in the parent directory). Mechanical verdict:
**NOT ADOPTED — the AGL ENS-EB champion stands** (Route A failed on the
LORO mean; Route B failed in both Alaska and PNW). Per the user's
direction, the visual bias-comparison artifact
(`ens-eb-champion-bias-comparison-report`) was updated with the pressure
results and the verdict. Production calibration gates were never touched
and remain off.
