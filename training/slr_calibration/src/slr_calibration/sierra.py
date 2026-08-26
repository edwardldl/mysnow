from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Audited Sierra Nevada / Tahoe / eastern-Sierra stations only.  The whitelist is
# intentionally narrower than a CA/NV bounding box and excludes Reno, Sparks,
# Carson City, Dayton, Susanville, and Mount Shasta-area leakage.
SIERRA_STATIONS: dict[str, tuple[float, float, float, str, str]] = {
    "US1CAAM0003": (38.4986, -120.5364, 1115.3, "CA", "VOLCANO 6.4 NE"),
    "US1CAAM0005": (38.5054, -120.4993, 1241.5, "CA", "PIONEER 6.4 NE"),
    "US1CACV0006": (38.2423, -120.3118, 1230.2, "CA", "ARNOLD 3.2 ENE"),
    "US1CACV0012": (38.2538, -120.3604, 1226.2, "CA", "ARNOLD 1.6 NNE"),
    "US1CACV0014": (38.2340, -120.3590, 1178.1, "CA", "ARNOLD 0.6 ENE"),
    "US1CACV0015": (38.2317, -120.3699, 1180.5, "CA", "ARNOLD 0.0 S"),
    "US1CAED0011": (38.7570, -120.5775, 1187.2, "CA", "POLLOCK PINES 0.5 NW"),
    "US1CAED0018": (38.7575, -120.6046, 1173.2, "CA", "POLLOCK PINES 1.9 WNW"),
    "US1CAED0020": (38.8817, -120.0339, 1927.9, "CA", "SOUTH LAKE TAHOE 4.9 SW"),
    "US1CAED0035": (38.9300, -119.9575, 1919.6, "CA", "SOUTH LAKE TAHOE 1.4 ESE"),
    "US1CAIN0002": (37.3227, -118.5404, 1829.4, "CA", "BISHOP 8.5 WSW"),
    "US1CAMA0001": (37.4415, -119.6316, 1302.7, "CA", "OAKHURST 7.5 N"),
    "US1CAMN0001": (38.0337, -119.1697, 2162.3, "CA", "LEE VINING 5.9 NW"),
    "US1CAMN0004": (37.5103, -118.6363, 1949.2, "CA", "WEST BISHOP 14.4 NW"),
    "US1CAMN0005": (37.5569, -118.7054, 2154.3, "CA", "MAMMOTH LAKES 16.3 ESE"),
    "US1CAMN0006": (38.0197, -119.1660, 2069.9, "CA", "MONO CITY 1.9 SW"),
    "US1CANV0003": (39.3174, -120.4517, 1839.5, "CA", "KINGVALE 1.3 WSW"),
    "US1CANV0051": (39.3498, -120.1993, 1977.2, "CA", "TRUCKEE 1.6 N"),
    "US1CAPC0001": (39.3021, -120.3837, 2099.2, "CA", "SODA SPRINGS 1.5 SSW"),
    "US1CAPM0002": (39.9191, -121.1001, 1352.7, "CA", "MEADOW VALLEY 5.6 WSW"),
    "US1CAPM0003": (40.3114, -121.2249, 1381.4, "CA", "CHESTER 0.9 NNE"),
    "US1CAPM0009": (39.8179, -120.3158, 1511.5, "CA", "BECKWOURTH 3.3 E"),
    "US1CAPM0015": (40.2927, -121.2281, 1375.6, "CA", "CHESTER 0.6 SSE"),
    "US1CASI0002": (39.5780, -120.3830, 1558.1, "CA", "SIERRAVILLE 1.2 SW"),
    "US1NVDG0017": (38.9831, -119.9539, 1896.2, "NV", "ZEPHYR COVE-ROUND HILL VILLAGE"),
    "US1NVWH0121": (39.2427, -119.9393, 1921.5, "NV", "INCLINE VILLAGE 0.9 ESE"),
    "US1NVWH0161": (39.2492, -119.9607, 1951.6, "NV", "INCLINE VILLAGE-CRYSTAL BAY 1."),
    "USC00048760": (39.0600, -120.1292, 1924.8, "CA", "TAHOMA"),
}
EXPLICIT_EXCLUSIONS = ("RENO", "SPARKS", "CARSON CITY", "DAYTON", "SUSANVILLE", "MOUNT SHASTA")
PST = timezone(timedelta(hours=-8))

@dataclass(frozen=True)
class SierraImportConfig:
    min_snow_mm: float = 25.0
    min_wesf_mm: float = 2.5
    min_observed_slr: float = 2.0
    max_observed_slr: float = 50.0


SIERRA_STATION_REGIONS = {sid: ("eastern_sierra" if sid.startswith(("US1CAMN", "US1CAIN")) else "tahoe_high_country" if sid.startswith(("US1CAED", "US1NV")) or sid in {"US1CANV0003", "US1CANV0051", "US1CAPC0001", "USC00048760"} else "western_slope") for sid in SIERRA_STATIONS}


