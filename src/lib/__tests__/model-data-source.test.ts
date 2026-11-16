import { createHash } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
    loadServedModelData,
    resetModelDataSourceCacheForTests,
} from '../model-data-source';
import { modelForecastData } from '../model-forecast';

const BASE = 'https://store123.public.blob.vercel-storage.com';
const MANIFEST_URL = `${BASE}/model-forecasts/manifest.json`;

const FAKE_DATA = {
    generatedAtUtc: '2026-11-16T00:00:00Z',
    modelVersion: 'test',
    modelLabel: 'ENS-EB Model (MySnow)',
    featureBuilderVersion: 'test',
    nanPolicy: 'test',
    cycles: { ecmwf: '2026-11-16T00:00:00Z', hrrr: '2026-11-16T00:00:00Z' },
    points: [{ pointId: 'fake', forecasts: { ecmwf: [], hrrr: [] } }],
};
const FAKE_BYTES = Buffer.from(JSON.stringify(FAKE_DATA), 'utf8');
const FAKE_SHA = createHash('sha256').update(FAKE_BYTES).digest('hex');
const SNAPSHOT_URL = `${BASE}/model-forecasts/snapshots/20261116T000000Z-${FAKE_SHA.slice(0, 12)}.json`;

function manifest(overrides: Record<string, unknown> = {}): Record<string, unknown> {
    return {
        version: 1,
        cycleUtc: '2026-11-16T00:00:00Z',
        cycles: { ecmwf: '2026-11-16T00:00:00Z', hrrr: '2026-11-16T00:00:00Z' },
        sha256: FAKE_SHA,
        snapshotPathname: 'model-forecasts/snapshots/x.json',
        snapshotUrl: SNAPSHOT_URL,
        recordCount: 1,
        coverageStartUtc: '2026-11-17T00:00:00Z',
        coverageEndUtc: '2026-11-25T00:00:00Z',
        generatedAtUtc: '2026-11-16T00:00:00Z',
        publishedAtUtc: '2026-11-16T01:00:00Z',
        ...overrides,
    };
}

function jsonResponse(body: unknown): Response {
    return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
    });
}

function happyFetch(): ReturnType<typeof vi.fn> {
    return vi.fn(async (input: string) => {
        if (input === MANIFEST_URL) return jsonResponse(manifest());
        if (input === SNAPSHOT_URL) {
            return new Response(FAKE_BYTES, { status: 200, headers: { 'content-type': 'application/json' } });
        }
        throw new Error(`unexpected fetch ${input}`);
    });
}

beforeEach(() => {
    resetModelDataSourceCacheForTests();
    process.env.MODEL_FORECASTS_BLOB_BASE_URL = BASE;
});

