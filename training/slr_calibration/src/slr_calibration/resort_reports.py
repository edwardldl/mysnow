"""Resort-reported snowfall: parsing, window alignment, verification.

MySnow's model forecasts describe 24 h windows ending 07:00 resort-local
time. Resorts publish their own snow reports (typically each morning)
whose headline figure is "new snow in the last 24 hours". This module
parses those reports (primary source: the SnoCountry JSON API), aligns
each report to the model window it describes, and computes verification
statistics against the stored forecast records.

Alignment rule (v1)
-------------------
SnoCountry's ``reportDateTime`` is a naive timestamp that resorts update
in their own local time (the API's ``timezone`` field is unpopulated);
it is therefore interpreted in the resort's registry timezone. A report
carrying local date ``D`` is matched to the model window whose
``window_end_local_date`` is ``D`` -- the 24 h ending 07:00 local on D.

Caveats, by design accepted in v1 and recorded so they can be revisited:

* A report timestamped in the afternoon describes a "last 24 hours"
  ending later than 07:00; the calendar-date rule absorbs that shift
  (up to ~12 h) as noise rather than attempting per-resort report-time
  archaeology.
* Some resorts report "since lifts closed yesterday" rather than a
  strict 24 h; SnoCountry normalises what resorts submit and does not
  flag the distinction.
* SnoCountry's new-snow figure is a min/max range across the resort's
  own measurement points (commonly base and summit stakes). The
  comparison value is the range midpoint; min and max are retained and
  shown alongside the model's base/mid/summit points.
* Forecast vintage: a report is compared against the last forecast
  issued *before* its window began (greatest ``cycle_utc`` strictly
  earlier than ``window_start_utc``), taken from the per-cycle tracking
  records, falling back to the installed frontend JSON under the same
  rule. Forecasts are never compared against a cycle that already saw
  part of the window.

All snowfall values are stored in centimetres (source values are
inches; the raw inch values are retained on the parsed record).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

INCH_TO_CM = 2.54

_RANGE_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(-?\d+(?:\.\d+)?)\s*$"
)
_SINGLE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*$")


def parse_inches_range(raw: object) -> tuple[float | None, float | None]:
    """Parse a SnoCountry snow figure into a (min, max) pair in inches.

    Accepts numbers, numeric strings (``"10"``), and range strings
    (``"13-15"``). Empty strings, ``None`` and unparseable values mean
    "not reported" and return ``(None, None)``. A reported ``"0"`` is a
    real observation and parses to ``(0.0, 0.0)``.
    """
    if raw is None:
        return (None, None)
    if isinstance(raw, (int, float)):
        value = float(raw)
        return (value, value)
    text = str(raw).strip()
    if not text:
        return (None, None)
    match = _RANGE_RE.match(text)
    if match:
        lo, hi = float(match.group(1)), float(match.group(2))
        return (min(lo, hi), max(lo, hi))
    match = _SINGLE_RE.match(text)
    if match:
        value = float(match.group(1))
        return (value, value)
    return (None, None)


def _range_cm(inches: tuple[float | None, float | None]) -> dict[str, float | None]:
    lo, hi = inches
    if lo is None or hi is None:
        return {"min": None, "max": None, "mid": None}
    return {
        "min": round(lo * INCH_TO_CM, 2),
        "max": round(hi * INCH_TO_CM, 2),
        "mid": round((lo + hi) / 2.0 * INCH_TO_CM, 2),
    }


def parse_snocountry_item(
    item: dict,
    *,
    resort_slug: str,
    resort_name: str,
    timezone_name: str,
    collected_at_utc: datetime,
    raw_ref: str,
) -> dict | None:
    """Parse one SnoCountry getSnowReport item into a stored record.

    Returns ``None`` when the item carries no usable report timestamp.
    Snow fields that the resort did not report parse to ``None`` values
    on the record (the record itself is still stored: "checked, nothing
    reported" is distinct from "not checked").
    """
    raw_dt = str(item.get("reportDateTime") or "").strip()
    if not raw_dt:
        return None
    try:
        naive = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    tz = ZoneInfo(timezone_name)
    local = naive.replace(tzinfo=tz)

    new_min_in = parse_inches_range(item.get("newSnowMin"))
    new_max_in = parse_inches_range(item.get("newSnowMax"))
    # newSnowMin/newSnowMax are separate fields for the same 24 h figure:
    # combine them into one range (either may be blank).
    lows = [v for v in (new_min_in[0], new_max_in[0]) if v is not None]
    highs = [v for v in (new_min_in[1], new_max_in[1]) if v is not None]
    new_24h_in: tuple[float | None, float | None] = (
        (min(lows), max(highs)) if lows and highs else (None, None)
    )
    snow_48h_in = parse_inches_range(item.get("snowLast48Hours"))
    base_min_in = parse_inches_range(item.get("avgBaseDepthMin"))
    base_max_in = parse_inches_range(item.get("avgBaseDepthMax"))
    base_lows = [v for v in (base_min_in[0], base_max_in[0]) if v is not None]
    base_highs = [v for v in (base_min_in[1], base_max_in[1]) if v is not None]
    base_in: tuple[float | None, float | None] = (
        (min(base_lows), max(base_highs)) if base_lows and base_highs else (None, None)
    )

    return {
        "source": "snocountry",
        "resort_slug": resort_slug,
        "resort_name": resort_name,
        "snocountry_id": str(item.get("id") or ""),
        "collected_at_utc": collected_at_utc.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "report_datetime_raw": raw_dt,
        "report_datetime_local": local.isoformat(),
        "report_tz": timezone_name,
        # The alignment key: the model window ending 07:00 local on
        # this date is the window the report describes (see module doc).
        "window_end_local_date": local.date().isoformat(),
        "new_snow_24h_in": {"min": new_24h_in[0], "max": new_24h_in[1]},
        "new_snow_24h_cm": _range_cm(new_24h_in),
        "snow_48h_in": {"min": snow_48h_in[0], "max": snow_48h_in[1]},
        "snow_48h_cm": _range_cm(snow_48h_in),
        "base_depth_cm": _range_cm(base_in),
        "operating_status": str(item.get("operatingStatus") or ""),
        "resort_status": str(item.get("resortStatus") or ""),
        "primary_surface_condition": str(item.get("primarySurfaceCondition") or ""),
        "raw_ref": raw_ref,
    }


def select_forecast_vintage(
    records: list[dict], window_start_utc: str
) -> dict | None:
    """Pick the forecast of record for one window from candidate records.

    Candidates are tracking/frontend records for one (point, arm,
    window). The forecast of record is the one with the greatest
    ``cycle_utc`` strictly before ``window_start_utc`` -- the last
    forecast issued before the window began. Records with missing or
    null snowfall never qualify. Returns ``None`` when no candidate
    qualifies.
    """
    best: dict | None = None
    for record in records:
        if record.get("missing"):
            continue
        if record.get("model_snowfall_cm") is None:
            continue
        cycle = record.get("cycle_utc") or record.get("cycleUtc")
        if not cycle or cycle >= window_start_utc:
            continue
        if best is None or cycle > (best.get("cycle_utc") or best.get("cycleUtc")):
            best = record
    return best


def comparison_row(
    report: dict,
    forecast: dict,
    *,
    role: str,
    arm: str,
) -> dict:
    """One matched resort-day-point-arm comparison row (values in cm)."""
    reported = report["new_snow_24h_cm"]["mid"]
    ens_eb = forecast.get("model_snowfall_cm")
    kuchera = forecast.get("kuchera_snowfall_cm")
    return {
        "resort_slug": report["resort_slug"],
        "resort_name": report["resort_name"],
        "window_end_local_date": report["window_end_local_date"],
        "report_datetime_local": report["report_datetime_local"],
        "role": role,
        "arm": arm,
        "cycle_utc": forecast.get("cycle_utc") or forecast.get("cycleUtc"),
        "lead_day": forecast.get("lead_day") or forecast.get("leadDay"),
        "reported_cm": reported,
        "reported_min_cm": report["new_snow_24h_cm"]["min"],
        "reported_max_cm": report["new_snow_24h_cm"]["max"],
        "ens_eb_cm": ens_eb,
        "kuchera_cm": kuchera,
        "err_ens_eb_cm": (
            round(reported - ens_eb, 2) if reported is not None and ens_eb is not None else None
        ),
        "err_kuchera_cm": (
            round(reported - kuchera, 2)
            if reported is not None and kuchera is not None
            else None
        ),
    }


def aggregate_errors(rows: list[dict], key: str) -> dict:
    """Bias / MAE / n for one error column over comparison rows."""
    errors = [r[key] for r in rows if r.get(key) is not None]
    if not errors:
        return {"n": 0, "bias_cm": None, "mae_cm": None}
    bias = sum(errors) / len(errors)
    mae = sum(abs(e) for e in errors) / len(errors)
    return {"n": len(errors), "bias_cm": round(bias, 2), "mae_cm": round(mae, 2)}
