# Resort-report verification — 2026-11-17

Resort-reported 24 h snowfall (SnoCountry; midpoint of the reported range, cm) against the ENS-EB and Kuchera forecasts of record — the last cycle issued before each window began. Errors are reported minus forecast: positive bias = resorts reported more than forecast. Alignment rule and caveats: `slr_calibration/resort_reports.py`.

Matched resort-days: **4** · comparison rows: 18 · unmatched reports: 125

## Overall (all points)

| Arm | Method | n | Bias (cm) | MAE (cm) |
|---|---|---|---|---|
| ecmwf | ENS-EB | 12 | -1.6 | 3.0 |
| ecmwf | Kuchera | 12 | -0.9 | 2.9 |
| hrrr | ENS-EB | 6 | -5.8 | 5.8 |
| hrrr | Kuchera | 3 | -11.7 | 11.7 |

## By point role (ECMWF arm)

| Role | Method | n | Bias (cm) | MAE (cm) |
|---|---|---|---|---|
| base | ENS-EB | 4 | -0.6 | 2.6 |
| base | Kuchera | 4 | 1.0 | 2.6 |
| mid_mountain | ENS-EB | 4 | -1.6 | 2.9 |
| mid_mountain | Kuchera | 4 | -1.1 | 2.6 |
| summit | ENS-EB | 4 | -2.7 | 3.4 |
| summit | Kuchera | 4 | -2.7 | 3.4 |

## By resort (ECMWF arm, mid-mountain point)

| Resort | Days | ENS-EB bias | ENS-EB MAE | Kuchera bias | Kuchera MAE |
|---|---|---|---|---|---|
| Copper Mountain | 1 | 0.0 | 0.0 | 0.0 | 0.0 |
| Kicking Horse | 1 | -2.8 | 2.8 | -1.5 | 1.5 |
| Lake Louise | 1 | 2.5 | 2.5 | 3.1 | 3.1 |
| Mt. Bachelor | 1 | -6.3 | 6.3 | -5.9 | 5.9 |

## Detail — matched resort-days (ECMWF arm)

| Window end | Resort | Reported (cm) | ENS-EB base/mid/summit | Kuchera base/mid/summit |
|---|---|---|---|---|
| 2026-11-15 | Mt. Bachelor | 0.0 (0.0–0.0) | 4.9/6.3/7.3 | 3.2/5.9/8.1 |
| 2026-11-16 | Copper Mountain | 0.0 (0.0–0.0) | -0.0/-0.0/-0.0 | -0.0/-0.0/-0.0 |
| 2026-11-16 | Kicking Horse | 5.1 (5.1–5.1) | 6.5/7.8/10.0 | 3.0/6.5/9.0 |
| 2026-11-16 | Lake Louise | 10.2 (10.2–10.2) | 6.0/7.7/8.7 | 5.0/7.1/8.8 |

## Unmatched reports

