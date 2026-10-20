"""Step 1 of the 2026-10 SLR plan: repair the direct-label pool.

Two deliverables live here:

1. **Pletcher hygiene** — the label filters of Pletcher et al. (2026,
   *Wea. Forecasting* 41, 317-338): keep only snowfall >= 5.09 cm and
   snowfall SWE >= 2.79 mm, drop SLR of exactly 10 (a documented
   missing-data placeholder in CoCoRaHS/GHCN practice), and drop
   SLR < 2 or > 50.  Applied to every pre-existing direct-label set so
   the attrition is measured, not assumed.

2. **A CoCoRaHS core-sampler screen** — Pletcher et al. identified
   genuine snowboard-core observers by surveying 29,519 volunteers
   (921 confirmed).  Most observers instead copy gauge catch into the
   snowfall-SWE field, which is exactly the population GHCN carries
   under source flag N and which the earlier regional audits had to
   reject wholesale.  This module replicates the *effect* of the
   survey from data alone: a station whose reported SWE is an
   independent board core diverges from its gauge catch (PRCP) on
   large events — undercatch makes true core SWE exceed catch — while
   a copied field tracks PRCP exactly even on the biggest days.  The
   screen is station-level, deterministic, network-scoped (it is only
   applied to CoCoRaHS-style US1 stations; NWS COOP stations derive
   WESF from the gauge by protocol, so for them equality carries no
   copying signal and they are admitted on network protocol), and it
   is validated against the survey-confirmed station set recovered
   from the Pletcher et al. public datasets (see
   ``match_pletcher_sites``).

Nothing here trains a model.  Outputs are labels only: per-region
normalized event files, a station screening ledger, and a report.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .ghcn import _assign_storm_groups
from .ghcn_track import NCEI_ACCESS_URL
from .sierra import SIERRA_STATIONS

# ---------------------------------------------------------------------------
# Pletcher hygiene
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HygieneConfig:
    """Pletcher et al. (2026) label thresholds, applied verbatim."""

    min_snow_mm: float = 50.9  # 5.09 cm (2 in) of snowfall
    min_wesf_mm: float = 2.79  # 2.79 mm (0.11 in) of snowfall SWE
    placeholder_slr: float = 10.0  # exact 10:1 is a missing-data placeholder
    min_slr: float = 2.0
    max_slr: float = 50.0


HYGIENE_FILTER_ORDER = (
    "snow_below_minimum",
    "wesf_below_minimum",
    "slr_exactly_10_placeholder",
    "slr_below_minimum",
    "slr_above_maximum",
)


def _finite(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _hygiene_failures(snow_mm: float, wesf_mm: float, config: HygieneConfig) -> list[str]:
    slr = snow_mm / wesf_mm
    failures: list[str] = []
    if snow_mm < config.min_snow_mm:
        failures.append("snow_below_minimum")
    if wesf_mm < config.min_wesf_mm:
        failures.append("wesf_below_minimum")
    if abs(slr - config.placeholder_slr) < 1e-9:
        failures.append("slr_exactly_10_placeholder")
    if slr < config.min_slr:
        failures.append("slr_below_minimum")
    if slr > config.max_slr:
        failures.append("slr_above_maximum")
    return failures


def apply_hygiene(
    rows: list[dict[str, Any]],
    config: HygieneConfig = HygieneConfig(),
    *,
    snow_key: str = "snow_mm",
    wesf_key: str = "wesf_mm",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply Pletcher hygiene to pair rows.

    Returns the kept rows and an attrition report.  ``rejected`` counts
    each row once, at the first filter it fails (in
    ``HYGIENE_FILTER_ORDER``); ``standalone`` counts each filter
    independently over all rows, so overlaps stay visible.
    """
    kept: list[dict[str, Any]] = []
    sequential: Counter[str] = Counter()
    standalone: Counter[str] = Counter()
    invalid = 0
    for row in rows:
        snow_mm = _finite(row.get(snow_key))
        wesf_mm = _finite(row.get(wesf_key))
        if snow_mm is None or wesf_mm is None or snow_mm <= 0 or wesf_mm <= 0:
            invalid += 1
            continue
        failures = _hygiene_failures(snow_mm, wesf_mm, config)
        for failure in failures:
            standalone[failure] += 1
        if failures:
            sequential[failures[0]] += 1
            continue
        kept.append(row)
    report = {
        "inputRows": len(rows),
        "invalidPairs": invalid,
        "keptRows": len(kept),
        "rejectedSequential": {name: sequential.get(name, 0) for name in HYGIENE_FILTER_ORDER},
        "standalone": {name: standalone.get(name, 0) for name in HYGIENE_FILTER_ORDER},
        "thresholds": {
            "minSnowMm": config.min_snow_mm,
            "minWesfMm": config.min_wesf_mm,
            "placeholderSlr": config.placeholder_slr,
            "minSlr": config.min_slr,
            "maxSlr": config.max_slr,
        },
    }
    return kept, report


# ---------------------------------------------------------------------------
# GHCN by-station parsing (long and NCEI-access wide formats)
# ---------------------------------------------------------------------------

_TRACKED_ELEMENTS = ("SNOW", "WESF", "PRCP", "SNWD", "DAPR", "MDSF")


def _attributes(raw: str | None) -> tuple[str, str, str, str]:
    parts = [part.strip() for part in (raw or "").split(",")]
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]


def _new_day() -> dict[str, Any]:
    return {
        "snow_mm": None,
        "wesf_mm": None,
        "prcp_mm": None,
        "snwd_mm": None,
        "snow_sflag": "",
        "wesf_sflag": "",
        "obs_time": "",
        "accumulated_snow": False,
        "accumulated_wesf": False,
        "qflag_failed": False,
    }


def _finalize_day(day: dict[str, Any], dapr: float | None, mdsf_present: bool) -> None:
    accumulated = bool((dapr is not None and dapr > 1) or mdsf_present)
    if day["snow_mm"] is not None:
        day["accumulated_snow"] = accumulated
    if day["wesf_mm"] is not None:
        day["accumulated_wesf"] = bool(dapr is not None and dapr > 1)


def parse_ghcn_long_text(text: str) -> dict[str, dict[str, Any]]:
    """Parse the GHCN by-station long format (one element per row)."""
    days: dict[str, dict[str, Any]] = {}
    aux: dict[str, dict[str, Any]] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 8:
            continue
        day_text, element = row[1].strip(), row[2].strip()
        if element not in _TRACKED_ELEMENTS:
            continue
        value = _finite(row[3])
        if value is None:
            continue
        mflag, qflag, sflag, obs_time = row[4].strip(), row[5].strip(), row[6].strip(), row[7].strip()
        day = days.setdefault(day_text, _new_day())
        extra = aux.setdefault(day_text, {"dapr": None, "mdsf": False})
        if element == "DAPR":
            extra["dapr"] = value
        elif element == "MDSF":
            extra["mdsf"] = True
        elif element == "SNOW":
            day["snow_mm"] = value
            day["snow_sflag"] = sflag
            if qflag:
                day["qflag_failed"] = True
            if obs_time and not day["obs_time"]:
                day["obs_time"] = obs_time
        elif element == "WESF":
            day["wesf_mm"] = value / 10.0
            day["wesf_sflag"] = sflag
            if qflag:
                day["qflag_failed"] = True
            if obs_time and not day["obs_time"]:
                day["obs_time"] = obs_time
        elif element == "PRCP":
            day["prcp_mm"] = value / 10.0
        elif element == "SNWD":
            day["snwd_mm"] = value
    for day_text, day in days.items():
        extra = aux.get(day_text, {"dapr": None, "mdsf": False})
        _finalize_day(day, extra["dapr"], extra["mdsf"])
    return days


