"""Phase-0 source audit for the MySnow model-serving test run (design SS4C).

Mechanically derives the required parameter list from the v3 feature
builder's raw inputs and verifies each entry against the LIVE catalogues
of both serving sources:

* ECMWF IFS open data (0.25 deg, oper stream) -- the per-step .index file
  at data.ecmwf.int enumerates every GRIB message (param / level / type).
* NOAA HRRR on AWS Open Data (s3://noaa-hrrr-bdp-pds) -- the per-file
  .idx enumerates every GRIB message in wrfprs / wrfsfc.

Required raw inputs (from v3_features.build_event_v3_features and
gold_profiles.build_event_features):

profile levels (13 ERA5 levels after adapter normalisation):
  temperature, relative humidity, wind (u/v or speed), geopotential height
pl series: vertical velocity (w / VVEL), specific humidity (q / SPFH)
accumulations: total precip (tp / APCP), convective precip (cp / ACPCP),
  large-scale precip (lsp = tp - cp derivation sanctioned by design)
surface: 2 m temperature, 2 m dewpoint, 10 m u/v wind, skin temperature,
  total column liquid water (tclw)

For HRRR, tclw is not a native field: the design (SS4B) requires it to be
integrated from HRRR's own hydrometeor mixing ratios on pressure levels
(CLWMR + RWMR), so the audit checks for those fields.

Usage: python audit_serving_sources.py --date 20261031 --cycle 12 \
           --out audit.json
Exit code 0 always; gate outcomes are in the JSON (arm stops are data,
not crashes).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone

ERA5_LEVELS = [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500]

ECMWF_REQUIRED_PL = {"t": "temperature", "q": "specific humidity",
                     "u": "u wind", "v": "v wind", "w": "vertical velocity",
                     "z": "geopotential"}
ECMWF_REQUIRED_PL_OPTIONAL = {"r": "relative humidity (else derive from q)"}
ECMWF_REQUIRED_SFC = {"2t": "2 m temperature", "2d": "2 m dewpoint",
                      "10u": "10 m u wind", "10v": "10 m v wind",
                      "skt": "skin temperature", "tp": "total precipitation",
                      "sf": "snowfall", "cp": "convective precipitation",
                      "tclw": "total column liquid water"}
ECMWF_REQUIRED_SFC_OPTIONAL = {"lsp": "large-scale precipitation (else tp - cp)"}

HRRR_REQUIRED_PL = {"TMP": "temperature", "RH": "relative humidity",
                    "SPFH": "specific humidity", "VVEL": "vertical velocity",
                    "UGRD": "u wind", "VGRD": "v wind", "HGT": "geopotential height"}
HRRR_TCLW_FEED = {"CLMR": "cloud water mixing ratio (HRRR short name)",
                  "RWMR": "rain mixing ratio"}
HRRR_REQUIRED_SFC = {"TMP_2m": "2 m temperature", "DPT_2m": "2 m dewpoint",
                     "UGRD_10m": "10 m u wind", "VGRD_10m": "10 m v wind",
                     "TMP_surface": "surface (skin) temperature",
                     "APCP": "total precipitation accumulation"}


def fetch_text(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "mysnow-serving-audit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def audit_ecmwf(date: str, cycle: int) -> dict:
    base = f"https://data.ecmwf.int/forecasts/{date}/{cycle:02d}z/ifs/0p25/oper"
    out: dict = {"source": "ecmwf-open-data", "base": base, "steps_checked": {}}
    pl_params: dict[str, set] = {}
    sfc_params: set[str] = set()
    other_params: set[str] = set()
    for step in (24, 78):
        name = f"{date}{cycle:02d}0000-{step}h-oper-fc.grib2"
        try:
            text = fetch_text(f"{base}/{name[:-6]}.index")
        except Exception as exc:  # noqa: BLE001 - audit records failures as data
            out["steps_checked"][str(step)] = {"file": name, "error": str(exc)}
            continue
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        out["steps_checked"][str(step)] = {"file": name, "messages": len(entries)}
        if step == 24:
            for e in entries:
                param = e.get("param")
                tol = e.get("levtype") or e.get("typeOfLevel")
                if param is None:
                    continue
                if tol in ("pl", "isobaricInhPa"):
                    levels = e.get("levelist") or e.get("level") or []
                    if isinstance(levels, (int, float, str)):
                        levels = [levels]
                    pl_params.setdefault(param, set()).update(int(float(x)) for x in levels)
                elif tol in ("sfc", "surface", "heightAboveGround", "nominalTop", "meanSea", "depthBelowLandLayer", "snowLayer"):
                    sfc_params.add(param)
                else:
                    other_params.add(param)
    out["pl_params"] = {k: sorted(v) for k, v in sorted(pl_params.items())}
    out["sfc_params"] = sorted(sfc_params)
    missing_pl = [p for p in ECMWF_REQUIRED_PL if p not in pl_params]
    missing_sfc = [p for p in ECMWF_REQUIRED_SFC if p not in sfc_params]
    out["missing_required_pl"] = missing_pl
    out["missing_required_sfc"] = missing_sfc
    out["missing_optional"] = [p for p in ECMWF_REQUIRED_PL_OPTIONAL if p not in pl_params] + \
                              [p for p in ECMWF_REQUIRED_SFC_OPTIONAL if p not in sfc_params]
    out["gate"] = "PASS" if not missing_pl and not missing_sfc else "FAIL"
    return out


def parse_idx(text: str) -> list[dict]:
    """Parse a GRIB .idx: n:offset:d=...:FIELD:level:fcst:."""
    rows = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) >= 6 and parts[0].isdigit():
            rows.append({"field": parts[3], "level": parts[4], "ref": parts[5]})
    return rows


def audit_hrrr(date: str, cycle: int) -> dict:
    base = f"https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{date}/conus"
    out: dict = {"source": "hrrr-aws-open-data", "base": base, "files": {}}
    agg: dict[str, dict] = {}
    for fxx, kind in ((24, "wrfprs"), (24, "wrfsfc"), (48, "wrfprs"), (48, "wrfsfc")):
        name = f"hrrr.t{cycle:02d}z.{kind}f{fxx:02d}.grib2"
        try:
            text = fetch_text(f"{base}/{name}.idx")
        except Exception as exc:  # noqa: BLE001
            out["files"][name] = {"error": str(exc)}
            continue
        rows = parse_idx(text)
        out["files"][name] = {"messages": len(rows)}
        if fxx == 24:
            for r in rows:
                slot = agg.setdefault(r["field"], {"levels": set(), "kinds": set()})
                slot["levels"].add(r["level"])
                slot["kinds"].add(kind)
    def has(field: str) -> bool:
        return field in agg

    pl_fields = {}
    for field, desc in HRRR_REQUIRED_PL.items():
        pl_fields[field] = {"desc": desc, "present": has(field),
                            "levels_sample": sorted(agg[field]["levels"])[:8] if has(field) else []}
    tclw_feed = {f: has(f) for f in HRRR_TCLW_FEED}
    sfc_rows = []
    try:
        sfc_rows = parse_idx(fetch_text(f"{base}/hrrr.t{cycle:02d}z.wrfsfcf24.grib2.idx"))
    except Exception:  # noqa: BLE001
        pass
    def sfc_has(field: str, level_sub: str) -> bool:
        return any(r["field"] == field and level_sub in r["level"] for r in sfc_rows)
    sfc = {
        "TMP_2m": sfc_has("TMP", "2 m above ground"),
        "DPT_2m": sfc_has("DPT", "2 m above ground"),
        "RH_2m": sfc_has("RH", "2 m above ground"),
        "SPFH_2m": sfc_has("SPFH", "2 m above ground"),
        "UGRD_10m": sfc_has("UGRD", "10 m above ground"),
        "VGRD_10m": sfc_has("VGRD", "10 m above ground"),
        "TMP_surface": sfc_has("TMP", "surface"),
        "APCP": any(r["field"] == "APCP" for r in sfc_rows),
        "ACPCP": any(r["field"] == "ACPCP" for r in sfc_rows),
        "ASNOW": any(r["field"] == "ASNOW" for r in sfc_rows),
        "WEASD": any(r["field"] == "WEASD" for r in sfc_rows),
    }
    out["pl_required"] = pl_fields
    out["tclw_feed_fields"] = tclw_feed
    out["sfc_checks"] = sfc
    out["all_fields_wrfprs_f24"] = sorted(f for f, v in agg.items() if "wrfprs" in v["kinds"])
    missing = [f for f in HRRR_REQUIRED_PL if not has(f)]
    if not all(tclw_feed.values()):
        missing.append("tclw-feed(CLMR/RWMR)")
    for key in ("TMP_2m", "UGRD_10m", "VGRD_10m", "TMP_surface", "APCP"):
        if not sfc.get(key):
            missing.append(f"sfc:{key}")
    if not (sfc.get("DPT_2m") or sfc.get("RH_2m") or sfc.get("SPFH_2m")):
        missing.append("sfc:2m-moisture")
    out["missing"] = missing
    out["gate"] = "PASS" if not missing else "FAIL"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261031")
    ap.add_argument("--cycle", type=int, default=12)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "date": args.date, "cycle_z": args.cycle,
        "era5_levels_required_after_normalisation": ERA5_LEVELS,
        "ecmwf": audit_ecmwf(args.date, args.cycle),
        "hrrr": audit_hrrr(args.date, args.cycle),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, default=str)
    print(json.dumps({"ecmwf_gate": result["ecmwf"]["gate"],
                      "ecmwf_missing_pl": result["ecmwf"]["missing_required_pl"],
                      "ecmwf_missing_sfc": result["ecmwf"]["missing_required_sfc"],
                      "hrrr_gate": result["hrrr"]["gate"],
                      "hrrr_missing": result["hrrr"]["missing"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
