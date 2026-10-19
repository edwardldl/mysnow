"""Station -> range -> climate-regime -> training-region assignments.

Design reference: mysnow-regional-slr-predictor-design.md sections 4.1-4.4.

The regime map is *data with an auditable rule*, not a model: every SNOTEL
station in ``data/hrrr-dataset/stations.csv`` is assigned a named mountain
range, one of the three design regimes (continental_mountain /
intermountain / maritime_mountain), and a candidate training region by
deterministic rules over station state + coordinates.  The materialized
map lives at ``training/slr_calibration/regions/station_regimes.csv`` with
an ``assignment_basis`` column recording which rule fired; regenerate it
with :func:`write_station_regimes` whenever the station list changes.  The
adapter refuses to run when a station has no assignment.

Regions are the state footprints of design section 4.3.  Two judgment calls
the design left open, recorded here and in regions/README.md:

* Arizona is not listed in section 4.2; it is assigned ``intermountain``
  (interior Southwest sky islands) and trains in ``southwest`` with New
  Mexico.
* California's far-northeast Warner Mountains stations are
  ``intermountain`` by range, but still train in ``sierra-nevada`` because
  section 4.3 fixes that region as the California Sierra footprint.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

CONTINENTAL = "continental_mountain"
INTERMOUNTAIN = "intermountain"
MARITIME = "maritime_mountain"
UNCLASSIFIED = "unclassified"

MERGED_CASCADES_ID = "cascades-merged"

# Candidate training regions (design section 4.3): state footprints.
REGION_DEFS: dict[str, dict[str, Any]] = {
    "colorado-ranges": {"label": "Colorado Ranges", "states": ("CO",)},
    "utah-wasatch-uinta": {"label": "Utah (Wasatch-Uinta)", "states": ("UT",)},
    "wyoming-ranges": {"label": "Wyoming Ranges", "states": ("WY",)},
    "washington-cascades": {"label": "Washington Cascades", "states": ("WA",)},
    "idaho-ranges": {"label": "Idaho Ranges", "states": ("ID",)},
    "montana-ranges": {"label": "Montana Ranges", "states": ("MT",)},
    "oregon-cascades": {"label": "Oregon Cascades", "states": ("OR",)},
    "sierra-nevada": {"label": "Sierra Nevada", "states": ("CA",)},
    "southwest": {"label": "Southwest (NM + AZ)", "states": ("NM", "AZ")},
    "nevada": {"label": "Nevada Ranges", "states": ("NV",)},
}

_STATE_REGION: dict[str, str] = {
    state: region_id
    for region_id, definition in REGION_DEFS.items()
    for state in definition["states"]
}

# Default regime per state before range-level coordinate rules apply.
_STATE_DEFAULT_REGIME: dict[str, str] = {
    "CO": CONTINENTAL, "WY": CONTINENTAL, "MT": CONTINENTAL, "NM": CONTINENTAL,
    "UT": INTERMOUNTAIN, "ID": INTERMOUNTAIN, "NV": INTERMOUNTAIN, "AZ": INTERMOUNTAIN,
    "WA": MARITIME, "OR": MARITIME, "CA": MARITIME,
    "SD": CONTINENTAL,
    "AK": UNCLASSIFIED,
}

_SINGLE_RANGE: dict[str, str] = {
    "CO": "Colorado Rockies",
    "UT": "Wasatch-Uinta and Utah plateaus",
    "WY": "Wyoming Rockies",
    "MT": "Montana Rockies",
    "ID": "Idaho Batholith and ranges",
    "NV": "Great Basin ranges",
    "NM": "New Mexico ranges",
    "AZ": "Arizona sky islands",
    "SD": "Black Hills",
    "AK": "Alaska ranges",
}

REGIME_FIELDS = [
    "station_triplet", "station_name", "state", "latitude", "longitude",
    "range_name", "climate_regime", "region_id", "assignment_basis",
]


def region_for_state(state: str) -> str | None:
    """Training region for a state footprint, or None (SD, AK, unknown)."""
    return _STATE_REGION.get(state.strip().upper())


def _range_and_regime(state: str, latitude: float, longitude: float) -> tuple[str, str, str]:
    """Return (range_name, climate_regime, assignment_basis) for one station."""
    if state == "WA":
        if longitude <= -123.0:
            return "Olympic Mountains", MARITIME, "coordinate rule: Olympic Peninsula west of -123.0"
        if longitude > -119.0:
            if latitude >= 45.9 and longitude >= -117.9:
                return "Blue Mountains (WA)", INTERMOUNTAIN, "coordinate rule: far southeast WA (Blue Mountains)"
            return "Kettle-Selkirk-Okanogan", INTERMOUNTAIN, "coordinate rule: WA east of the Cascade crest (lon > -119.0)"
        return "Cascade Range (WA)", MARITIME, "state default: Cascade Range (WA)"
    if state == "OR":
        if longitude >= -118.6:
            return "Wallowa-Blue Mountains", INTERMOUNTAIN, "coordinate rule: OR east (lon >= -118.6)"
        if latitude < 43.2:
            if longitude <= -122.8:
                return "Siskiyou Mountains", MARITIME, "coordinate rule: southwest OR (Siskiyou)"
            return "Southern Oregon Cascades", MARITIME, "coordinate rule: southern OR Cascades"
        return "Cascade Range (OR)", MARITIME, "state default: Cascade Range (OR)"
    if state == "CA":
        if latitude >= 41.3:
            return "Warner Mountains", INTERMOUNTAIN, "coordinate rule: far northeast CA (Warner Mountains); region stays sierra-nevada per design 4.3"
        return "Sierra Nevada", MARITIME, "state default: Sierra Nevada"
    range_name = _SINGLE_RANGE.get(state)
    if range_name is not None:
        return range_name, _STATE_DEFAULT_REGIME[state], f"state default: {range_name}"
    return "Unassigned", UNCLASSIFIED, "no rule for state"


def assign_station(state: str, latitude: float, longitude: float) -> dict[str, Any]:
    state = state.strip().upper()
    range_name, regime, basis = _range_and_regime(state, latitude, longitude)
    return {
        "range_name": range_name,
        "climate_regime": regime,
        "region_id": region_for_state(state),
        "assignment_basis": basis,
    }


def build_station_regime_rows(stations_path: Path) -> list[dict[str, Any]]:
    with stations_path.open(newline="", encoding="utf-8") as handle:
        stations = list(csv.DictReader(handle))
    rows: list[dict[str, Any]] = []
    for station in stations:
        assignment = assign_station(
            str(station["state"]),
            float(station["latitude"]),
            float(station["longitude"]),
        )
        rows.append({
            "station_triplet": str(station["station_triplet"]),
            "station_name": str(station.get("station_name") or ""),
            "state": str(station["state"]),
            "latitude": float(station["latitude"]),
            "longitude": float(station["longitude"]),
            **assignment,
        })
    rows.sort(key=lambda row: row["station_triplet"])
    return rows


def write_station_regimes(stations_path: Path, output_path: Path) -> list[dict[str, Any]]:
    rows = build_station_regime_rows(stations_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGIME_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "region_id": row["region_id"] or ""})
    return rows


def load_station_regimes(path: Path) -> dict[str, dict[str, Any]]:
    """Load the materialized regime map: triplet -> assignment dict."""
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {
        str(row["station_triplet"]): {
            "range_name": str(row["range_name"]),
            "climate_regime": str(row["climate_regime"]),
            "region_id": str(row["region_id"]) or None,
        }
        for row in rows
    }