@dataclass(frozen=True)
class SierraReject:
    station_id: str
    observation_date: str
    reason: str
    detail: str


def _storm_groups(rows: list[dict[str, Any]]) -> None:
    ds=sorted({date.fromisoformat(str(r["observation_date"])) for r in rows}); start=None; prev=None; by={}
    for d in ds:
        if prev is None or (d-prev).days>1: start=d
        assert start is not None
        by[d]=f"sierra-{start.isoformat()}"; prev=d
    for r in rows:r["storm_group_id"]=by[date.fromisoformat(str(r["observation_date"]))]


def import_ghcn_sierra(raw_dir: Path, *, min_snow_mm: float=25.0, min_wesf_mm: float=2.5, min_observed_slr: float=2.0, max_observed_slr: float=50.0) -> tuple[list[dict[str,Any]],list[SierraReject],dict[str,Any]]:
    accepted=[]; rejected=[]; pair_count=0; files=[]
    for path in sorted(raw_dir.glob("*.csv")):
        station_id=path.stem
        if station_id not in SIERRA_STATIONS: continue
        files.append({"name":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
        rows: dict[str,dict[str,list[str]]]=defaultdict(dict)
        with path.open(newline="",encoding="utf-8") as h:
            for raw in csv.reader(h):
                if len(raw)>=8 and raw[0]==station_id and raw[2] in {"SNOW","WESF"}: rows[raw[1]][raw[2]]=raw
        lat,lon,elev,state,name=SIERRA_STATIONS[station_id]
        for day,pair in sorted(rows.items()):
            if set(pair)!={"SNOW","WESF"}: continue
            pair_count+=1; snow,wet=pair["SNOW"],pair["WESF"]
            def reject(reason:str,detail:str)->None: rejected.append(SierraReject(station_id,day,reason,detail))
            try: snow_mm=float(snow[3]); wesf_mm=float(wet[3])/10.0
            except ValueError: reject("INVALID_VALUE","non-numeric SNOW or WESF"); continue
            if snow[4] or wet[4]: reject("MEASUREMENT_FLAG",f"SNOW={snow[4]!r};WESF={wet[4]!r}"); continue
            if snow[5] or wet[5]: reject("QUALITY_FLAG",f"SNOW={snow[5]!r};WESF={wet[5]!r}"); continue
            if not math.isfinite(snow_mm) or not math.isfinite(wesf_mm) or snow_mm<=0 or wesf_mm<=0: reject("INVALID_VALUE","non-positive/non-finite pair"); continue
            if snow_mm<min_snow_mm: reject("INSUFFICIENT_NEW_SNOW",str(snow_mm)); continue
            if wesf_mm<min_wesf_mm: reject("INSUFFICIENT_FRESH_SWE",str(wesf_mm)); continue
            slr=snow_mm/wesf_mm
            if not min_observed_slr<slr<max_observed_slr: reject("SLR_OUT_OF_RANGE",f"{slr:.6f}"); continue
            # Exact 10:1 WESF from source N is a known circular/default pattern,
            # not independent water-equivalent evidence.
            if wet[6]=="N" and abs(slr-10.0)<1e-9: reject("SOURCE_N_EXACT_10_TO_1","possible derived/default WESF"); continue
            obs=snow[7].strip()
            if obs!=wet[7].strip(): reject("MISMATCHED_OBSERVATION_TIME",f"SNOW={obs};WESF={wet[7].strip()}"); continue
            if len(obs)!=4 or not obs.isdigit() or (obs != "2400" and (int(obs[:2])>23 or int(obs[2:])>59)):
                reject("INVALID_OBSERVATION_TIME",obs); continue
            d=datetime.strptime(day,"%Y%m%d").date()
            if obs == "2400":
                next_day = d + timedelta(days=1)
                end=datetime(next_day.year,next_day.month,next_day.day,0,0,tzinfo=PST)
            else:
                hh,mm=int(obs[:2]),int(obs[2:]); end=datetime(d.year,d.month,d.day,hh,mm,tzinfo=PST)
            accepted.append({
                "event_id":f"ghcn:{station_id}:{day}","storm_group_id":"","station_id":station_id,"site_id":station_id,
                "station_name":name,"state":state,"observation_date":d.isoformat(),"valid_time":end.isoformat(),
                "observation_time_local_standard":obs,"observation_window_hours":24,"latitude":lat,"longitude":lon,"elevation_m":elev,
                "fresh_snow_mm":snow_mm,"fresh_swe_mm":wesf_mm,"observed_slr":round(slr,6),"baseline_method":"kuchera",
                "climate_regime":"sierra_maritime_mountain","qc_passed":True,"qc_weight":1.0,"source":"NOAA GHCN-Daily direct same-date SNOW+WESF",
                "source_license":"NOAA/NCEI public-domain U.S. Government work","source_doi":"10.7289/V5D21VHZ","source_flag":f"SNOW:{snow[6]};WESF:{wet[6]}",
            })
    accepted.sort(key=lambda r:(r["observation_date"],r["station_id"])); _storm_groups(accepted)
    counts=Counter(r.reason for r in rejected)
    report={"source":"NOAA GHCN-Daily csv.gz/by_station","rawSameDatePairCount":pair_count,"acceptedEventCount":len(accepted),
        "rejectedEventCount":len(rejected),"stationCount":len({r["station_id"] for r in accepted}),
        "stationCounts":dict(sorted(Counter(r["station_id"] for r in accepted).items())),"rejectionCounts":dict(sorted(counts.items())),
        "dateRange":[accepted[0]["observation_date"],accepted[-1]["observation_date"]] if accepted else [],
        "mask":{"kind":"audited-whitelist","stationIds":sorted(SIERRA_STATIONS),"explicitlyExcludedPlaceNames":list(EXPLICIT_EXCLUSIONS),
        "note":"Sierra Nevada/Tahoe/eastern-Sierra mountain stations only; CSSL is absent."},
        "label":"same-row/date SNOW + WESF; blank measurement/quality flags; same valid time; 24h non-accumulated values only; never SNWD/WESD",
        "units":{"SNOW":"mm","WESF":"tenths of mm divided by 10"},"timezone":"fixed UTC-08:00 local standard time",
        "broadReconnaissance":{"positivePairCount":2193,"flagRejectCount":5,"thresholdPassEventCount":157,"stationCount":32,"dateRange":["2021-01-23","2026-02-20"],"elevationRangeM":[1193.9,2069.9]},
        "sourceFlags":dict(sorted(Counter(r["source_flag"] for r in accepted).items())),
        "repeatedAcceptedRatios":[{"ratio":ratio,"count":count} for ratio,count in sorted(Counter(r["observed_slr"] for r in accepted).items(),key=lambda item:(-item[1],item[0])) if count>1],
        "limitations":["NOAA notes WESF may be estimated, but by-station rows expose no distinct WESF estimation flag; available measurement, quality, source, date, and time fields are audited.","Repeated accepted ratios are reported rather than automatically discarded; source-N exact 10:1 is rejected."],
        "sourceFiles":files}
    report["rejectedPairCount"] = len(rejected)
    return accepted,rejected,report


def import_sierra_ghcn_directory(station_directory: Path, station_metadata: Path | None = None, config: SierraImportConfig = SierraImportConfig()):
    """Compatibility entry point; metadata is embedded in the audited whitelist.

    If a metadata path is provided it must exist, preserving a reproducible provenance check.
    """
    if station_metadata is not None:
        if not station_metadata.exists():
            raise FileNotFoundError(station_metadata)
        found: set[str] = set()
        for line in station_metadata.read_text(encoding="utf-8").splitlines():
            station_id = line[:11]
            if station_id not in SIERRA_STATIONS:
                continue
            found.add(station_id)
            latitude, longitude, elevation_m, state, _ = SIERRA_STATIONS[station_id]
            actual = (float(line[12:20]), float(line[21:30]), float(line[31:37]), line[38:40])
            expected = (latitude, longitude, elevation_m, state)
            if actual != expected:
                raise ValueError(f"Station metadata changed for {station_id}: {actual!r} != {expected!r}")
        missing = set(SIERRA_STATIONS) - found
        if missing:
            raise ValueError(f"Station metadata lacks audited Sierra stations: {sorted(missing)}")
    events, rejected, report = import_ghcn_sierra(station_directory, min_snow_mm=config.min_snow_mm, min_wesf_mm=config.min_wesf_mm, min_observed_slr=config.min_observed_slr, max_observed_slr=config.max_observed_slr)
    if station_metadata is not None:
        report["stationMetadata"] = {"name": station_metadata.name, "sha256": hashlib.sha256(station_metadata.read_bytes()).hexdigest()}
    return events, rejected, report


def write_sierra_import(events:list[dict[str,Any]],rejected:list[SierraReject],report:dict[str,Any],events_path:Path,rejections_path:Path,report_path:Path)->None:
    events_path.parent.mkdir(parents=True,exist_ok=True)
    if events:
        with events_path.open("w",newline="",encoding="utf-8") as h:
            w=csv.DictWriter(h,fieldnames=list(events[0]));w.writeheader();w.writerows(events)
    rejections_path.parent.mkdir(parents=True,exist_ok=True)
    with rejections_path.open("w",newline="",encoding="utf-8") as h:
        w=csv.DictWriter(h,fieldnames=list(SierraReject.__dataclass_fields__));w.writeheader();w.writerows(asdict(r) for r in rejected)
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