| Window end | Resort | Reason | Operating status |
|---|---|---|---|
| 2026-11-09 | Big Boulder | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Blue Mountain (PA) | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Camelback | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Hidden Valley (PA) | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Jack Frost | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Laurel Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Liberty Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Pico Mountain | no 24 h snow value reported | Plan to Open 12/11 |
| 2026-11-09 | Roundtop | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Seven Springs | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-09 | Whitetail | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Afton Alps | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Alpine Valley (OH) | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Alyeska Resort | no 24 h snow value reported | Plan to Open 11/28 |
| 2026-11-10 | Boston Mills Brandywine | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Boyne Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Buck Hill | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Devil's Head | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Granite Peak | no 24 h snow value reported | Plan to Open 11/25 |
| 2026-11-10 | Hidden Valley (MO) | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Lutsen Mountains | no 24 h snow value reported | Plan to Open 11/26 |
| 2026-11-10 | Mad River Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Mt. Brighton | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Paoli Peaks | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Snow Creek | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-10 | Snowriver | no 24 h snow value reported | Plan to Open 11/26 |
| 2026-11-10 | The Highlands | no 24 h snow value reported | Plan to Open 12/11 |
| 2026-11-10 | Wilmot Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Alpental | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Bear Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Blue Mountain (ON) | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Crystal Mountain | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-11 | Cypress Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Fernie | no 24 h snow value reported | Plan to Open 12/04 |
| 2026-11-11 | Grouse Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | June Mountain | no 24 h snow value reported | Plan to Open 12/19 |
| 2026-11-11 | Kimberley | no 24 h snow value reported | Plan to Open 12/11 |
| 2026-11-11 | Kirkwood | no 24 h snow value reported | Plan to Open 12/04 |
| 2026-11-11 | Le Massif de Charlevoix | no 24 h snow value reported | Plan to Open 12/05 |
| 2026-11-11 | Marmot Basin | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Mont-Sainte-Anne | no 24 h snow value reported | Plan to Open 12/05 |
| 2026-11-11 | Mt. Baker | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Mt. Hood Meadows | no 24 h snow value reported | Plan to Open 12/04 |
| 2026-11-11 | Palisades Tahoe | no 24 h snow value reported | Plan to Open 11/25 |
| 2026-11-11 | Panorama | no 24 h snow value reported | Plan to Open 12/04 |
| 2026-11-11 | RED Mountain | no 24 h snow value reported | Plan to Open 12/12 |
| 2026-11-11 | Revelstoke | no 24 h snow value reported | Plan to Open 12/05 |
| 2026-11-11 | Schweitzer | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-11 | SilverStar | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-11 | Snow Summit | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Snow Valley | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Snowshoe | no 24 h snow value reported | Plan to Open 11/25 |
| 2026-11-11 | Stevens Pass | no 24 h snow value reported | Plan to Open 12/04 |
| 2026-11-11 | Stoneham | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Sugar Bowl | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-11 | Sun Peaks | no 24 h snow value reported | Plan to Open 11/21 |
| 2026-11-11 | Sun Valley | no 24 h snow value reported | Plan to Open 11/26 |
| 2026-11-11 | Tamarack Resort | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | The Summit at Snoqualmie | no 24 h snow value reported | Plan to Open 12/16 |
| 2026-11-11 | Timberline | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-11 | Tremblant | no 24 h snow value reported | Plan to Open 11/26 |
| 2026-11-11 | Whistler Blackcomb | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-14 | Banff Sunshine | no 24 h snow value reported |  |
| 2026-11-14 | Mt. Norquay | no 24 h snow value reported |  |
| 2026-11-14 | Nakiska | no 24 h snow value reported |  |
| 2026-11-14 | Northstar California | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-14 | Sierra-at-Tahoe | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-15 | Heavenly | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-15 | Mount Rose | no 24 h snow value reported |  |
| 2026-11-15 | Wolf Creek | no 24 h snow value reported |  |
| 2026-11-16 | Alta | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-16 | Arapahoe Basin | no 24 h snow value reported |  |
| 2026-11-16 | Aspen | no 24 h snow value reported | Plan to Open 11/26 |
| 2026-11-16 | Aspen Highlands | no 24 h snow value reported | Plan to Open 12/12 |
| 2026-11-16 | Attitash | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Beaver Creek | no 24 h snow value reported | Plan to Open 11/25 |
| 2026-11-16 | Big Sky | no 24 h snow value reported | Plan to Open 11/25 |
| 2026-11-16 | Breckenridge | no 24 h snow value reported |  |
| 2026-11-16 | Bridger Bowl | no 24 h snow value reported | Plan to Open 12/11 |
| 2026-11-16 | Brighton | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Buttermilk | no 24 h snow value reported | Plan to Open 12/12 |
| 2026-11-16 | Cannon Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Cranmore | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-16 | Crested Butte | no 24 h snow value reported | Plan to Open 11/25 |
| 2026-11-16 | Crotched Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Deer Valley | no 24 h snow value reported | Plan to Open 12/05 |
| 2026-11-16 | Eldora | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-16 | Grand Targhee | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-16 | Hunter Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Jackson Hole | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-16 | Jay Peak | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-16 | Jiminy Peak | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Keystone | no 24 h snow value reported |  |
| 2026-11-16 | Killington | no 24 h snow value reported | Plan to Open 11/17 |
| 2026-11-16 | Loon Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Loveland | no 24 h snow value reported |  |
| 2026-11-16 | Mad River Glen | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Mammoth Mountain | no 24 h snow value reported |  |
| 2026-11-16 | Monarch Mountain | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Mount Snow | no 24 h snow value reported | Plan to Open 11/21 |
| 2026-11-16 | Mount Sunapee | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Okemo | no 24 h snow value reported | Plan to Open 11/21 |
| 2026-11-16 | Park City | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-16 | Powder Mountain | no 24 h snow value reported | Plan to Open 12/11 |
| 2026-11-16 | Purgatory | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-16 | Saddleback | no 24 h snow value reported | Plan to Open 12/04 |
| 2026-11-16 | Ski Butternut | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Snowbasin | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-16 | Snowbird | no 24 h snow value reported | Plan to Open 11/27 |
| 2026-11-16 | Snowmass | no 24 h snow value reported | Plan to Open 11/26 |
| 2026-11-16 | Solitude | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-16 | Steamboat | no 24 h snow value reported | Plan to Open 11/21 |
| 2026-11-16 | Stowe | no 24 h snow value reported | Plan to Open 11/21 |
| 2026-11-16 | Stratton | no 24 h snow value reported | Plan to Open 11/25 |
| 2026-11-16 | Sugarbush | no 24 h snow value reported | Plan to Open 11/21 |
| 2026-11-16 | Sugarloaf | no 24 h snow value reported | Plan to Open 11/20 |
| 2026-11-16 | Sunday River | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Taos Ski Valley | no 24 h snow value reported | Plan to Open 11/26 |
| 2026-11-16 | Telluride | no 24 h snow value reported | Plan to Open 11/26 |
| 2026-11-16 | Vail | no 24 h snow value reported |  |
| 2026-11-16 | Whiteface | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Whitefish | no 24 h snow value reported | Plan to Open 12/04 |
| 2026-11-16 | Wild Mountain | no 24 h snow value reported |  |
| 2026-11-16 | Wildcat | no 24 h snow value reported | Opening Soon for Snow Sports |
| 2026-11-16 | Winter Park | no 24 h snow value reported |  |