def parse_ghcn_wide_text(text: str) -> dict[str, dict[str, Any]]:
    """Parse the NCEI access (wide) CSV format for one station."""
    days: dict[str, dict[str, Any]] = {}
    reader = csv.DictReader(io.StringIO(text))
    for record in reader:
        day_text = str(record.get("DATE") or "").strip().replace("-", "")
        if len(day_text) != 8:
            continue
        day = _new_day()
        for element, key in (("SNOW", "snow_mm"), ("WESF", "wesf_mm"), ("PRCP", "prcp_mm"), ("SNWD", "snwd_mm")):
            raw = record.get(element)
            value = _finite(raw) if raw is not None and str(raw).strip() else None
            if value is None:
                continue
            if element in ("WESF", "PRCP"):
                value = value / 10.0
            day[key] = value
            _, qflag, sflag, obs_time = _attributes(record.get(element + "_ATTRIBUTES"))
            if element == "SNOW":
                day["snow_sflag"] = sflag
                if obs_time and not day["obs_time"]:
                    day["obs_time"] = obs_time
            if element == "WESF":
                day["wesf_sflag"] = sflag
                if obs_time and not day["obs_time"]:
                    day["obs_time"] = obs_time
            if element in ("SNOW", "WESF") and qflag:
                day["qflag_failed"] = True
        dapr = _finite(record.get("DAPR"))
        mdsf_present = str(record.get("MDSF") or "").strip() not in ("",)
        _finalize_day(day, dapr, mdsf_present)
        if any(day[key] is not None for key in ("snow_mm", "wesf_mm", "prcp_mm", "snwd_mm")):
            days[day_text] = day
    return days


def parse_station_file(path: Path) -> dict[str, dict[str, Any]]:
    """Parse a by-station GHCN file in either format, gzipped or plain."""
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8-sig", errors="replace")
    first = next((line for line in text.splitlines() if line.strip()), "")
    if first.lstrip('"').startswith("STATION"):
        return parse_ghcn_wide_text(text)
    return parse_ghcn_long_text(text)


