"""Extended Phase-0 audit: ECMWF IFS open-data availability to step 240.

Step 1 of the ECMWF-arm horizon extension (user decision 2026-10-31:
the model should cover all days of ECMWF data). The Phase-0 audit
(scripts/audit_serving_sources.py) verified the required parameters at
steps 24 and 78 only. This audit re-derives the required parameter list
the same way -- the Phase-0 requirement constants plus the exact
param/level subset the serving adapter consumes
(scripts/run_model_serving.py: ECMWF_PL_PARAMS x ECMWF_PL_LEVELS +
ECMWF_SFC_PARAMS) -- and verifies it against the .index catalogue of
EVERY step file needed to build 24 h windows out to the open-data
ceiling (step 240): steps 0-144 at 3-hourly intervals and 150-240 at
6-hourly intervals, for the latest complete 00/12Z cycle.

It also verifies, from the GRIB messages themselves (byte-range fetches
of single messages), that the accumulated surface fields (tp/sf/cp)
keep accumulation-from-step-0 semantics at steps 144/150/240, computes
the maximum coverable lead day per catalogue timezone from the 240 h
ceiling and the 07:00-local window ends, and estimates the byte volume
of the adapter's wanted messages across all steps.

Gate outcomes are data in the JSON (an arm stop is a finding, not a
crash). Exit code 0 always.

Usage: python audit_serving_sources_extended.py --date 20261031 \
           --cycle 12 --registry ../../data/tracking/registry.json \
           --out-json phase0_audit_extended.json \
           --out-md phase0_audit_extended.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from audit_serving_sources import (  # noqa: E402
    ECMWF_REQUIRED_PL,
    ECMWF_REQUIRED_PL_OPTIONAL,
    ECMWF_REQUIRED_SFC,
    ECMWF_REQUIRED_SFC_OPTIONAL,
)
from slr_calibration.model_serving import (  # noqa: E402
    fetch_ranges,
    http_get,
    load_catalogue,
    window_ends,
)

# The exact subset the serving adapter samples (run_model_serving.py).
ADAPTER_PL_PARAMS = ["t", "q", "r", "u", "v", "w", "gh"]
ADAPTER_PL_LEVELS = [1000, 925, 850, 700, 600, 500]
ADAPTER_SFC_PARAMS = ["2t", "2d", "10u", "10v", "skt", "tp", "sf", "cp", "tclw"]

ECMWF_STEPS = list(range(0, 145, 3)) + list(range(150, 241, 6))
CEILING_STEP = 240


def base_url(date: str, cycle: int) -> str:
    return f"https://data.ecmwf.int/forecasts/{date}/{cycle:02d}z/ifs/0p25/oper"


def fetch_index(date: str, cycle: int, step: int) -> tuple[list[dict] | None, str | None]:
    name = f"{date}{cycle:02d}0000-{step}h-oper-fc"
    try:
        text = http_get(f"{base_url(date, cycle)}/{name}.index").decode()
    except Exception as exc:  # noqa: BLE001 - audit records failures as data
        return None, str(exc)
    entries = [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]
    return entries, None


def analyse(entries: list[dict]) -> dict:
    pl_levels: dict[str, set[int]] = {}
    sfc_params: set[str] = set()
    wanted_bytes = 0
    for e in entries:
        param, levtype = e.get("param"), e.get("levtype")
        if param is None:
            continue
        if levtype == "pl":
            try:
                level = int(float(e.get("levelist")))
            except (TypeError, ValueError):
                continue
            pl_levels.setdefault(param, set()).add(level)
            if param in ADAPTER_PL_PARAMS and level in ADAPTER_PL_LEVELS:
                wanted_bytes += int(e["_length"])
        elif levtype == "sfc":
            sfc_params.add(param)
            if param in ADAPTER_SFC_PARAMS:
                wanted_bytes += int(e["_length"])
    missing_pl = [p for p in ADAPTER_PL_PARAMS if p not in pl_levels]
    missing_levels = {
        p: [lvl for lvl in ADAPTER_PL_LEVELS if lvl not in pl_levels.get(p, set())]
        for p in ADAPTER_PL_PARAMS
        if p in pl_levels and any(lvl not in pl_levels[p] for lvl in ADAPTER_PL_LEVELS)
    }
    missing_sfc = [p for p in ADAPTER_SFC_PARAMS if p not in sfc_params]
    phase0_missing_pl = [p for p in ECMWF_REQUIRED_PL if p not in pl_levels]
    phase0_missing_sfc = [p for p in ECMWF_REQUIRED_SFC if p not in sfc_params]
    return {
        "messages": len(entries),
        "pl_levels": {k: sorted(v) for k, v in sorted(pl_levels.items())},
        "sfc_params": sorted(sfc_params),
        "adapter_missing_pl": missing_pl,
        "adapter_missing_levels": missing_levels,
        "adapter_missing_sfc": missing_sfc,
        "phase0_missing_pl": phase0_missing_pl,
        "phase0_missing_sfc": phase0_missing_sfc,
        "optional_missing": [p for p in ECMWF_REQUIRED_PL_OPTIONAL if p not in pl_levels]
        + [p for p in ECMWF_REQUIRED_SFC_OPTIONAL if p not in sfc_params],
        "adapter_wanted_bytes": wanted_bytes,
        "complete": not missing_pl and not missing_levels and not missing_sfc,
    }


def accumulation_semantics(date: str, cycle: int, step: int, params: list[str]) -> dict:
    """Read step-type keys off the actual GRIB messages (byte-range)."""
    entries, err = fetch_index(date, cycle, step)
    if entries is None:
        return {"step": step, "error": err}
    name = f"{date}{cycle:02d}0000-{step}h-oper-fc.grib2"
    url = f"{base_url(date, cycle)}/{name}"
    wanted = {}
    for e in entries:
        if e.get("levtype") == "sfc" and e.get("param") in params:
            wanted[e["param"]] = (int(e["_offset"]), int(e["_length"]))
    out: dict = {"step": step, "params": {}}
    if not wanted:
        out["error"] = "no requested surface params in index"
        return out
    payloads = fetch_ranges(url, list(wanted.values()))
    import eccodes

    for param, (off, _length) in wanted.items():
        gid = eccodes.codes_new_from_message(payloads[off])
        keys: dict[str, object] = {}
        for key in ("stepType", "typeOfStatisticalProcessing", "startStep", "endStep", "stepRange"):
            try:
                keys[key] = eccodes.codes_get(gid, key)
            except Exception:  # noqa: BLE001 - key may not exist
                keys[key] = None
        eccodes.codes_release(gid)
        out["params"][param] = keys
    return out


def probe_cycle(date: str, cycle: int, steps: list[int]) -> dict:
    out: dict = {"date": date, "cycle_z": cycle, "steps": {}}
    for step in steps:
        entries, err = fetch_index(date, cycle, step)
        if entries is None:
            out["steps"][str(step)] = {"present": False, "error": err}
        else:
            a = analyse(entries)
            out["steps"][str(step)] = {
                "present": True, "messages": a["messages"], "complete": a["complete"],
                "adapter_missing_pl": a["adapter_missing_pl"],
                "adapter_missing_levels": a["adapter_missing_levels"],
                "adapter_missing_sfc": a["adapter_missing_sfc"],
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261031")
    ap.add_argument("--cycle", type=int, default=12)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--probe-date", default="20261101")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args()

    cycle_dt = datetime.strptime(args.date, "%Y%m%d").replace(
        hour=args.cycle, tzinfo=timezone.utc)

    steps_detail: dict[str, dict] = {}
    for step in ECMWF_STEPS:
        entries, err = fetch_index(args.date, args.cycle, step)
        if entries is None:
            steps_detail[str(step)] = {"present": False, "error": err, "complete": False}
        else:
            a = analyse(entries)
            a["present"] = True
            steps_detail[str(step)] = a
        print(f"step {step}: {steps_detail[str(step)].get('messages', 'MISSING')} messages, "
              f"complete={steps_detail[str(step)]['complete']}", flush=True)

    def range_summary(steps: list[int]) -> dict:
        sel = [steps_detail[str(s)] for s in steps]
        return {
            "steps": steps,
            "all_present": all(s.get("present") for s in sel),
            "all_complete": all(s.get("complete") for s in sel),
            "incomplete_steps": {
                str(st): {k: s.get(k) for k in
                          ("adapter_missing_pl", "adapter_missing_levels", "adapter_missing_sfc", "error")}
                for st, s in zip(steps, sel) if not s.get("complete")
            },
            "tclw_present_all_steps": all(
                "tclw" not in (s.get("adapter_missing_sfc") or []) and s.get("present")
                for s in sel),
        }

    range_a = range_summary([s for s in ECMWF_STEPS if s <= 144])
    range_b = range_summary([s for s in ECMWF_STEPS if s >= 150])
    last_contiguous = -1
    for s in ECMWF_STEPS:
        if steps_detail[str(s)].get("complete"):
            last_contiguous = s
        else:
            break
    total_wanted = sum(s.get("adapter_wanted_bytes", 0) for s in steps_detail.values())

    accum = [accumulation_semantics(args.date, args.cycle, step, ["tp", "sf", "cp"])
             for step in (144, 150, 240)]

    # Per-timezone coverable lead days from the 240 h ceiling.
    points = load_catalogue(Path(args.registry))
    ceilings: dict[str, dict] = {}
    for tz_name in sorted({p["timezone"] for p in points}):
        ends = window_ends(cycle_dt, tz_name, range(1, 13))
        per_lead = {}
        max_lead = 0
        for k, end in zip(range(1, 13), ends):
            step_needed = (end - cycle_dt).total_seconds() / 3600.0
            per_lead[str(k)] = {"window_end_utc": end.isoformat(), "end_step": step_needed,
                                "within_ceiling": step_needed <= CEILING_STEP}
            if step_needed <= CEILING_STEP:
                max_lead = k
        ceilings[tz_name] = {"max_lead_day_full_ceiling": max_lead, "per_lead": per_lead}
    # If coverage stops early, recompute ceilings against the last complete step.
    if last_contiguous < CEILING_STEP:
        for tz_name, c in ceilings.items():
            ends = window_ends(cycle_dt, tz_name, range(1, 13))
            c["max_lead_day_last_complete_step"] = max(
                [k for k, end in zip(range(1, 13), ends)
                 if (end - cycle_dt).total_seconds() / 3600.0 <= last_contiguous] or [0])

    result = {
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "cycle": {"date": args.date, "hour_z": args.cycle,
                  "base": base_url(args.date, args.cycle)},
        "adapter_requirements": {
            "pl_params": ADAPTER_PL_PARAMS, "pl_levels": ADAPTER_PL_LEVELS,
            "sfc_params": ADAPTER_SFC_PARAMS,
            "note": "adapter subset from run_model_serving.py; Phase-0 requirement "
                    "status (audit_serving_sources constants) recorded per step too",
        },
        "step_schedule": {"steps": ECMWF_STEPS,
                          "range_a_0_144_3hourly": [s for s in ECMWF_STEPS if s <= 144],
                          "range_b_150_240_6hourly": [s for s in ECMWF_STEPS if s >= 150]},
        "steps": steps_detail,
        "range_a": range_a,
        "range_b": range_b,
        "last_contiguous_complete_step": last_contiguous,
        "full_ceiling_complete": last_contiguous >= CEILING_STEP,
        "accumulation_semantics": accum,
        "adapter_wanted_bytes_all_steps": total_wanted,
        "ceilings_by_timezone": ceilings,
        "other_cycles": {
            "next_00z_probe": probe_cycle(args.probe_date, 0, [0, 240]),
            "same_day_00z_parity": probe_cycle(args.date, 0, [150, 240]),
        },
        "gate": "PASS" if last_contiguous >= CEILING_STEP else "PARTIAL",
    }
    Path(args.out_json).write_text(json.dumps(result, indent=1, default=str))

    # ---- markdown summary ----
    lines = []
    lines.append(f"# Extended Phase-0 audit — ECMWF availability to step 240")
    lines.append("")
    lines.append(f"- Audited (UTC): {result['audited_at_utc']}")
    lines.append(f"- Cycle: {args.date} {args.cycle:02d}Z, IFS oper 0.25° open data "
                 f"(`{result['cycle']['base']}`)")
    lines.append(f"- Gate: **{result['gate']}** — last contiguous complete step "
                 f"{last_contiguous} of {CEILING_STEP}")
    lines.append(f"- Adapter requirement set (from run_model_serving.py): PL "
                 f"{', '.join(ADAPTER_PL_PARAMS)} on levels "
                 f"{'/'.join(str(x) for x in ADAPTER_PL_LEVELS)} hPa; SFC "
                 f"{', '.join(ADAPTER_SFC_PARAMS)} (lsp derived as tp − cp, as sanctioned)")
    lines.append("")
    lines.append("## Step ranges")
    lines.append("")
    lines.append(f"- Steps 0–144 (3-hourly, {len(range_a['steps'])} files): "
                 f"all present = {range_a['all_present']}, all complete = {range_a['all_complete']}, "
                 f"tclw at every step = {range_a['tclw_present_all_steps']}")
    lines.append(f"- Steps 150–240 (6-hourly, {len(range_b['steps'])} files): "
                 f"all present = {range_b['all_present']}, all complete = {range_b['all_complete']}, "
                 f"tclw at every step = {range_b['tclw_present_all_steps']}")
    for label, rng in (("0–144", range_a), ("150–240", range_b)):
        if rng["incomplete_steps"]:
            lines.append(f"- Incomplete steps in {label}: {json.dumps(rng['incomplete_steps'])}")
    lines.append("")
    lines.append("## Accumulation semantics (from the GRIB messages)")
    lines.append("")
    for a in accum:
        if "params" in a:
            desc = "; ".join(
                f"{p}: stepType={v.get('stepType')}, startStep={v.get('startStep')}, "
                f"endStep={v.get('endStep')}"
                for p, v in a["params"].items())
            lines.append(f"- Step {a['step']}: {desc}")
        else:
            lines.append(f"- Step {a['step']}: ERROR {a.get('error')}")
    lines.append("")
    lines.append("## Coverable lead days per catalogue timezone (240 h ceiling, 07:00-local ends)")
    lines.append("")
    for tz_name, c in ceilings.items():
        extra = (f"; against last complete step {last_contiguous}: "
                 f"{c.get('max_lead_day_last_complete_step')}"
                 if last_contiguous < CEILING_STEP else "")
        lines.append(f"- {tz_name}: max lead day {c['max_lead_day_full_ceiling']}{extra}")
    lines.append("")
    lines.append(f"- Adapter download estimate (wanted messages, all 65 steps): "
                 f"{total_wanted / 1e6:.1f} MB")
    lines.append(f"- Next 00Z cycle probe ({args.probe_date} 00Z): "
                 f"{json.dumps(result['other_cycles']['next_00z_probe']['steps'])}")
    lines.append(f"- Same-day 00Z parity (steps 150/240 complete): "
                 f"{json.dumps({k: v.get('complete') for k, v in result['other_cycles']['same_day_00z_parity']['steps'].items()})}")
    lines.append("")
    Path(args.out_md).write_text("\n".join(lines))

    print(json.dumps({
        "gate": result["gate"],
        "last_contiguous_complete_step": last_contiguous,
        "range_a_complete": range_a["all_complete"],
        "range_b_complete": range_b["all_complete"],
        "adapter_wanted_MB": round(total_wanted / 1e6, 1),
        "ceilings": {k: v["max_lead_day_full_ceiling"] for k, v in ceilings.items()},
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
