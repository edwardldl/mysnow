"""Queue item 4 — pairing verification on real windows (one-off driver).

Builds the feature row for Alta pair alta-cln-20020107
(2002-01-06 11:00 UTC -> 2002-01-07 11:00 UTC) from:
  * pressure levels: the one completed CDS file (Jan 5-6 2002) for
    Jan 5-6 hours, NCAR S3 range reads for Jan 7 hours;
  * single-level + accumulations: NCAR (gold reader machinery).
Also cross-checks NCAR vs CDS pressure-level values on the overlap
day (2002-01-06) — the two routes carry the same ERA5 fields, so
they must agree to float precision.

This is a verification driver, not the production fetcher; the
production paths are alpine_profiles.fetch_source (CDS) and
fetch_source_ncar (NCAR).
"""

from __future__ import annotations

import glob
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slr_calibration.alpine_profiles import (  # noqa: E402
    NCAR_SFC,
    _ncar_reader,
    _read_sfc_month,
    assemble_series,
    load_unit_variables,
    PL_SHORT,
    slice_series,
)
from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA  # noqa: E402
from slr_calibration.gold_profiles_era5 import (  # noqa: E402
    accumulation_init,
    hourly_accumulation,
)

UTC = timezone.utc
STATION = ("ALTA-CLN", 40.57607, -111.63889)
BENCH = Path("data/slr-training-data/screened-v2/model-v1/model-v3-alpine-benchmark")


