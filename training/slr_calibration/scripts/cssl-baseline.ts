import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { intervalMean, intervalRepresentativeLayers } from '../../../src/lib/snow/atmosphere/interval';
import { normalizeProfile, type RawPressureLayer } from '../../../src/lib/snow/profile';
import { estimateFreshSnowSlr } from '../../../src/lib/snow/slr';
import type { SnowfallInput } from '../../../src/lib/snow/types';

interface ResearchHour {
  time: string;
  localPstDate?: string;
  temperature2mC: number | null;
  precipitationMm: number | null;
  snowfallCm: number | null;
  levels: RawPressureLayer[];
}

interface ResearchEvent extends Record<string, unknown> {
  event_id: string;
  storm_group_id: string;
  station_id: string;
  site_id: string;
  valid_time: string;
  latitude: string | number;
  longitude: string | number;
  elevation_m: string | number;
  observed_slr: string | number;
  climate_regime: string;
  qc_weight: string | number;
  hourly: ResearchHour[];
  windowStartUtc?: string;
  windowEndUtc?: string;
}


export interface BaselineEventResult {
  row: Record<string, string | number | boolean> | null;
  rejectionReason: string | null;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function evaluateResearchKucheraBaseline(event: ResearchEvent): BaselineEventResult {
  const stationElevationM = Number(event.elevation_m);
  const eventDate = String(event.valid_time).slice(0, 10);
  const windowStartMs = typeof event.windowStartUtc === 'string' ? Date.parse(event.windowStartUtc) : Number.NaN;
  const windowEndMs = typeof event.windowEndUtc === 'string' ? Date.parse(event.windowEndUtc) : Number.NaN;
  const usesExplicitWindow = Number.isFinite(windowStartMs) && Number.isFinite(windowEndMs);
  const hourly = [...event.hourly].sort((left, right) => left.time.localeCompare(right.time));
  const evaluated: Array<{ slr: number; weight: number }> = [];
  for (let index = 1; index < hourly.length; index += 1) {
    const previous = hourly[index - 1];
    const current = hourly[index];
    if (usesExplicitWindow) {
      const currentMs = Date.parse(current.time);
      if (!Number.isFinite(currentMs) || currentMs <= windowStartMs || currentMs > windowEndMs) continue;
    } else if (current.localPstDate && current.localPstDate !== eventDate) continue;
    const modeledSnowfall = finite(current.snowfallCm);
    if (modeledSnowfall === null || modeledSnowfall <= 0) continue;
    const precipitationWeight = finite(current.precipitationMm);
    const weight = precipitationWeight !== null && precipitationWeight > 0
      ? precipitationWeight
      : modeledSnowfall;
    const profile = normalizeProfile(intervalRepresentativeLayers(previous.levels, current.levels), {
      surfacePressureHpa: null,
      stationElevationM: Number.isFinite(stationElevationM) ? stationElevationM : null,
    });
    if (profile.validLevelCount < 1) continue;
    const input: SnowfallInput = {
      time: current.time,
      precipitationMm: precipitationWeight,
      snowfallCm: modeledSnowfall,
      snowfallWaterEquivalentMm: null,
      precipitationType: null,
      weatherCode: null,
      surface: {
        temperatureC: intervalMean(previous.temperature2mC, current.temperature2mC),
        dewPointC: null,
        relativeHumidityPct: null,
        wetBulbTemperatureC: null,
        windSpeedMs: null,
        windDirectionDeg: null,
        surfacePressureHpa: null,
        stationElevationM: Number.isFinite(stationElevationM) ? stationElevationM : null,
      },
      profile,
    };
    const estimate = estimateFreshSnowSlr(input, 'kuchera', null, { enabled: false });
    if (estimate.freshSlr !== null && Number.isFinite(estimate.freshSlr)) {
      evaluated.push({ slr: estimate.freshSlr, weight });
    }
  }
  if (evaluated.length === 0) return { row: null, rejectionReason: 'NO_VALID_KUCHERA_INTERVALS' };
  const totalWeight = evaluated.reduce((sum, item) => sum + item.weight, 0);
  if (totalWeight <= 0) return { row: null, rejectionReason: 'NO_POSITIVE_FROZEN_SWE_PROXY_WEIGHT' };
  const baselineSlr = evaluated.reduce((sum, item) => sum + item.slr * item.weight, 0) / totalWeight;
  return {
    row: {
      event_id: event.event_id,
      storm_group_id: event.storm_group_id,
      station_id: event.station_id,
      site_id: event.site_id,
      station_name: String(event.station_name || ''),
      state: String(event.state || ''),
      valid_time: event.valid_time,
      latitude: Number(event.latitude),
      longitude: Number(event.longitude),
      elevation_m: stationElevationM,
      observed_slr: Number(event.observed_slr),
      baseline_slr: Math.round(baselineSlr * 1_000_000) / 1_000_000,
      baseline_method: 'kuchera',
      climate_regime: event.climate_regime || 'maritime_mountain',
      qc_passed: true,
      qc_weight: Number(event.qc_weight || 1),
      evaluated_profile_hours: evaluated.length,
      model_frozen_swe_proxy_weight_mm: Math.round(totalWeight * 1_000_000) / 1_000_000,
      profile_source: String(event.profileSource || ''),
      profile_model: String(event.profileModel || ''),
      profile_alignment: String(event.profileAlignment || ''),
      source_license: String(event.source_license || ''),
      source_doi: String(event.source_doi || ''),
    },
    rejectionReason: null,
  };
}

export function evaluateCsslKucheraBaseline(event: ResearchEvent): BaselineEventResult {
  return evaluateResearchKucheraBaseline(event);
}

function csvEscape(value: string | number | boolean): string {
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function evaluateJsonLines(input: string): { rows: Array<Record<string, string | number | boolean>>; report: Record<string, unknown> } {
  const events = input.split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line) as ResearchEvent);
  const rows: Array<Record<string, string | number | boolean>> = [];
  const rejectionCounts: Record<string, number> = {};
  for (const event of events) {
    const result = evaluateResearchKucheraBaseline(event);
    if (result.row) rows.push(result.row);
    else if (result.rejectionReason) rejectionCounts[result.rejectionReason] = (rejectionCounts[result.rejectionReason] ?? 0) + 1;
  }
  rows.sort((left, right) => String(left.event_id).localeCompare(String(right.event_id)));
  return {
    rows,
    report: {
      inputEventCount: events.length,
      alignedBaselineEventCount: rows.length,
      rejectedEventCount: events.length - rows.length,
      rejectionCounts,
      baselineMethod: 'exact MySnow production (t-1,t] interval-mean profile; model-precipitation weighted Kuchera daily mean',
      calibrationEnabled: false,
      scientificScope: 'research-only observational SLR evaluation; source scope is carried per event',
    },
  };
}

function writeCsv(path: string, rows: Array<Record<string, string | number | boolean>>): void {
  if (rows.length === 0) throw new Error('No baseline rows were produced');
  mkdirSync(dirname(path), { recursive: true });
  const fields = Object.keys(rows[0]);
  const lines = [fields.join(','), ...rows.map(row => fields.map(field => csvEscape(row[field])).join(','))];
  writeFileSync(path, `${lines.join('\n')}\n`, 'utf8');
}

function main(): void {
  const args = process.argv.slice(2);
  const value = (flag: string): string => {
    const index = args.indexOf(flag);
    if (index < 0 || !args[index + 1]) throw new Error(`Missing ${flag}`);
    return args[index + 1];
  };
  const inputPath = value('--input');
  const outputPath = value('--output');
  const reportPath = value('--report');
  const result = evaluateJsonLines(readFileSync(inputPath, 'utf8'));
  writeCsv(outputPath, result.rows);
  mkdirSync(dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, `${JSON.stringify(result.report, null, 2)}\n`, 'utf8');
  process.stdout.write(`${JSON.stringify({ output: outputPath, ...result.report })}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
