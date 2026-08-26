import { latLngToCell } from 'h3-js';
import { describe, expect, it } from 'vitest';
import defaultArtifact from '../calibration/models/unvalidated-smoke-v1.json';
import csslResearchArtifact from '../../../../training/slr_calibration/artifacts/regional-slr-research-cssl-kuchera-v1.json';
import { applyRegionalSlrCalibration, validateRegionalSlrArtifact } from '../calibration/regional';
import type { RegionalSlrArtifact, RegionalSlrNode } from '../calibration/types';
import { estimateFreshSnowSlr } from '../slr';
import { calculateSnowfall } from '../snowfall';
import { profileFixtures } from './fixtures';

const LATITUDE = 39.19;
const LONGITUDE = -120.24;
const node = (logBias: number, effectiveSampleSize = 50): RegionalSlrNode => ({
  logBias,
  rawLogBias: logBias,
  residualStdLog: 0.12,
  rawSampleCount: 60,
  effectiveSampleSize,
  shrinkageWeight: 0.8,
});

function artifact(): RegionalSlrArtifact {
  const regionCell = latLngToCell(LATITUDE, LONGITUDE, 4);
  const climateCell = latLngToCell(LATITUDE, LONGITUDE, 2);
  return {
    schemaVersion: 1,
    artifactId: 'test-v1',
    artifactVersion: 'test-v1',
    status: 'validated',
    generatedAt: '2025-01-01T00:00:00Z',
    trainedThrough: '2025-01-01',
    modelKind: 'hierarchical-log-residual-eb',
    regionIndex: { system: 'h3', resolution: 4 },
    climateIndex: { system: 'h3', resolution: 2, assignments: { [climateCell]: 'maritime_mountain' } },
    bounds: { minCorrectionFactor: 0.5, maxCorrectionFactor: 2, minSlr: 3, maxSlr: 30 },
    training: {
      sourceLabel: 'test', sourceDigestSha256: '0'.repeat(64), acceptedEventCount: 100,
      rejectedEventCount: 0, rejectionCounts: {}, stationCount: 10,
      priorEffectiveSampleSize: 20, minEffectiveSampleSize: 5,
      leakageGroup: 'event_id', target: 'log(observed_slr / physical_baseline_slr)',
    },
    coverage: { qualityTier: 'regional', geographicNote: 'test', supportedMethods: ['kuchera', 'cobb_2011'] },
    methods: {
      kuchera: {
        global: node(Math.log(1.05)),
        climate: { maritime_mountain: node(Math.log(0.95)) },
        region: { [regionCell]: node(Math.log(0.9)) },
        local: { 'site-a': node(Math.log(1.1)) },
      },
    },
  };
}