afterEach(() => {
    delete process.env.MODEL_FORECASTS_BLOB_BASE_URL;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('loadServedModelData', () => {
    it('serves the bundled snapshot when Blob is not configured', async () => {
        delete process.env.MODEL_FORECASTS_BLOB_BASE_URL;
        const fetchFn = vi.fn();
        vi.stubGlobal('fetch', fetchFn);
        const served = await loadServedModelData();
        expect(served.source).toBe('bundled');
        expect(served.data).toBe(modelForecastData);
        expect(fetchFn).not.toHaveBeenCalled();
    });

    it('serves the Blob snapshot after verifying its hash', async () => {
        vi.stubGlobal('fetch', happyFetch());
        const served = await loadServedModelData();
        expect(served.source).toBe('blob');
        expect(served.sha256).toBe(FAKE_SHA);
        expect(served.cycleUtc).toBe('2026-11-16T00:00:00Z');
        expect(served.data.points).toHaveLength(1);
    });

    it('falls back to bundled when the snapshot hash does not match the manifest', async () => {
        const fetchFn = vi.fn(async (input: string) => {
            if (input === MANIFEST_URL) return jsonResponse(manifest({ sha256: '0'.repeat(64) }));
            if (input === SNAPSHOT_URL) return new Response(FAKE_BYTES, { status: 200 });
            throw new Error(`unexpected fetch ${input}`);
        });
        vi.stubGlobal('fetch', fetchFn);
        const served = await loadServedModelData();
        expect(served.source).toBe('bundled');
        expect(served.data).toBe(modelForecastData);
    });

    it('falls back to bundled when the manifest cannot be fetched', async () => {
        vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('network down'); }));
        const served = await loadServedModelData();
        expect(served.source).toBe('bundled');
    });

    it('rejects a manifest whose snapshot URL points outside the store', async () => {
        const fetchFn = vi.fn(async (input: string) => {
            if (input === MANIFEST_URL) {
                return jsonResponse(manifest({ snapshotUrl: 'https://evil.example.com/x.json' }));
            }
            throw new Error(`unexpected fetch ${input}`);
        });
        vi.stubGlobal('fetch', fetchFn);
        const served = await loadServedModelData();
        expect(served.source).toBe('bundled');
    });

    it('caches within the recheck window and does not refetch the snapshot for an unchanged hash', async () => {
        const fetchFn = happyFetch();
        vi.stubGlobal('fetch', fetchFn);
        const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
        await loadServedModelData();
        await loadServedModelData();
        expect(fetchFn).toHaveBeenCalledTimes(2); // manifest + snapshot, once each

        nowSpy.mockReturnValue(1_000_000 + 61_000); // past the 60 s recheck window
        const served = await loadServedModelData();
        expect(served.source).toBe('blob');
        expect(fetchFn).toHaveBeenCalledTimes(3); // manifest re-checked, snapshot not refetched
    });

    it('rejects a structurally invalid snapshot even with a matching hash', async () => {
        const badData = {
            cycles: { ecmwf: '2026-11-16T00:00:00Z' },
            points: [null],
        };
        const badBytes = Buffer.from(JSON.stringify(badData), 'utf8');
        const badSha = createHash('sha256').update(badBytes).digest('hex');
        const fetchFn = vi.fn(async (input: string) => {
            if (input === MANIFEST_URL) return jsonResponse(manifest({ sha256: badSha }));
            if (input === SNAPSHOT_URL) return new Response(badBytes, { status: 200 });
            throw new Error(`unexpected fetch ${input}`);
        });
        vi.stubGlobal('fetch', fetchFn);
        const served = await loadServedModelData();
        expect(served.source).toBe('bundled');
    });

    it('rejects a crossed-arms snapshot: stale ECMWF behind a fresh HRRR cycle', async () => {
        // The scalar maximum (the HRRR cycle) is newer than the
        // bundle, but the ECMWF arm is older than the bundled ECMWF
        // cycle: per-arm floors must refuse it.
        const bundled = modelForecastData.cycles;
        const shift = (iso: string, hours: number) =>
            new Date(Date.parse(iso) + hours * 3_600_000).toISOString().replace('.000Z', 'Z');
        const crossedCycles = {
            ecmwf: shift(bundled.ecmwf!, -24),
            hrrr: shift(bundled.hrrr!, 24),
        };
        const crossedData = { ...FAKE_DATA, cycles: crossedCycles };
        const crossedBytes = Buffer.from(JSON.stringify(crossedData), 'utf8');
        const crossedSha = createHash('sha256').update(crossedBytes).digest('hex');
        const fetchFn = vi.fn(async (input: string) => {
            if (input === MANIFEST_URL) {
                return jsonResponse(manifest({
                    sha256: crossedSha,
                    cycleUtc: crossedCycles.hrrr,
                    cycles: crossedCycles,
                }));
            }
            if (input === SNAPSHOT_URL) return new Response(crossedBytes, { status: 200 });
            throw new Error(`unexpected fetch ${input}`);
        });
        vi.stubGlobal('fetch', fetchFn);
        const served = await loadServedModelData();
        expect(served.source).toBe('bundled');
    });

    it('accepts a merged snapshot whose arms advance independently', async () => {
        const bundled = modelForecastData.cycles;
        const shift = (iso: string, hours: number) =>
            new Date(Date.parse(iso) + hours * 3_600_000).toISOString().replace('.000Z', 'Z');
        const mergedCycles = {
            ecmwf: bundled.ecmwf!,
            hrrr: shift(bundled.hrrr!, 6),
        };
        const mergedData = { ...FAKE_DATA, cycles: mergedCycles };
        const mergedBytes = Buffer.from(JSON.stringify(mergedData), 'utf8');
        const mergedSha = createHash('sha256').update(mergedBytes).digest('hex');
        const fetchFn = vi.fn(async (input: string) => {
            if (input === MANIFEST_URL) {
                return jsonResponse(manifest({
                    sha256: mergedSha,
                    cycleUtc: mergedCycles.hrrr,
                    cycles: mergedCycles,
                }));
            }
            if (input === SNAPSHOT_URL) return new Response(mergedBytes, { status: 200 });
            throw new Error(`unexpected fetch ${input}`);
        });
        vi.stubGlobal('fetch', fetchFn);
        const served = await loadServedModelData();
        expect(served.source).toBe('blob');
        expect(served.data.cycles).toEqual(mergedCycles);
    });

    it('rejects a manifest naming an older cycle than the bundled snapshot', async () => {
        const oldData = { ...FAKE_DATA, cycles: { ecmwf: '2026-11-10T00:00:00Z', hrrr: '2026-11-10T00:00:00Z' } };
        const oldBytes = Buffer.from(JSON.stringify(oldData), 'utf8');
        const oldSha = createHash('sha256').update(oldBytes).digest('hex');
        const fetchFn = vi.fn(async (input: string) => {
            if (input === MANIFEST_URL) {
                return jsonResponse(manifest({ sha256: oldSha, cycleUtc: '2026-11-10T00:00:00Z' }));
            }
            if (input === SNAPSHOT_URL) return new Response(oldBytes, { status: 200 });
            throw new Error(`unexpected fetch ${input}`);
        });
        vi.stubGlobal('fetch', fetchFn);
        const served = await loadServedModelData();
        expect(served.source).toBe('bundled');
    });

    it('throttles retries after an upstream failure', async () => {
        const fetchFn = vi.fn(async () => { throw new Error('network down'); });
        vi.stubGlobal('fetch', fetchFn);
        vi.spyOn(Date, 'now').mockReturnValue(5_000_000);
        const first = await loadServedModelData();
        const second = await loadServedModelData();
        expect(first.source).toBe('bundled');
        expect(second.source).toBe('bundled');
        expect(fetchFn).toHaveBeenCalledTimes(1);
    });
});