def main() -> int:
    reader = _ncar_reader()
    days = [date(2002, 1, 5), date(2002, 1, 6), date(2002, 1, 7)]
    instants = [datetime(2002, 1, 5, tzinfo=UTC) + timedelta(hours=h)
                for h in range(72)]

    # --- NCAR pressure levels for Jan 6 (equivalence) + Jan 7 (coverage)
    tasks = [(d, v) for d in (date(2002, 1, 6), date(2002, 1, 7))
             for v in ("t", "r", "u", "v", "z", "w", "q")]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(
            lambda a: reader.read_pl_day(a[0], a[1], [STATION]), tasks))
    ncar_pl: dict[tuple[datetime, str], dict[int, float | None]] = {}
    for (d, var), out in zip(tasks, results):
        for (_sid, instant), level_map in out.items():
            ncar_pl[(instant, var)] = level_map

    # --- CDS pressure levels for Jan 5-6 from the completed file
    cds_file = glob.glob(str(BENCH / "cds_cache" / "*_pl.zip"))[0]
    cds_vars = load_unit_variables(Path(cds_file), set(PL_SHORT.values()))
    from slr_calibration.alpine_profiles import _VarView
    views = {short: _VarView(cds_vars[short], STATION[1], STATION[2])
             for short in PL_SHORT.values()}

    def cds_level(instant: datetime, short: str, level: int) -> float | None:
        return views[short].at(instant, level)

    # --- equivalence on Jan 6 (all levels, all 7 vars, a few hours)
    worst: dict[str, float] = {}
    for hour in (0, 6, 12, 18):
        instant = datetime(2002, 1, 6, hour, tzinfo=UTC)
        for var, short in (("t", "t"), ("r", "r"), ("u", "u"), ("v", "v"),
                           ("z", "z"), ("w", "w"), ("q", "q")):
            for level in PRESSURE_LEVELS_HPA:
                a = cds_level(instant, short, level)
                b = ncar_pl[(instant, var)][level]
                if a is None or b is None:
                    continue
                worst[var] = max(worst.get(var, 0.0), abs(a - b))
    print("equivalence NCAR vs CDS (max abs diff, Jan 6):",
          {k: round(v, 6) for k, v in sorted(worst.items())}, flush=True)

    # --- NCAR single levels + accumulations, Jan 5-7
    t2m = reader.read_2t(instants, [STATION])
    sfc = {short: _read_sfc_month(reader, code, token, ds, instants, [STATION])
           for short, (code, token, ds) in NCAR_SFC.items()}
    requests = []
    for instant in instants:
        init, fh = accumulation_init(instant)
        requests.append((init, fh, STATION[0], STATION[1], STATION[2]))
        if fh > 1:
            requests.append((init, fh - 1, STATION[0], STATION[1], STATION[2]))
    cumulative = {var: reader.read_accumu_cumulative(var, requests)
                  for var in ("lsp", "cp", "sf")}

    # --- raw records
    hours: dict[str, dict] = {}
    for instant in instants:
        levels: dict[str, list] = {}
        for level in PRESSURE_LEVELS_HPA:
            if instant < datetime(2002, 1, 7, tzinfo=UTC):
                t_k = cds_level(instant, "t", level)
                rh = cds_level(instant, "r", level)
                u = cds_level(instant, "u", level)
                v = cds_level(instant, "v", level)
                z = cds_level(instant, "z", level)
                w = cds_level(instant, "w", level)
                q = cds_level(instant, "q", level)
            else:
                t_k = ncar_pl[(instant, "t")][level]
                rh = ncar_pl[(instant, "r")][level]
                u = ncar_pl[(instant, "u")][level]
                v = ncar_pl[(instant, "v")][level]
                z = ncar_pl[(instant, "z")][level]
                w = ncar_pl[(instant, "w")][level]
                q = ncar_pl[(instant, "q")][level]
            levels[str(level)] = [
                None if t_k is None else t_k - 273.15, rh,
                None if u is None or v is None else math.hypot(u, v),
                None if z is None else z / 9.80665, w, q]
        amounts = {}
        for var in ("lsp", "cp", "sf"):
            amt = hourly_accumulation(cumulative[var], STATION[0], instant)
            amounts[var] = None if amt is None else amt * 1000.0
        t2m_k = t2m.get((STATION[0], instant))
        hours[instant.isoformat()] = {
            "levels": levels,
            "t2m_c": None if t2m_k is None else t2m_k - 273.15,
            "d2m_k": sfc["d2m"].get((STATION[0], instant)),
            "tclw": sfc["tclw"].get((STATION[0], instant)),
            "u10": sfc["u10"].get((STATION[0], instant)),
            "v10": sfc["v10"].get((STATION[0], instant)),
            "skt_k": sfc["skt"].get((STATION[0], instant)),
            "lsp_mm": amounts["lsp"], "cp_mm": amounts["cp"],
            "sf_mm": amounts["sf"],
        }

    series = assemble_series(hours)
    start = datetime(2002, 1, 6, 11, tzinfo=UTC)
    end = datetime(2002, 1, 7, 11, tzinfo=UTC)
    sliced = slice_series(series, start, end)

    from slr_calibration.model_serving import build_point_window_features
    point = {
        "point_id": "alta-cln", "resort_name": "Alta-Collins (CLN)",
        "point_name": "benchmark", "state": "UT",
        "timezone": "America/Denver",
        "climate_regime": "intermountain_west", "region": "rockies",
        "latitude": STATION[1], "longitude": STATION[2],
        "elevation_m": 2945.0,
    }
    row, status, counts = build_point_window_features(
        point, end, sliced["profile"], sliced["w"], sliced["q"],
        sliced["accumu"], sliced["sfc"], "era5_mixed_verification")
    print("profile_status:", status)
    print("valid_hours:", counts["valid_hours"],
          "source_resolved:", counts["source_resolved_counts"])
    print("baseline_slr (Kuchera):", row["baseline_slr"])
    # profile sanity: 700 hPa height/temperature near the station
    prof = sliced["profile"][datetime(2002, 1, 7, 0, tzinfo=UTC)]
    lvl = {p: (t, r, w_, z) for p, t, r, w_, z in prof["levels"]}
    print("2002-01-07 00Z levels: 850 hPa z/T:", lvl[850][3], lvl[850][0],
          "| 700 hPa z/T:", lvl[700][3], lvl[700][0],
          "| 500 hPa z/T:", lvl[500][3], lvl[500][0])

    from slr_calibration.champion import ChampionModel
    from slr_calibration.model_serving import predict_with_passthrough
    import pandas as pd
    v1 = Path("data/slr-training-data/screened-v2/model-v1")
    for name, mdir in (("champion", v1 / "model-v3" / "champion"),
                       ("wall", v1 / "model-v3-western" / "candidate"),
                       ("w1500", v1 / "model-v3-western" / "candidate-1500")):
        model = ChampionModel.load(mdir)
        feats = list(model.sidecar["features"])
        frame = pd.DataFrame([{c: row.get(c) for c in feats}])
        frame["region"] = "rockies"
        frame["site_id"] = STATION[0]
        pred = float(predict_with_passthrough(model, frame)[0])
        print(f"{name} prediction: {pred:.4f} (observed SLR 4.8077)")
    print("features finite:", sum(1 for c in ChampionModel.load(
        v1 / 'model-v3' / 'champion').sidecar['features']
        if row.get(c) is not None and math.isfinite(row[c])), "of 64")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