describe('regional SLR calibration', () => {
  it('validates schema version and the committed smoke artifact', () => {
    expect(validateRegionalSlrArtifact(defaultArtifact)).toBe(true);
    expect(validateRegionalSlrArtifact({ ...artifact(), schemaVersion: 2 })).toBe(false);
  });

  it('applies the global → climate → region → local hierarchy', () => {
    const result = applyRegionalSlrCalibration(10, 'kuchera', {
      enabled: true,
      artifact: artifact(),
      context: { latitude: LATITUDE, longitude: LONGITUDE, siteId: 'site-a' },
    });
    expect(result.diagnostics.hierarchyChain).toEqual(['global', 'climate', 'region', 'local']);
    expect(result.diagnostics.highestSpecificity).toBe('local');
    expect(result.slr).toBeCloseTo(10 * 1.05 * 0.95 * 0.9 * 1.1, 6);
    expect(result.quantiles?.p10).toBeLessThan(result.slr);
    expect(result.quantiles?.p90).toBeGreaterThan(result.slr);
  });

  it('falls back when specific nodes have insufficient support', () => {
    const model = artifact();
    const regionCell = latLngToCell(LATITUDE, LONGITUDE, 4);
    model.methods.kuchera!.region[regionCell] = node(Math.log(1.8), 2);
    model.methods.kuchera!.local['site-a'] = node(Math.log(1.8), 1);
    const result = applyRegionalSlrCalibration(10, 'kuchera', {
      enabled: true,
      artifact: model,
      context: { latitude: LATITUDE, longitude: LONGITUDE, siteId: 'site-a' },
    });
    expect(result.diagnostics.hierarchyChain).toEqual(['global', 'climate']);
    expect(result.diagnostics.highestSpecificity).toBe('climate');
  });

  it('enforces correction and physical SLR bounds', () => {
    const model = artifact();
    model.methods.kuchera!.global = node(Math.log(20));
    model.methods.kuchera!.climate = {};
    model.methods.kuchera!.region = {};
    model.methods.kuchera!.local = {};
    const result = applyRegionalSlrCalibration(20, 'kuchera', {
      enabled: true,
      artifact: model,
      context: { latitude: LATITUDE, longitude: LONGITUDE },
    });
    expect(result.diagnostics.correctionFactor).toBeCloseTo(2);
    expect(result.slr).toBe(30);
  });

  it('fails closed for unvalidated artifacts unless a test explicitly opts in', () => {
    const model = { ...artifact(), status: 'unvalidated' as const };
    const result = applyRegionalSlrCalibration(11, 'kuchera', {
      enabled: true,
      artifact: model,
      context: { latitude: LATITUDE, longitude: LONGITUDE },
    });
    expect(result.slr).toBe(11);
    expect(result.diagnostics.reason).toBe('unvalidated_artifact');
  });

  it('validates but rejects the real CSSL research artifact in normal runtime', () => {
    expect(validateRegionalSlrArtifact(csslResearchArtifact)).toBe(true);
    const result = applyRegionalSlrCalibration(11, 'kuchera', {
      enabled: true,
      artifact: csslResearchArtifact,
      context: { latitude: 39.3257, longitude: -120.3681, siteId: 'CSSL' },
    });
    expect(result.slr).toBe(11);
    expect(result.diagnostics.reason).toBe('unvalidated_artifact');
  });

  it('returns identity for invalid artifacts, missing context, and unsupported methods', () => {
    const invalid = applyRegionalSlrCalibration(11, 'kuchera', {
      enabled: true,
      artifact: { ...artifact(), schemaVersion: 999 },
      context: { latitude: LATITUDE, longitude: LONGITUDE },
    });
    expect(invalid.slr).toBe(11);
    expect(invalid.diagnostics.reason).toBe('invalid_artifact');

    const missingContext = applyRegionalSlrCalibration(11, 'kuchera', {
      enabled: true,
      artifact: artifact(),
    });
    expect(missingContext.slr).toBe(11);
    expect(missingContext.diagnostics.reason).toBe('missing_context');

    const unsupported = applyRegionalSlrCalibration(11, 'fixed_10', {
      enabled: true,
      artifact: artifact(),
      context: { latitude: LATITUDE, longitude: LONGITUDE },
    });
    expect(unsupported.slr).toBe(11);
    expect(unsupported.diagnostics.reason).toBe('unsupported_method');
  });

  it('uses the Kuchera residual model when Cobb falls back to Kuchera', () => {
    const model = artifact();
    model.methods.cobb_2011 = {
      global: node(Math.log(1.9)), climate: {}, region: {}, local: {},
    };
    const result = estimateFreshSnowSlr(
      profileFixtures.coldSaturatedDgzNoLift,
      'cobb_2011',
      null,
      {
        enabled: true,
        artifact: model,
        context: { latitude: LATITUDE, longitude: LONGITUDE, siteId: 'site-a' },
      },
    );
    expect(result.diagnostics.sourceMethod).toBe('kuchera');
    expect(result.diagnostics.warnings).toContain('KUCHERA_FALLBACK');
    expect(result.diagnostics.calibration.correctionFactor).toBeCloseTo(1.05 * 0.95 * 0.9 * 1.1);
  });

  it('only changes SLR-derived depth when calibration is explicitly exercised', () => {
    const input = profileFixtures.coldSaturatedDgzStrongLift;
    const baseline = calculateSnowfall(input, 'kuchera');
    const calibrated = calculateSnowfall(input, 'kuchera', {
      enabled: true,
      artifact: artifact(),
      context: { latitude: LATITUDE, longitude: LONGITUDE, siteId: 'site-a' },
    });
    expect(calibrated.diagnostics.calibration.reason).toBe('applied');
    expect(calibrated.freshSlr).not.toBeCloseTo(baseline.freshSlr!);
    expect(calibrated.freshSnowCm).not.toBeCloseTo(baseline.freshSnowCm);
    expect(calibrated.precipitationMm).toBe(baseline.precipitationMm);
    expect(calibrated.snowFraction).toBe(baseline.snowFraction);
    expect(calibrated.frozenSweMm).toBe(baseline.frozenSweMm);
    expect(calibrated.rainMm).toBe(baseline.rainMm);
    expect(calibrated.phase).toEqual(baseline.phase);
    expect(calibrated.qpfAdjustment).toEqual(baseline.qpfAdjustment);
    expect(calibrated.qpfDistribution).toEqual(baseline.qpfDistribution);
  });

  it('preserves the current forecast result while the production gate is disabled', () => {
    const input = profileFixtures.coldSaturatedDgzStrongLift;
    const baseline = calculateSnowfall(input, 'kuchera');
    const explicitlyDisabled = calculateSnowfall(input, 'kuchera', {
      enabled: false,
      context: { latitude: LATITUDE, longitude: LONGITUDE, siteId: 'site-a' },
    });
    expect(explicitlyDisabled).toEqual(baseline);
    expect(baseline.diagnostics.calibration.reason).toBe('disabled');
  });
});
