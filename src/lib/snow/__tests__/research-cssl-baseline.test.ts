import { describe, expect, it } from 'vitest';
import { evaluateCsslKucheraBaseline, evaluateJsonLines, evaluateResearchKucheraBaseline } from '../../../../training/slr_calibration/scripts/cssl-baseline';

function event() {
  return {
    event_id: 'CSSL-2024-03-02',
    storm_group_id: 'CSSL-STORM-0001',
    station_id: 'CSSL',
    site_id: 'CSSL',
    valid_time: '2024-03-02T16:00:00-08:00',
    latitude: 39.3257,
    longitude: -120.3681,
    elevation_m: 2100,
    observed_slr: 9.05172414,
    climate_regime: 'maritime_mountain',
    qc_weight: 1,
    profileSource: 'synthetic profile fixture',
    profileModel: 'synthetic',
    profileAlignment: 'test',
    source_license: 'CC0-1.0',
    source_doi: '10.6078/D1941T',
    hourly: [
      {
        time: '2024-03-02T11:00:00Z', localPstDate: '2024-03-02', temperature2mC: -4, precipitationMm: 0, snowfallCm: 0,
        levels: [
          { pressureHpa: 900, temperatureC: -3, geopotentialHeightM: 2200 },
          { pressureHpa: 850, temperatureC: -6, geopotentialHeightM: 2700 },
          { pressureHpa: 800, temperatureC: -9, geopotentialHeightM: 3200 },
          { pressureHpa: 700, temperatureC: -15, geopotentialHeightM: 4200 },
        ],
      },
      {
        time: '2024-03-02T12:00:00Z', localPstDate: '2024-03-02', temperature2mC: -4, precipitationMm: 1, snowfallCm: 1,
        levels: [
          { pressureHpa: 900, temperatureC: -3, geopotentialHeightM: 2200 },
          { pressureHpa: 850, temperatureC: -6, geopotentialHeightM: 2700 },
          { pressureHpa: 800, temperatureC: -9, geopotentialHeightM: 3200 },
          { pressureHpa: 700, temperatureC: -15, geopotentialHeightM: 4200 },
        ],
      },
      {
        time: '2024-03-02T13:00:00Z', localPstDate: '2024-03-02', temperature2mC: -6, precipitationMm: 3, snowfallCm: 3,
        levels: [
          { pressureHpa: 925, temperatureC: 2, geopotentialHeightM: 1900 },
          { pressureHpa: 900, temperatureC: -5, geopotentialHeightM: 2250 },
          { pressureHpa: 850, temperatureC: -8, geopotentialHeightM: 2750 },
          { pressureHpa: 800, temperatureC: -11, geopotentialHeightM: 3250 },
          { pressureHpa: 700, temperatureC: -17, geopotentialHeightM: 4250 },
        ],
      },
    ],
  };
}

describe('CSSL exact MySnow Kuchera research bridge', () => {
  it('uses production interval-mean profiles and precipitation-weighted baselines', () => {
    const result = evaluateCsslKucheraBaseline(event());
    expect(result.rejectionReason).toBeNull();
    expect(result.row?.baseline_method).toBe('kuchera');
    expect(result.row?.baseline_slr).toBe(13.75);
    expect(result.row?.evaluated_profile_hours).toBe(2);
  });

  it('uses an explicit UTC observation window for multi-station GHCN events', () => {
    const input = event();
    const result = evaluateResearchKucheraBaseline({
      ...input,
      windowStartUtc: '2024-03-02T11:30:00Z',
      windowEndUtc: '2024-03-02T12:30:00Z',
    });
    expect(result.rejectionReason).toBeNull();
    expect(result.row?.evaluated_profile_hours).toBe(1);
    expect(result.row?.baseline_slr).toBe(13);
  });

  it('is deterministic and explicitly does not enable calibration', () => {
    const line = JSON.stringify(event());
    const first = evaluateJsonLines(`${line}\n`);
    const second = evaluateJsonLines(`${line}\n`);
    expect(first).toEqual(second);
    expect(first.report.calibrationEnabled).toBe(false);
  });
});