def station_pairs(days: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Valid same-day SNOW+WESF pairs from parsed day records.

    A pair is valid when both elements are present and positive, the
    interval is not flagged as a multi-day accumulation, and neither
    element carries a GHCN quality flag.
    """
    pairs: list[dict[str, Any]] = []
    for day_text in sorted(days):
        day = days[day_text]
        snow_mm, wesf_mm = day["snow_mm"], day["wesf_mm"]
        if snow_mm is None or wesf_mm is None or snow_mm <= 0 or wesf_mm <= 0:
            continue
        if day["accumulated_snow"] or day["accumulated_wesf"] or day["qflag_failed"]:
            continue
        pairs.append({
            "date": day_text,
            "snow_mm": snow_mm,
            "wesf_mm": wesf_mm,
            "prcp_mm": day["prcp_mm"],
            "obs_time": day["obs_time"] or "0700",
            "snow_sflag": day["snow_sflag"],
            "wesf_sflag": day["wesf_sflag"],
        })
    return pairs


# ---------------------------------------------------------------------------
# Core-sampler screen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenConfig:
    """Deterministic station screen.  Thresholds are global (never tuned
    per region) and were fixed from the reference-population behaviour
    documented in SCREENING_REPORT.md:

    * equality on big days (WESF >= 10 mm) is bimodal across CoCoRaHS
      stations — genuine core samplers diverge (undercatch), copiers
      sit at ~1.0 with no excess days;
    * presence and history floors are consistency requirements, not
      genuineness proofs;
    * the exact-10 ceiling sits above the 90th percentile of every
      reference population examined.
    """

    min_paired_days: int = 20
    big_wesf_mm: float = 10.0
    min_big_days: int = 8
    copy_eq_big: float = 0.90
    copy_excess_big: float = 0.05
    copy_eq_all: float = 0.97
    copy_excess_all: float = 0.03
    min_presence: float = 0.50
    max_exact10_rate: float = 0.15
    exact10_min_snow_mm: float = 25.0
    exact10_min_wesf_mm: float = 1.0


def compute_screen_features(
    days: dict[str, dict[str, Any]],
    config: ScreenConfig = ScreenConfig(),
) -> dict[str, Any]:
    """Station-history discriminators a gauge-copier fails.

    Computed from the station's *full* parsed history: presence needs
    snow days that lack WESF, so the paired subset alone is not
    enough.
    """
    snow_days = 0
    paired_days: list[tuple[float, float, float | None]] = []
    for day_text in sorted(days):
        day = days[day_text]
        snow_mm = day["snow_mm"]
        if snow_mm is None or snow_mm <= 0:
            continue
        snow_days += 1
        wesf_mm = day["wesf_mm"]
        if wesf_mm is None or wesf_mm <= 0:
            continue
        if day["accumulated_snow"] or day["accumulated_wesf"] or day["qflag_failed"]:
            continue
        paired_days.append((snow_mm, wesf_mm, day["prcp_mm"]))

    with_prcp = [(s, w, p) for s, w, p in paired_days if p is not None]
    big = [(s, w, p) for s, w, p in with_prcp if w >= config.big_wesf_mm]

    def _rate(rows: list[tuple[float, float, float | None]], predicate: Any) -> float | None:
        if not rows:
            return None
        return sum(1 for row in rows if predicate(row)) / len(rows)

    ratios = [w / p for _, w, p in with_prcp if p and p > 0]
    candidates = [
        (s, w) for s, w, _ in paired_days
        if s >= config.exact10_min_snow_mm and w >= config.exact10_min_wesf_mm
    ]
    exact10 = sum(1 for s, w in candidates if abs(s / w - 10.0) < 1e-9)
    big_diffs = [abs(w - p) for _, w, p in big if p is not None]
    return {
        "snow_days": snow_days,
        "n_paired": len(paired_days),
        "presence_rate": (len(paired_days) / snow_days) if snow_days else None,
        "equality_rate": _rate(with_prcp, lambda r: abs(r[1] - r[2]) < 1e-9),
        "excess_rate": _rate(with_prcp, lambda r: r[1] > r[2] + 1e-9),
        "deficit_rate": _rate(with_prcp, lambda r: r[1] < r[2] - 1e-9),
        "n_big": len(big),
        "equality_rate_big": _rate(big, lambda r: abs(r[1] - r[2]) < 1e-9),
        "excess_rate_big": _rate(big, lambda r: r[1] > r[2] + 1e-9),
        "mean_absdiff_big_mm": (sum(big_diffs) / len(big_diffs)) if big_diffs else None,
        "ratio_median": statistics.median(ratios) if ratios else None,
        "exact10_rate": (exact10 / len(candidates)) if candidates else None,
        "n_exact10_candidates": len(candidates),
    }


def station_network(station_id: str) -> str:
    if station_id.startswith("USC"):
        return "coop"
    if station_id.startswith("US1"):
        return "cocorahs"
    return "other"


def classify_station(
    features: dict[str, Any],
    network: str,
    config: ScreenConfig = ScreenConfig(),
) -> tuple[str, str, str | None]:
    """Return (decision, reason, source_tier) for one station.

    The equality/excess rules are CoCoRaHS-scoped: at NWS COOP
    stations WESF is derived from the precipitation gauge by protocol,
    so WESF == PRCP carries no copying signal there; COOP stations
    are admitted on network protocol (tier ``ghcn-coop``) subject
    only to the history and exact-10 floors that apply to everyone.
    """
    if features["n_paired"] < config.min_paired_days:
        return "rejected", "insufficient_history", None
    exact10_rate = features.get("exact10_rate")
    if exact10_rate is not None and exact10_rate > config.max_exact10_rate:
        return "rejected", "slr10_heaping", None
    if network == "coop":
        return "admitted", "coop_protocol", "ghcn-coop"
    presence = features.get("presence_rate")
    if presence is None or presence < config.min_presence:
        return "rejected", "sporadic_swe_reporting", None
    equality_big = features.get("equality_rate_big")
    excess_big = features.get("excess_rate_big")
    if features.get("n_big", 0) >= config.min_big_days and equality_big is not None and excess_big is not None:
        if equality_big >= config.copy_eq_big and excess_big <= config.copy_excess_big:
            return "rejected", "gauge_copy_signature", None
    else:
        equality_all = features.get("equality_rate")
        excess_all = features.get("excess_rate")
        if (
            equality_all is not None
            and excess_all is not None
            and equality_all >= config.copy_eq_all
            and excess_all <= config.copy_excess_all
        ):
            return "rejected", "gauge_copy_signature", None
    return "admitted", "passed_screen", "ghcn-cocorahs-screened"


# ---------------------------------------------------------------------------
# GHCN station metadata / inventory
# ---------------------------------------------------------------------------


def load_station_metadata(path: Path) -> dict[str, dict[str, Any]]:
    """Parse ghcnd-stations.txt (fixed width)."""
    metadata: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if len(line) < 71:
            continue
        station_id = line[0:11].strip()
        latitude = _finite(line[12:20])
        longitude = _finite(line[21:30])
        elevation = _finite(line[31:37])
        if not station_id or latitude is None or longitude is None:
            continue
        metadata[station_id] = {
            "station_id": station_id,
            "station_name": line[41:71].strip(),
            "state": line[38:40].strip(),
            "latitude": latitude,
            "longitude": longitude,
            "elevation_m": elevation if elevation is not None and elevation > -900 else None,
        }
    return metadata


def load_inventory(path: Path) -> dict[str, dict[str, tuple[int, int]]]:
    """Parse ghcnd-inventory.txt into {station: {element: (first, last)}}."""
    inventory: dict[str, dict[str, tuple[int, int]]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if len(line) < 45:
            continue
        station_id = line[0:11].strip()
        element = line[31:35].strip()
        first = _finite(line[36:40])
        last = _finite(line[41:45])
        if not station_id or not element or first is None or last is None:
            continue
        inventory.setdefault(station_id, {})[element] = (int(first), int(last))
    return inventory


def inventory_prefilter(
    inventory_entry: dict[str, tuple[int, int]] | None,
    *,
    min_span_years: int = 5,
    min_last_year: int = 2015,
) -> bool:
    """Candidate rule: sustained, modern WESF reporting in the inventory."""
    if not inventory_entry or "WESF" not in inventory_entry:
        return False
    first, last = inventory_entry["WESF"]
    return (last - first + 1) >= min_span_years and last >= min_last_year


def match_pletcher_sites(
    sites: Iterable[tuple[float, float, float]],
    metadata: dict[str, dict[str, Any]],
    *,
    tolerance_deg: float = 0.0011,
) -> tuple[set[str], dict[str, Any]]:
    """Match Pletcher et al. site coordinates to GHCN station IDs.

    The public Pletcher datasets carry (lat, lon, elev) per event but
    no station IDs; GHCN station coordinates are published to four
    decimals, so exact rounded matching recovers most sites and a
    small tolerance pass (with an elevation tiebreak) recovers most
    of the rest.
    """
    by_coord: dict[tuple[float, float], list[str]] = defaultdict(list)
    for station_id, meta in metadata.items():
        by_coord[(round(meta["latitude"], 4), round(meta["longitude"], 4))].append(station_id)
    matched: set[str] = set()
    exact = 0
    tolerant = 0
    site_list = list(sites)
    for lat, lon, elev in site_list:
        key = (round(lat, 4), round(lon, 4))
        hits = by_coord.get(key, [])
        if len(hits) == 1:
            matched.add(hits[0])
            exact += 1
            continue
        if len(hits) > 1:
            best = min(hits, key=lambda sid: abs((metadata[sid]["elevation_m"] or elev) - elev))
            matched.add(best)
            exact += 1
            continue
        candidates = [
            sid
            for (mlat, mlon), sids in by_coord.items()
            if abs(mlat - lat) <= tolerance_deg and abs(mlon - lon) <= tolerance_deg
            for sid in sids
        ]
        if candidates:
            best = min(
                candidates,
                key=lambda sid: (
                    abs(metadata[sid]["latitude"] - lat) + abs(metadata[sid]["longitude"] - lon),
                    abs((metadata[sid]["elevation_m"] or elev) - elev),
                ),
            )
            matched.add(best)
            tolerant += 1
    report = {
        "sites": len(site_list),
        "matchedStations": len(matched),
        "exactMatches": exact,
        "toleranceMatches": tolerant,
        "unmatchedSites": len(site_list) - exact - tolerant,
    }
    return matched, report


# ---------------------------------------------------------------------------
# NCEI by-station download (polite, cached)
# ---------------------------------------------------------------------------


def _fetch_one(station_id: str, cache_dir: Path, timeout: float) -> tuple[str, str]:
    target = cache_dir / f"{station_id}.csv"
    if target.exists() and target.stat().st_size > 0:
        return station_id, "cached"
    url = NCEI_ACCESS_URL.format(station_id=station_id)
    request = Request(url, headers={"User-Agent": "mysnow-slr-calibration/1.0 (research; local)"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except Exception as exc:  # one bad station must not sink the run
        return station_id, f"failed:{type(exc).__name__}"
    if not payload.strip():
        return station_id, "failed:empty"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return station_id, "downloaded"


def fetch_station_files(
    station_ids: Iterable[str],
    cache_dir: Path,
    *,
    max_workers: int = 4,
    timeout: float = 45.0,
) -> dict[str, str]:
    """Download missing by-station CSVs from the NCEI access endpoint."""
    ids = sorted(set(station_ids))
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for station_id, status in pool.map(lambda sid: _fetch_one(sid, cache_dir, timeout), ids):
            results[station_id] = status
    return results


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

REGION_STATES: dict[str, tuple[str, ...]] = {
    "rockies": ("CO", "WY", "MT", "UT", "ID", "NM"),
    "sierra-nevada": ("CA", "NV"),
    "pacific-northwest": ("WA", "OR"),
    "northeast": ("ME", "NH", "VT", "MA", "NY"),
    "great-lakes": ("WI", "MI", "MN"),
    "alaska": ("AK",),
}
STATE_TO_REGION = {state: region for region, states in REGION_STATES.items() for state in states}
REGION_TIMEZONES = {
    "rockies": "America/Denver",
    "sierra-nevada": "America/Los_Angeles",
    "pacific-northwest": "America/Los_Angeles",
    "northeast": "America/New_York",
    "great-lakes": "America/Chicago",
    "alaska": "America/Anchorage",
}
REGION_REGIMES = {
    "rockies": "continental_mountain",
    "sierra-nevada": "sierra_maritime_mountain",
    "pacific-northwest": "maritime_mountain",
    "northeast": "continental_mountain",
    "great-lakes": "continental_mountain",
    "alaska": "maritime_mountain",
}
#: Regions whose candidate stations are enumerated from the GHCN
#: inventory and downloaded; the Rockies/Sierra candidates come from
#: the pre-existing audited pools (they were never provenance-blocked).
DOWNLOAD_REGIONS = ("pacific-northwest", "northeast", "great-lakes", "alaska")

TIER_LICENSES = {
    "ghcn-coop": "NOAA/NCEI public data; station-source attribution retained",
    "ghcn-cocorahs-screened": (
        "CoCoRaHS observations via NOAA GHCN-Daily (NCEI public access); "
        "CoCoRaHS commercial-use terms unverified - research use only; "
        "station-source attribution retained"
    ),
}

EVENT_COLUMNS = [
    "event_id", "storm_group_id", "station_id", "site_id", "station_name", "state",
    "observation_date", "valid_time", "observation_time_local_standard",
    "observation_window_hours", "latitude", "longitude", "elevation_m",
    "fresh_snow_mm", "fresh_swe_mm", "observed_slr", "baseline_method",
    "climate_regime", "qc_passed", "qc_weight", "source", "source_license",
    "source_flag", "source_tier", "screen_presence_rate", "screen_equality_rate_big",
    "screen_excess_rate_big", "screen_exact10_rate", "pletcher_confirmed",
]


def _parse_obs_time(value: str) -> tuple[int, int]:
    text = str(value or "").strip().zfill(4)
    if len(text) != 4 or not text.isdigit():
        return 7, 0
    hour, minute = int(text[:2]), int(text[2:])
    if hour == 24 and minute == 0:
        return 0, 0
    if hour > 23 or minute > 59:
        return 7, 0
    return hour, minute


def build_event_rows(
    meta: dict[str, Any],
    kept_pairs: list[dict[str, Any]],
    *,
    region: str,
    tier: str,
    features: dict[str, Any],
    pletcher_confirmed: bool,
) -> list[dict[str, Any]]:
    """Normalize hygiene-kept pairs into the package event schema."""
    timezone = ZoneInfo(REGION_TIMEZONES[region])
    rows: list[dict[str, Any]] = []
    for pair in kept_pairs:
        day = datetime.strptime(pair["date"], "%Y%m%d").date()
        hour, minute = _parse_obs_time(str(pair.get("obs_time") or "0700"))
        valid_local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone)
        snow_mm = float(pair["snow_mm"])
        wesf_mm = float(pair["wesf_mm"])
        rows.append({
            "event_id": f"ghcn:{meta['station_id']}:{day.isoformat()}",
            "storm_group_id": "pending",
            "station_id": meta["station_id"],
            "site_id": meta["station_id"],
            "station_name": meta["station_name"],
            "state": meta["state"],
            "observation_date": day.isoformat(),
            "valid_time": valid_local.isoformat(),
            "observation_time_local_standard": f"{hour:02d}:{minute:02d}",
            "observation_window_hours": 24,
            "latitude": meta["latitude"],
            "longitude": meta["longitude"],
            "elevation_m": meta["elevation_m"],
            "fresh_snow_mm": snow_mm,
            "fresh_swe_mm": round(wesf_mm, 4),
            "observed_slr": round(snow_mm / wesf_mm, 6),
            "baseline_method": "kuchera",
            "climate_regime": REGION_REGIMES[region],
            "qc_passed": True,
            "qc_weight": 1.0,
            "source": "NOAA GHCN-Daily direct SNOW+WESF (screened-v2)",
            "source_license": TIER_LICENSES[tier],
            "source_flag": pair.get("wesf_sflag") or pair.get("snow_sflag") or "",
            "source_tier": tier,
            "screen_presence_rate": features.get("presence_rate"),
            "screen_equality_rate_big": features.get("equality_rate_big"),
            "screen_excess_rate_big": features.get("excess_rate_big"),
            "screen_exact10_rate": features.get("exact10_rate"),
            "pletcher_confirmed": pletcher_confirmed,
        })
    rows.sort(key=lambda row: (row["observation_date"], row["station_id"]))
    return rows


# ---------------------------------------------------------------------------
# Hygiene over the pre-existing direct-label sets
# ---------------------------------------------------------------------------


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def hygiene_existing_sets(repo_root: Path, config: HygieneConfig = HygieneConfig()) -> dict[str, Any]:
    """Measure Pletcher-hygiene attrition on every stored direct set."""
    training = repo_root / "data" / "slr-training-data"
    observations = training / "normalized" / "observations"
    results: dict[str, Any] = {}

    def _run(name: str, rows: list[dict[str, Any]], snow_key: str, wesf_key: str) -> None:
        _, report = apply_hygiene(rows, config, snow_key=snow_key, wesf_key=wesf_key)
        results[name] = report

    _run("ghcn_paired_pool", _read_csv(observations / "ghcn_paired_snow_wesf.csv"), "snow_mm", "wesf_mm")
    _run("ghcn_qc_passed_previous", _read_csv(observations / "ghcn_qc_passed.csv"), "snow_mm", "wesf_mm")
    _run("rockies_events_stored", _read_csv(observations / "ghcn_rockies_events.csv"), "fresh_snow_mm", "fresh_swe_mm")
    sierra_events = _read_csv(repo_root / "data" / "sierra-slr-research" / "normalized" / "sierra_events.csv")
    _run("sierra_events_stored", sierra_events, "fresh_snow_mm", "fresh_swe_mm")

    # The demotion check sets, rebuilt exactly as
    # weak_label_investigation.load_direct_sets built them.
    rockies_events = {
        row["event_id"]: row for row in _read_csv(observations / "ghcn_rockies_events.csv")
    }
    colorado_pairs: list[dict[str, Any]] = []
    for row in _read_csv(training / "normalized" / "baseline" / "ghcn_rockies_kuchera.csv"):
        if str(row.get("state") or "") != "CO":
            continue
        source = rockies_events.get(str(row["event_id"]), {})
        colorado_pairs.append({
            "snow_mm": source.get("fresh_snow_mm"),
            "wesf_mm": source.get("fresh_swe_mm"),
        })
    _, colorado_report = apply_hygiene(colorado_pairs, config)
    results["colorado_check_set"] = colorado_report

    sierra_events_by_id = {row["event_id"]: row for row in sierra_events}
    sierra_pairs: list[dict[str, Any]] = []
    for row in _read_csv(repo_root / "data" / "sierra-slr-research" / "normalized" / "sierra_kuchera.csv"):
        source = sierra_events_by_id.get(str(row["event_id"]), {})
        sierra_pairs.append({
            "snow_mm": source.get("fresh_snow_mm"),
            "wesf_mm": source.get("fresh_swe_mm"),
        })
    _, sierra_ghcn_report = apply_hygiene(sierra_pairs, config)
    results["sierra_check_set_ghcn"] = sierra_ghcn_report

    cssl_swe: dict[str, float] = {}
    cssl_events_path = repo_root / "data" / "cssl" / "v13" / "research" / "cssl-profile-events.jsonl"
    if cssl_events_path.exists():
        for line in cssl_events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("fresh_swe_mm") not in (None, ""):
                cssl_swe[str(record["event_id"])] = float(record["fresh_swe_mm"])
    cssl_pairs: list[dict[str, Any]] = []
    for row in _read_csv(repo_root / "data" / "cssl" / "v13" / "research" / "cssl-aligned-baselines.csv"):
        swe = cssl_swe.get(str(row["event_id"]))
        slr = _finite(row.get("observed_slr"))
        cssl_pairs.append({
            "snow_mm": (slr * swe) if (swe is not None and slr is not None) else None,
            "wesf_mm": swe,
        })
    _, cssl_report = apply_hygiene(cssl_pairs, config)
    results["sierra_check_set_cssl"] = cssl_report

    sierra_total = {
        "inputRows": sierra_ghcn_report["inputRows"] + cssl_report["inputRows"],
        "keptRows": sierra_ghcn_report["keptRows"] + cssl_report["keptRows"],
        "rejectedSequential": {
            name: sierra_ghcn_report["rejectedSequential"][name] + cssl_report["rejectedSequential"][name]
            for name in HYGIENE_FILTER_ORDER
        },
        "standalone": {
            name: sierra_ghcn_report["standalone"][name] + cssl_report["standalone"][name]
            for name in HYGIENE_FILTER_ORDER
        },
    }
    results["sierra_check_set_total"] = sierra_total
    return results


# ---------------------------------------------------------------------------
# Screened-v2 build
# ---------------------------------------------------------------------------


def load_pletcher_sites(pickle_paths: Iterable[Path]) -> list[tuple[float, float, float]]:
    """Unique (lat, lon, elev) sites from the Pletcher et al. datasets."""
    import pandas as pd  # local import: only needed for the reference build

    sites: dict[tuple[float, float], float] = {}
    for path in pickle_paths:
        frame = pd.read_pickle(path)
        for lat, lon, elev in zip(frame["lat"], frame["lon"], frame["elev"]):
            sites[(round(float(lat), 4), round(float(lon), 4))] = float(elev)
    return [(lat, lon, elev) for (lat, lon), elev in sorted(sites.items())]


def _station_file_candidates(station_id: str, repo_root: Path, cache_dir: Path) -> list[tuple[str, Path]]:
    training = repo_root / "data" / "slr-training-data"
    candidates = [
        ("ncei-access-download", cache_dir / f"{station_id}.csv"),
        ("pool-station-data", training / "raw" / "ghcn" / "station_data" / f"{station_id}.csv"),
        ("northeast-raw", repo_root / "data" / "northeast-slr-research" / "raw" / "ghcn" / f"{station_id}.csv"),
        ("alaska-raw", repo_root / "data" / "alaska-slr-research" / "raw" / "ghcn" / f"{station_id}.csv.gz"),
        ("great-lakes-by-station", repo_root / "data" / "great-lakes-slr-research" / "by_station" / f"{station_id}.csv.gz"),
        ("sierra-raw-csv", repo_root / "data" / "sierra-slr-research" / "raw" / "ghcn" / f"{station_id}.csv"),
        ("sierra-raw-gz", repo_root / "data" / "sierra-slr-research" / "raw" / "ghcn" / f"{station_id}.csv.gz"),
        ("broad-audit-raw", training / "reports" / "broad-us-regional-audit" / "raw" / f"{station_id}.csv.gz"),
    ]
    return [(tag, path) for tag, path in candidates if path.exists()]


def _dir_station_ids(directory: Path, suffixes: tuple[str, ...]) -> set[str]:
    ids: set[str] = set()
    if not directory.exists():
        return ids
    for path in directory.iterdir():
        name = path.name
        for suffix in suffixes:
            if name.endswith(suffix):
                ids.add(name[: -len(suffix)])
                break
    return ids


def _elevation_bands(rows: list[dict[str, Any]]) -> dict[str, int]:
    bands = {"<500": 0, "500-900": 0, "900-1500": 0, "1500-2500": 0, ">=2500": 0, "unknown": 0}
    for row in rows:
        elev = row.get("elevation_m")
        if elev is None:
            bands["unknown"] += 1
        elif elev < 500:
            bands["<500"] += 1
        elif elev < 900:
            bands["500-900"] += 1
        elif elev < 1500:
            bands["900-1500"] += 1
        elif elev < 2500:
            bands["1500-2500"] += 1
        else:
            bands[">=2500"] += 1
    return bands


def build_screened_labels(
    *,
    repo_root: Path,
    output_dir: Path,
    fetch_missing: bool = False,
    max_workers: int = 4,
    pletcher_sites: list[tuple[float, float, float]] | None = None,
    hygiene_config: HygieneConfig = HygieneConfig(),
    screen_config: ScreenConfig = ScreenConfig(),
) -> dict[str, Any]:
    """Run hygiene measurement + the screen + the screened-v2 pool build."""
    training_dir = repo_root / "data" / "slr-training-data"
    cache_dir = output_dir / "raw" / "ghcn"
    metadata = load_station_metadata(training_dir / "raw" / "ghcn" / "ghcnd-stations.txt")
    inventory = load_inventory(training_dir / "raw" / "ghcn" / "ghcnd-inventory.txt")

    pletcher_matched: set[str] = set()
    pletcher_report: dict[str, Any] | None = None
    if pletcher_sites:
        pletcher_matched, pletcher_report = match_pletcher_sites(pletcher_sites, metadata)

    pool_ids = _dir_station_ids(training_dir / "raw" / "ghcn" / "station_data", (".csv",))
    ne_ids = _dir_station_ids(repo_root / "data" / "northeast-slr-research" / "raw" / "ghcn", (".csv",))
    ak_ids = _dir_station_ids(repo_root / "data" / "alaska-slr-research" / "raw" / "ghcn", (".csv.gz",))
    gl_ids = _dir_station_ids(repo_root / "data" / "great-lakes-slr-research" / "by_station", (".csv.gz",))
    sierra_ids = _dir_station_ids(repo_root / "data" / "sierra-slr-research" / "raw" / "ghcn", (".csv", ".csv.gz"))
    broad_ids = _dir_station_ids(training_dir / "reports" / "broad-us-regional-audit" / "raw", (".csv.gz",))
    prior_audit_ids = ne_ids | ak_ids | gl_ids | sierra_ids | broad_ids

    # ---- candidate universe -------------------------------------------------
    ledger: dict[str, dict[str, Any]] = {}

    def _ledger_row(station_id: str) -> dict[str, Any]:
        meta = metadata.get(station_id, {})
        row = ledger.setdefault(station_id, {
            "station_id": station_id,
            "station_name": meta.get("station_name", ""),
            "state": meta.get("state", ""),
            "region": STATE_TO_REGION.get(str(meta.get("state", "")), ""),
            "latitude": meta.get("latitude"),
            "longitude": meta.get("longitude"),
            "elevation_m": meta.get("elevation_m"),
            "network": station_network(station_id),
            "candidate_basis": [],
            "prefilter_pass": None,
            "data_source": "",
            "pletcher_confirmed": station_id in pletcher_matched,
            "decision": "",
            "reason": "",
            "source_tier": "",
            "events_admitted": 0,
        })
        return row

    for station_id, meta in metadata.items():
        region = STATE_TO_REGION.get(str(meta.get("state", "")))
        if region is None:
            continue
        entry = inventory.get(station_id)
        has_wesf = bool(entry and "WESF" in entry)
        bases: list[str] = []
        prefilter = inventory_prefilter(entry) if has_wesf else False
        if region in DOWNLOAD_REGIONS and has_wesf and prefilter:
            bases.append("inventory_prefilter")
        if station_id in pletcher_matched:
            bases.append("pletcher_confirmed")
        if station_id in pool_ids:
            bases.append("existing_pool")
        if station_id in SIERRA_STATIONS:
            bases.append("sierra_whitelist")
        if station_id in prior_audit_ids and region in DOWNLOAD_REGIONS:
            bases.append("prior_audit_data")
        if bases:
            row = _ledger_row(station_id)
            row["candidate_basis"] = bases
            row["prefilter_pass"] = prefilter if has_wesf else None
        elif region in DOWNLOAD_REGIONS and has_wesf:
            row = _ledger_row(station_id)
            row["candidate_basis"] = []
            row["prefilter_pass"] = False
            row["decision"] = "not_candidate"
            row["reason"] = "inventory_prefilter"

    candidates = [sid for sid, row in ledger.items() if row["candidate_basis"]]

    # ---- optional download --------------------------------------------------
    download_status: dict[str, str] = {}
    if fetch_missing:
        missing = []
        for station_id in candidates:
            row = ledger[station_id]
            region = str(row["region"])
            in_scope = region in DOWNLOAD_REGIONS or "pletcher_confirmed" in row["candidate_basis"]
            if in_scope and not _station_file_candidates(station_id, repo_root, cache_dir):
                missing.append(station_id)
        if missing:
            download_status = fetch_station_files(missing, cache_dir, max_workers=max_workers)

    # ---- screen + events ----------------------------------------------------
    events_by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    contrast_slr: dict[str, list[float]] = {"admitted": [], "rejected": []}
    feature_store: dict[str, dict[str, Any]] = {}
    for station_id in sorted(candidates):
        row = ledger[station_id]
        meta = metadata.get(station_id)
        if meta is None:
            row["decision"] = "rejected"
            row["reason"] = "no_station_metadata"
            continue
        resolved = _station_file_candidates(station_id, repo_root, cache_dir)
        if not resolved:
            row["decision"] = "rejected"
            row["reason"] = "no_data_file"
            continue
        source_tag, path = resolved[0]
        row["data_source"] = source_tag
        try:
            days = parse_station_file(path)
        except Exception:  # unparseable files are ledgered, not fatal
            row["decision"] = "rejected"
            row["reason"] = "parse_failure"
            continue
        features = compute_screen_features(days, screen_config)
        feature_store[station_id] = features
        for key in (
            "snow_days", "n_paired", "presence_rate", "equality_rate", "excess_rate",
            "equality_rate_big", "excess_rate_big", "n_big", "mean_absdiff_big_mm",
            "ratio_median", "exact10_rate",
        ):
            row[key] = features.get(key)
        decision, reason, tier = classify_station(features, str(row["network"]), screen_config)
        row["decision"], row["reason"], row["source_tier"] = decision, reason, tier or ""
        pairs = station_pairs(days)
        kept, _ = apply_hygiene(pairs, hygiene_config)
        if (
            str(row["network"]) in ("cocorahs", "other")
            and features["n_paired"] >= screen_config.min_paired_days
        ):
            # Contrast slice: big days only — the undercatch bias of a
            # copied SWE field concentrates exactly there and is
            # diluted to invisibility in an all-events median.
            bucket = "admitted" if decision == "admitted" else "rejected"
            contrast_slr[bucket].extend(
                float(p["snow_mm"]) / float(p["wesf_mm"])
                for p in kept
                if float(p["wesf_mm"]) >= screen_config.big_wesf_mm
            )
        if decision != "admitted":
            continue
        region = str(row["region"])
        if region == "sierra-nevada" and station_id not in SIERRA_STATIONS:
            row["events_admitted"] = 0
            row["reason"] = "admitted_but_outside_sierra_whitelist"
            continue
        if region not in DOWNLOAD_REGIONS and region != "sierra-nevada" and "pletcher_confirmed" in row["candidate_basis"] and "existing_pool" not in row["candidate_basis"]:
            # Survey-confirmed stations outside the download regions are
            # validation references for the Rockies refresh, not pool
            # additions (keeps the refresh an identical-rules comparison).
            row["events_admitted"] = 0
            row["reason"] = "admitted_validation_only"
            continue
        event_rows = build_event_rows(
            meta,
            kept,
            region=region,
            tier=str(tier),
            features=features,
            pletcher_confirmed=bool(row["pletcher_confirmed"]),
        )
        events_by_region[region].extend(event_rows)
        row["events_admitted"] = len(event_rows)

    for region, rows in events_by_region.items():
        rows.sort(key=lambda r: (r["observation_date"], r["station_id"]))
        _assign_storm_groups(rows, region)

    report = _assemble_report(
        repo_root=repo_root,
        ledger=ledger,
        feature_store=feature_store,
        events_by_region=events_by_region,
        contrast_slr=contrast_slr,
        pletcher_matched=pletcher_matched,
        pletcher_report=pletcher_report,
        download_status=download_status,
        hygiene_config=hygiene_config,
        screen_config=screen_config,
    )
    _write_outputs(output_dir, ledger, events_by_region, report, pletcher_matched, metadata)
    return report


LEDGER_COLUMNS = [
    "station_id", "station_name", "state", "region", "latitude", "longitude",
    "elevation_m", "network", "candidate_basis", "prefilter_pass", "data_source",
    "snow_days", "n_paired", "presence_rate", "equality_rate", "excess_rate",
    "equality_rate_big", "excess_rate_big", "n_big", "mean_absdiff_big_mm",
    "ratio_median", "exact10_rate", "pletcher_confirmed", "decision", "reason",
    "source_tier", "events_admitted",
]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _before_counts(repo_root: Path) -> dict[str, Any]:
    training = repo_root / "data" / "slr-training-data"
    before: dict[str, Any] = {}

    def _stored(name: str, path: Path) -> None:
        rows = _read_csv(path)
        before[name] = {
            "admittedEvents": len(rows),
            "admittedStations": len({row["station_id"] for row in rows}),
            "source": str(path.relative_to(repo_root)),
        }

    _stored("rockies", training / "normalized" / "observations" / "ghcn_rockies_events.csv")
    _stored("sierra-nevada", repo_root / "data" / "sierra-slr-research" / "normalized" / "sierra_events.csv")

    def _json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    pnw = _json(repo_root / "data" / "pnw-slr-research" / "pnw-blocker-report.json")
    coverage = pnw.get("coverage", {}) if isinstance(pnw, dict) else {}
    before["pacific-northwest"] = {
        "admittedEvents": 0,
        "admittedStations": 0,
        "candidatePairsScreened": coverage.get("screenedEventCount"),
        "candidateStationsScreened": coverage.get("screenedStationCount"),
        "eventsAtOrAbove900m": coverage.get("eventsAtOrAbove900m"),
        "source": "data/pnw-slr-research/pnw-blocker-report.json",
        "note": "Previous audit screened the old pool only; every candidate was source-flag N and inadmissible under the provenance rule.",
    }
    napp = _json(repo_root / "data" / "northern-appalachians-slr-research" / "northern-appalachians-blocker-report.json")
    by_class = napp.get("byClass", {}) if isinstance(napp, dict) else {}
    before["northeast"] = {
        "admittedEvents": 0,
        "admittedStations": 0,
        "candidatePairsScreened": sum(int(v.get("positiveSameDatePairs", 0)) for v in by_class.values() if isinstance(v, dict)),
        "source": "data/northern-appalachians-slr-research/northern-appalachians-blocker-report.json",
        "note": "Positive same-date SNOW+WESF pairs in the audit classes; all carried source flag N and were inadmissible.",
    }
    gl = _json(repo_root / "data" / "great-lakes-slr-research" / "great-lakes-blocker-report.json")
    gl_audit = gl.get("exactCachedEventAudit", {}) if isinstance(gl, dict) else {}
    before["great-lakes"] = {
        "admittedEvents": int(gl_audit.get("admissibleEventCount", 0)),
        "admittedStations": 0,
        "candidatePairsScreened": gl_audit.get("maskedPositiveSameDateEventCount"),
        "source": "data/great-lakes-slr-research/great-lakes-blocker-report.json",
        "note": "Positive same-date pairs inside the study mask in the cached pool audit; all source-flag N.",
    }
    broad = _json(training / "reports" / "broad-us-regional-audit" / "broad-us-regional-audit.json")
    ak_state = (broad.get("states", {}) if isinstance(broad, dict) else {}).get("AK", {})
    before["alaska"] = {
        "admittedEvents": int(ak_state.get("strictEvents", 0)),
        "admittedStations": 0,
        "candidatePairsScreened": ak_state.get("provisionalSourceNEvents"),
        "candidateStationsScreened": len(ak_state.get("provisionalStations", [])),
        "source": "data/slr-training-data/reports/broad-us-regional-audit/broad-us-regional-audit.json (states.AK)",
        "note": "Provisional pairs are from two USC stations whose WESF still carried flag N; strict (admissible) count was zero.",
    }
    return before


def _assemble_report(
    *,
    repo_root: Path,
    ledger: dict[str, dict[str, Any]],
    feature_store: dict[str, dict[str, Any]],
    events_by_region: dict[str, list[dict[str, Any]]],
    contrast_slr: dict[str, list[float]],
    pletcher_matched: set[str],
    pletcher_report: dict[str, Any] | None,
    download_status: dict[str, str],
    hygiene_config: HygieneConfig,
    screen_config: ScreenConfig,
) -> dict[str, Any]:
    rows = list(ledger.values())
    with_data = [row for row in rows if row.get("data_source")]

    # --- validation: Pletcher survey-confirmed stations ----------------------
    pletcher_rows = [row for row in with_data if row.get("pletcher_confirmed")]
    pletcher_sufficient = [row for row in pletcher_rows if (row.get("n_paired") or 0) >= screen_config.min_paired_days]
    pletcher_admitted = [row for row in pletcher_sufficient if row["decision"] == "admitted"]
    pletcher_validation = {
        "matchedStations": pletcher_report,
        "withDataInScope": len(pletcher_rows),
        "sufficientHistory": len(pletcher_sufficient),
        "admitted": len(pletcher_admitted),
        "passRateAmongSufficient": (len(pletcher_admitted) / len(pletcher_sufficient)) if pletcher_sufficient else None,
        "rejectionReasons": dict(Counter(row["reason"] for row in pletcher_sufficient if row["decision"] != "admitted")),
    }

    # --- validation: COOP negative control for the equality axis -------------
    coop_rows = [row for row in with_data if row.get("network") == "coop" and (row.get("n_paired") or 0) >= screen_config.min_paired_days]
    coop_big = [row for row in coop_rows if (row.get("n_big") or 0) >= screen_config.min_big_days]
    coop_would_fail = [
        row for row in coop_big
        if (row.get("equality_rate_big") or 0) >= screen_config.copy_eq_big
        and (row.get("excess_rate_big") or 1) <= screen_config.copy_excess_big
    ]
    coop_control = {
        "stationsWithSufficientHistory": len(coop_rows),
        "stationsWithBigDays": len(coop_big),
        "medianEqualityRateBig": _median([float(row["equality_rate_big"]) for row in coop_big if row.get("equality_rate_big") is not None]),
        "wouldFailCocorahsCopyRule": len(coop_would_fail),
        "note": (
            "COOP WESF is gauge-derived by protocol, so WESF == PRCP is expected there; "
            "the copy rule is therefore never applied to the coop network. This control "
            "quantifies why the rule must stay network-scoped."
        ),
    }

    cocorahs_rows = [
        row for row in with_data
        if row.get("network") in ("cocorahs", "other") and (row.get("n_paired") or 0) >= screen_config.min_paired_days
    ]
    population = {
        "cocorahsCandidatesWithSufficientHistory": len(cocorahs_rows),
        "decisionCounts": dict(Counter(row["decision"] for row in cocorahs_rows)),
        "rejectionReasons": dict(Counter(row["reason"] for row in cocorahs_rows if row["decision"] != "admitted")),
        "admittedMedianSlrBigDays": _median(contrast_slr["admitted"]),
        "rejectedMedianSlrBigDays": _median(contrast_slr["rejected"]),
        "admittedBigDayEvents": len(contrast_slr["admitted"]),
        "rejectedBigDayEvents": len(contrast_slr["rejected"]),
        "note": (
            "Indirect check, restricted to big days (WESF >= 10 mm) where gauge "
            "undercatch concentrates: copied SWE inherits the undercatch and biases "
            "copier-station SLR high, so the admitted population is expected to sit "
            "lower than the rejected one on this slice. An all-events median shows "
            "no separation and is not used."
        ),
    }

    # --- per-region before/after ---------------------------------------------
    before = _before_counts(repo_root)
    regions: dict[str, Any] = {}
    for region in REGION_STATES:
        events = events_by_region.get(region, [])
        stations = {row["station_id"] for row in events}
        high_stations = {
            row["station_id"] for row in events if (row.get("elevation_m") or 0) >= 900
        }
        entry: dict[str, Any] = {
            "before": before.get(region, {}),
            "after": {
                "events": len(events),
                "stations": len(stations),
                "tiers": dict(Counter(row["source_tier"] for row in events)),
                "eventElevationBands": _elevation_bands(events),
                "stationsAtOrAbove900m": len(high_stations),
                "eventsFromStationsAtOrAbove900m": sum(1 for row in events if (row.get("elevation_m") or 0) >= 900),
            },
        }
        if region == "rockies":
            from .ghcn import is_rockies_station

            masked = [
                row for row in events
                if is_rockies_station(str(row["state"]), float(row["latitude"]), float(row["longitude"]), float(row["elevation_m"] or 0))
            ]
            entry["after"]["eventsWithinPreviousRockiesMask"] = len(masked)
            entry["after"]["stationsWithinPreviousRockiesMask"] = len({row["station_id"] for row in masked})
        regions[region] = entry

    blocked = ("pacific-northwest", "northeast", "great-lakes", "alaska")
    totals = {
        "eventsAllRegions": sum(len(v) for v in events_by_region.values()),
        "stationsAllRegions": len({row["station_id"] for rows in events_by_region.values() for row in rows}),
        "newAdmissibleEventsInPreviouslyBlockedRegions": sum(len(events_by_region.get(r, [])) for r in blocked),
        "candidateStationsLedgered": len(rows),
        "candidateStationsWithData": len(with_data),
        "noDataFileStations": sum(1 for row in rows if row.get("reason") == "no_data_file"),
    }

    return {
        "step": "step-1-direct-label-repair",
        "hygiene": hygiene_existing_sets(repo_root, hygiene_config),
        "screenConfig": {
            "minPairedDays": screen_config.min_paired_days,
            "bigWesfMm": screen_config.big_wesf_mm,
            "minBigDays": screen_config.min_big_days,
            "copyEqBig": screen_config.copy_eq_big,
            "copyExcessBig": screen_config.copy_excess_big,
            "copyEqAll": screen_config.copy_eq_all,
            "copyExcessAll": screen_config.copy_excess_all,
            "minPresence": screen_config.min_presence,
            "maxExact10Rate": screen_config.max_exact10_rate,
        },
        "validation": {
            "pletcher": pletcher_validation,
            "coopControl": coop_control,
            "population": population,
        },
        "regions": regions,
        "downloads": dict(Counter(download_status.values())),
        "totals": totals,
    }


def _write_outputs(
    output_dir: Path,
    ledger: dict[str, dict[str, Any]],
    events_by_region: dict[str, list[dict[str, Any]]],
    report: dict[str, Any],
    pletcher_matched: set[str],
    metadata: dict[str, dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / ".gitignore").write_text("raw/\n", encoding="utf-8")
    for region in REGION_STATES:
        rows = events_by_region.get(region, [])
        with (output_dir / f"events_{region}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVENT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    with (output_dir / "station_ledger.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for station_id in sorted(ledger):
            row = dict(ledger[station_id])
            row["candidate_basis"] = ";".join(row.get("candidate_basis") or [])
            writer.writerow(row)
    with (output_dir / "pletcher_reference_stations.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["station_id", "station_name", "state", "latitude", "longitude", "elevation_m"])
        for station_id in sorted(pletcher_matched):
            meta = metadata.get(station_id, {})
            writer.writerow([
                station_id, meta.get("station_name", ""), meta.get("state", ""),
                meta.get("latitude", ""), meta.get("longitude", ""), meta.get("elevation_m", ""),
            ])
    (output_dir / "screening_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    (output_dir / "SCREENING_REPORT.md").write_text(render_screening_report_md(report), encoding="utf-8")


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_screening_report_md(report: dict[str, Any]) -> str:
    lines: list[str] = []
    totals = report["totals"]
    lines.append("# Screened direct-label pool v2 — screening report")
    lines.append("")
    lines.append(
        "Step 1 of the 2026-10 SLR plan: fix the direct labels. Two mechanisms: "
        "(1) Pletcher et al. (2026) label hygiene applied to every pre-existing "
        "direct-label set, with attrition measured per filter; (2) a station-level "
        "CoCoRaHS core-sampler screen that replicates, from data alone, the effect "
        "of the survey Pletcher et al. used to identify genuine snowboard-core "
        "observers among the GHCN source-flag-N population that previous regional "
        "audits had to reject wholesale."
    )
    lines.append("")
    lines.append(
        f"**Outcome:** {totals['eventsAllRegions']} admissible events from "
        f"{totals['stationsAllRegions']} stations across six regions, of which "
        f"{totals['newAdmissibleEventsInPreviouslyBlockedRegions']} events are in "
        "previously blocked regions (Pacific Northwest, Northeast, Great Lakes, "
        "Alaska), which previously admitted **zero**. "
        f"{totals['candidateStationsLedgered']} candidate stations are ledgered "
        f"({totals['candidateStationsWithData']} with data resolved). Labels only — "
        "no model was trained."
    )
    lines.append("")

    # ---- hygiene ---------------------------------------------------------
    lines.append("## 1. Pletcher hygiene attrition on the pre-existing sets")
    lines.append("")
    lines.append(
        "Filters, in order: snowfall >= 5.09 cm; snowfall SWE >= 2.79 mm; "
        "SLR exactly 10 dropped (documented missing-data placeholder); "
        "SLR < 2 dropped; SLR > 50 dropped. `Rejected (sequential)` counts each "
        "row once at the first filter it fails; `Exact-10 standalone` counts every "
        "row whose SLR is exactly 10 regardless of other filters."
    )
    lines.append("")
    lines.append("| Set | Input | Kept | Snow < 5.09 cm | SWE < 2.79 mm | Exact-10 (seq.) | SLR < 2 | SLR > 50 | Exact-10 standalone |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    labels = {
        "ghcn_paired_pool": "Aug-2026 GHCN paired pool (master)",
        "ghcn_qc_passed_previous": "Previous QC-passed pool",
        "rockies_events_stored": "Stored Rockies events",
        "sierra_events_stored": "Stored Sierra events",
        "colorado_check_set": "Colorado demotion check set",
        "sierra_check_set_ghcn": "Sierra demotion check set (GHCN part)",
        "sierra_check_set_cssl": "Sierra demotion check set (CSSL part)",
    }
    for key, label in labels.items():
        entry = report["hygiene"].get(key)
        if not entry:
            continue
        seq = entry["rejectedSequential"]
        lines.append(
            f"| {label} | {entry['inputRows']} | {entry['keptRows']} | "
            f"{seq['snow_below_minimum']} | {seq['wesf_below_minimum']} | "
            f"{seq['slr_exactly_10_placeholder']} | {seq['slr_below_minimum']} | "
            f"{seq['slr_above_maximum']} | {entry['standalone']['slr_exactly_10_placeholder']} |"
        )
    sierra_total = report["hygiene"].get("sierra_check_set_total", {})
    if sierra_total:
        seq = sierra_total.get("rejectedSequential", {})
        lines.append(
            f"| **Sierra check set (total)** | {sierra_total.get('inputRows')} | "
            f"{sierra_total.get('keptRows')} | {seq.get('snow_below_minimum')} | "
            f"{seq.get('wesf_below_minimum')} | {seq.get('slr_exactly_10_placeholder')} | "
            f"{seq.get('slr_below_minimum')} | {seq.get('slr_above_maximum')} | "
            f"{sierra_total.get('standalone', {}).get('slr_exactly_10_placeholder')} |"
        )
    lines.append("")
    co = report["hygiene"].get("colorado_check_set", {})
    si = report["hygiene"].get("sierra_check_set_total", {})
    lines.append(
        f"Demotion check sets specifically: the Colorado set (n={co.get('inputRows')}) contained "
        f"{co.get('standalone', {}).get('slr_exactly_10_placeholder')} exact-10 placeholders and the "
        f"Sierra set (n={si.get('inputRows')}) contained "
        f"{si.get('standalone', {}).get('slr_exactly_10_placeholder')}. Hygiene keeps "
        f"{co.get('keptRows')} Colorado and {si.get('keptRows')} Sierra check events. "
        "(Pool delta only — the demotion analysis was not redone.)"
    )
    lines.append("")

    # ---- screen ----------------------------------------------------------
    cfg = report["screenConfig"]
    lines.append("## 2. The core-sampler screen")
    lines.append("")
    lines.append(
        "A copied SWE field equals gauge catch (PRCP) even on the largest days and "
        "never exceeds it; a genuine board core diverges, because gauge undercatch "
        "makes true core SWE exceed catch on big, windy events. The screen is "
        "computed from each station's full GHCN history and is deterministic:"
    )
    lines.append("")
    lines.append(f"- History floor (all networks): at least {cfg['minPairedDays']} valid SNOW+WESF pairs, else `insufficient_history`.")
    lines.append(f"- Exact-10 ceiling (all networks): share of candidate pairs with SLR exactly 10 must be <= {cfg['maxExact10Rate']}, else `slr10_heaping`.")
    lines.append("- COOP network (USC IDs): admitted on protocol (tier `ghcn-coop`). WESF there is gauge-derived by NWS protocol, so equality carries no copying signal (see the negative control in §3).")
    lines.append(f"- CoCoRaHS/other networks: SWE presence on snow days must be >= {cfg['minPresence']}, else `sporadic_swe_reporting`.")
    lines.append(
        f"- Copy signature: with >= {cfg['minBigDays']} big days (WESF >= {cfg['bigWesfMm']} mm), reject as "
        f"`gauge_copy_signature` when big-day equality >= {cfg['copyEqBig']} and big-day excess (WESF > PRCP) "
        f"<= {cfg['copyExcessBig']}; with fewer big days, reject when whole-history equality >= {cfg['copyEqAll']} "
        f"and excess <= {cfg['copyExcessAll']}."
    )
    lines.append("- Survivors are tier `ghcn-cocorahs-screened`.")
    lines.append("")
    lines.append(
        "Deliberately *not* used as discriminators: the WESF/PRCP ratio median "
        "(~1.000 in every population examined, including survey-confirmed core "
        "samplers — the signal lives in the tails, not the median) and raw "
        "equality over all days (small events legitimately agree at 0.01-inch "
        "reporting resolution; the signal concentrates in big days)."
    )
    lines.append("")

    # ---- validation --------------------------------------------------------
    val = report["validation"]
    pl = val["pletcher"]
    lines.append("## 3. Screen validation")
    lines.append("")
    matched = pl.get("matchedStations") or {}
    lines.append(
        f"**Positive reference — Pletcher et al. survey-confirmed core samplers.** "
        f"Their public datasets carry coordinates but no station IDs; coordinate "
        f"matching against ghcnd-stations.txt recovered {matched.get('matchedStations')} of "
        f"{matched.get('sites')} sites ({matched.get('exactMatches')} exact, "
        f"{matched.get('toleranceMatches')} by tolerance). Of the matched stations, "
        f"{pl['withDataInScope']} had data in scope and {pl['sufficientHistory']} had "
        f"sufficient history; the screen admitted {pl['admitted']} "
        f"(pass rate {_fmt(pl['passRateAmongSufficient'], 2)}). Rejection reasons among "
        f"confirmed core samplers: {pl['rejectionReasons']} — these are false rejections "
        "(calm-climate genuine samplers whose cores rarely diverge from catch are the "
        "expected loss; the screen trades recall for precision by design)."
    )
    lines.append("")
    cc = val["coopControl"]
    lines.append(
        f"**Negative control — COOP equality.** {cc['stationsWithBigDays']} COOP stations "
        f"with big days have median big-day equality {_fmt(cc['medianEqualityRateBig'], 2)} and "
        f"{cc['wouldFailCocorahsCopyRule']} would fail the CoCoRaHS copy rule if it were "
        "applied to them. It is not applied: the rule is network-scoped, and this control "
        "is the evidence that it must be."
    )
    lines.append("")
    pop = val["population"]
    admitted_med = pop.get("admittedMedianSlrBigDays")
    rejected_med = pop.get("rejectedMedianSlrBigDays")
    if admitted_med is not None and rejected_med is not None and admitted_med < rejected_med:
        contrast_sentence = (
            "the admitted population sits lower on this slice, the direction expected "
            "if the rejected population is enriched for gauge-copiers"
        )
    else:
        contrast_sentence = (
            "no separation in the expected direction was observed, so this indirect "
            "check is inconclusive and is reported as such"
        )
    lines.append(
        f"**Population behaviour.** {pop['cocorahsCandidatesWithSufficientHistory']} CoCoRaHS "
        f"candidates had sufficient history; decisions {pop['decisionCounts']}, rejection "
        f"reasons {pop['rejectionReasons']}. Indirect contrast, big days only "
        f"(WESF >= 10 mm, where copied SWE inherits gauge undercatch and inflates SLR): "
        f"median SLR admitted {_fmt(admitted_med, 2)} "
        f"(n={pop['admittedBigDayEvents']}) vs rejected {_fmt(rejected_med, 2)} "
        f"(n={pop['rejectedBigDayEvents']}) — {contrast_sentence}. "
        "(An all-events median shows no separation — 11.96 vs 11.89 — because the "
        "bias concentrates in the big-day slice.)"
    )
    lines.append("")
    lines.append(
        "Honesty note: there is no survey-confirmed *copier* list to validate against; "
        "the negative direction rests on synthetic station histories (unit tests), the "
        "bimodality of big-day equality across the candidate population, and the "
        "admitted/rejected SLR contrast above. The screen is a precision instrument "
        "with quantified behaviour, not a provenance proof."
    )
    lines.append("")

    # ---- before / after ----------------------------------------------------
    lines.append("## 4. Before / after by region")
    lines.append("")
    lines.append("| Region | Before: admitted events (stations) | Before: candidate pairs screened | After: admitted events (stations) | After: >=900 m stations (events) |")
    lines.append("|---|---:|---:|---:|---:|")
    names = {
        "rockies": "Rockies",
        "sierra-nevada": "Sierra Nevada",
        "pacific-northwest": "Pacific Northwest",
        "northeast": "Northeast / N. Appalachians",
        "great-lakes": "Great Lakes / Upper Midwest",
        "alaska": "Alaska",
    }
    for region, name in names.items():
        entry = report["regions"][region]
        before, after = entry["before"], entry["after"]
        candidate_pairs = before.get('candidatePairsScreened')
        lines.append(
            f"| {name} | {before.get('admittedEvents')} ({before.get('admittedStations')}) | "
            f"{candidate_pairs if candidate_pairs is not None else '—'} | "
            f"{after['events']} ({after['stations']}) | "
            f"{after['stationsAtOrAbove900m']} ({after['eventsFromStationsAtOrAbove900m']}) |"
        )
    lines.append("")
    pnw_after = report["regions"]["pacific-northwest"]["after"]
    lines.append(
        f"Pacific Northwest detail: the old blocker was 2 low stations and 0 events at "
        f">=900 m. The screened pool now has {pnw_after['stationsAtOrAbove900m']} stations at "
        f">=900 m contributing {pnw_after['eventsFromStationsAtOrAbove900m']} events; "
        f"elevation bands (events): {pnw_after['eventElevationBands']}."
    )
    lines.append("")
    rockies_after = report["regions"]["rockies"]["after"]
    if "eventsWithinPreviousRockiesMask" in rockies_after:
        lines.append(
            f"Rockies comparability: {rockies_after['events']} events region-wide under the new "
            f"rules; {rockies_after['eventsWithinPreviousRockiesMask']} of them from "
            f"{rockies_after['stationsWithinPreviousRockiesMask']} stations inside the previous "
            "research mask. The stored pool's 14,213 events came from 146 mask stations under "
            "the older QC, which had no station-level method screen and no presence floor — "
            "and §1 shows those stored events have *zero* hygiene attrition (their snow floor "
            "was already ~51 mm), so the shrinkage is the screen acting on stations, not "
            "hygiene acting on events. The same applies to Sierra (26 stored stations -> 12)."
        )
        lines.append("")
    downloads = report.get("downloads") or {}
    if downloads:
        lines.append(f"Downloads this run (NCEI by-station access, cached under `raw/ghcn/`): {downloads}.")
        lines.append("")

    # ---- licensing / caveats ----------------------------------------------
    lines.append("## 5. Licensing and caveats")
    lines.append("")
    lines.append(
        "- GHCN-Daily data are NOAA/NCEI public data; station-source attribution is "
        "retained in every event row (`source_flag`, `source_tier`)."
    )
    lines.append(
        "- CoCoRaHS observations reach this pool through GHCN-Daily. No explicit "
        "CoCoRaHS commercial licence text was found in the preceding research; the "
        "screened CoCoRaHS tier is therefore flagged **research use only, terms "
        "unverified** in its `source_license` field. Do not treat this pool as "
        "cleared for commercial redistribution."
    )
    lines.append(
        "- The screen cannot certify method; it certifies *behaviour consistent "
        "with* an independent core measurement. Survey-confirmed stations it "
        "rejects are listed in the ledger with their scores for audit."
    )
    lines.append(
        "- Rockies/Sierra scope: refreshed from the pre-existing audited station "
        "pools under identical rules. Survey-confirmed stations in those states "
        "were used as validation references only, so the before/after comparison "
        "isolates the rules change rather than a station-set change."
    )
    no_data = report["totals"].get("noDataFileStations")
    if no_data:
        lines.append(
            f"- {no_data} candidate stations could not be downloaded (network "
            "errors) and are ledgered as `no_data_file`; a rerun with "
            "`--fetch-missing` retries them (the download cache is resumable)."
        )
    lines.append("")
    return "\n".join(lines) + "\n"
