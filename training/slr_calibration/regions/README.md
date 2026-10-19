# Station climate-regime map (region-first SLR build)

`station_regimes.csv` assigns every SNOTEL station in `data/hrrr-dataset/stations.csv`
to a named mountain range, one of the design's three climate regimes, and a
candidate training region. It is generated deterministically by
`slr_calibration.regions.build_station_regimes` from station state +
coordinates; the `assignment_basis` column records which rule fired for each
station so the map is auditable without reading the code.

## Rules (design section 4.2)

Three regimes only: `continental_mountain`, `intermountain`,
`maritime_mountain`. The trainer's hierarchy consumes the regime at its
climate level as the station's assigned value.

- **Colorado / Wyoming / Montana / New Mexico** — `continental_mountain`;
  regions are the per-state footprints of design section 4.3
  (`colorado-ranges`, `wyoming-ranges`, `montana-ranges`, `southwest` shares
  New Mexico with Arizona).
- **Utah / Idaho / Nevada** — `intermountain`; regions `utah-wasatch-uinta`,
  `idaho-ranges`, `nevada`.
- **Arizona** — `intermountain` (design section 4.2 does not list it; judged
  closer to the interior Southwest than to maritime ranges); region
  `southwest` with New Mexico.
- **Washington / Oregon / California** — `maritime_mountain` by default,
  with interior exceptions assigned by range from coordinates:
  - WA east of the Cascade crest (lon > -119.0): Blue Mountains (far
    southeast, intermountain) and Kettle–Selkirk–Okanogan (intermountain);
    Olympics (lon <= -123.0) are maritime.
  - OR east (lon >= -118.6): Wallowa–Blue Mountains, intermountain;
    Siskiyou and the southern Cascades are maritime.
  - CA far northeast (lat >= 41.3): Warner Mountains, intermountain; the
    station still trains in `sierra-nevada` because design section 4.3
    fixes the region as the California Sierra footprint.
- **South Dakota** (Black Hills): `continental_mountain`, no training
  region — 32 eligible events cannot meet the support gates.
- **Alaska**: `unclassified`, no training region (outside the design's
  region list; the dataset carries no Alaska stations).

Regions are state footprints (section 4.3); the WA/OR stations additionally
train in the merged `cascades-merged` variant, and the evaluation report
records which Cascades form the gates retained.
