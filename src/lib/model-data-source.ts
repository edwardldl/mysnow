/**
 * Server-side loader for the current ENS-EB serving snapshot.
 *
 * Publication model (plan Revision 2, D5 — Blob runtime-read): each
 * serving batch is published to Vercel Blob as an immutable snapshot
 * object plus a small manifest that names it:
 *
 *   model-forecasts/snapshots/<cycle>-<sha12>.json   (immutable)
 *   model-forecasts/manifest.json                    (pointer, written last)
 *
 * This loader reads the manifest, fetches the snapshot it names, and
 * verifies the snapshot bytes against the manifest's SHA-256 before
 * serving them. The payload is also validated structurally and for
 * manifest/snapshot cycle consistency, and a manifest naming an older
 * cycle than the one already in effect (cached or bundled) is rejected
 * — publication can never move the served forecasts backward here.
 *
 * Any failure — Blob unconfigured, manifest or snapshot unreachable,
 * hash mismatch, malformed payload, regression — falls back to the
 * last known good snapshot: a previously verified Blob snapshot held
 * in this process if there is one, otherwise the bundled
 * `model-forecasts.json` shipped with the deployment. A Blob outage
 * therefore degrades freshness, never availability.
 *
 * Per-process caching: upstream is attempted at most once a minute
 * (successes and failures alike — a failing upstream is not retried on
 * every request); snapshots are cached by hash (they are immutable,
 * so a known hash never needs refetching). Freshness expectation: a
 * newly published batch reaches a given process within about a minute
 * of the manifest propagating, and reaches an open page on its next
 * load — pages do not poll, matching the pre-Blob behaviour where data
 * changed only on redeploy.
 *
 * Server-only: imports node:crypto, so a client-side import fails the
 * build loudly.
 */
import { createHash } from 'node:crypto';
import { modelForecastData, type ModelForecastData } from './model-forecast';

export interface ModelDataManifest {
    version: 1;
    cycleUtc: string;
    cycles: Partial<Record<string, string>>;
    sha256: string;
    snapshotPathname: string;
    snapshotUrl: string;
    recordCount: number;
    coverageStartUtc: string;
    coverageEndUtc: string;
    generatedAtUtc: string;
    publishedAtUtc: string;
}

export interface ServedModelData {
    data: ModelForecastData;
    source: 'blob' | 'bundled';
    cycleUtc: string | null;
    sha256: string | null;
}

const MANIFEST_PATHNAME = 'model-forecasts/manifest.json';
const ATTEMPT_INTERVAL_MS = 60_000;
const MANIFEST_TIMEOUT_MS = 5_000;
const SNAPSHOT_TIMEOUT_MS = 15_000;
const ISO_Z_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

interface CacheEntry {
    sha256: string;
    data: ModelForecastData;
    cycleUtc: string;
    checkedAtMs: number;
}

let cache: CacheEntry | null = null;
let lastAttemptAtMs = 0;
let inFlight: Promise<ServedModelData> | null = null;

function blobBaseUrl(): string | null {
    const raw = process.env.MODEL_FORECASTS_BLOB_BASE_URL;
    if (!raw) return null;
    const trimmed = raw.trim().replace(/\/+$/, '');
    return trimmed.length > 0 ? trimmed : null;
}

function bundledResult(): ServedModelData {
    return {
        data: modelForecastData,
        source: 'bundled',
        cycleUtc: modelForecastData.cycles?.ecmwf ?? null,
        sha256: null,
    };
}

function cachedResult(): ServedModelData {
    return { data: cache!.data, source: 'blob', cycleUtc: cache!.cycleUtc, sha256: cache!.sha256 };
}

/**
 * Per-arm freshness: every arm cycle in the floor must be present in
 * the candidate and no older there. A merged snapshot's arms advance
 * independently, so a scalar-maximum comparison is not a freshness
 * proof (a stale ECMWF arm could ride in behind a fresh HRRR cycle).
 */
function cyclesNotOlder(
    candidate: Partial<Record<string, string>> | undefined,
    floor: Partial<Record<string, string>> | undefined,
): boolean {
    for (const [arm, floorCycle] of Object.entries(floor ?? {})) {
        if (!floorCycle) continue;
        const candidateCycle = candidate?.[arm];
        if (candidateCycle === undefined || candidateCycle < floorCycle) return false;
    }
    return true;
}

function isPlausibleManifest(value: unknown, baseUrl: string): value is ModelDataManifest {
    if (typeof value !== 'object' || value === null) return false;
    const m = value as Record<string, unknown>;
    if (
        m.version !== 1 ||
        typeof m.cycleUtc !== 'string' ||
        !ISO_Z_PATTERN.test(m.cycleUtc) ||
        typeof m.sha256 !== 'string' ||
        !/^[0-9a-f]{64}$/.test(m.sha256) ||
        typeof m.snapshotUrl !== 'string' ||
        !m.snapshotUrl.startsWith(`${baseUrl}/`)
    ) {
        return false;
    }
    if (typeof m.cycles === 'object' && m.cycles !== null) {
        for (const v of Object.values(m.cycles as Record<string, unknown>)) {
            if (typeof v !== 'string' || !ISO_Z_PATTERN.test(v)) return false;
        }
    }
    return true;
}

function isPlausibleData(value: unknown): value is ModelForecastData {
    if (typeof value !== 'object' || value === null) return false;
    const d = value as Record<string, unknown>;
    if (!Array.isArray(d.points) || d.points.length === 0) return false;
    for (const point of d.points) {
        if (typeof point !== 'object' || point === null) return false;
        const forecasts = (point as Record<string, unknown>).forecasts;
        if (typeof forecasts !== 'object' || forecasts === null) return false;
        for (const records of Object.values(forecasts as Record<string, unknown>)) {
            if (!Array.isArray(records)) return false;
        }
    }
    if (typeof d.cycles !== 'object' || d.cycles === null) return false;
    const cycleValues = Object.values(d.cycles as Record<string, unknown>);
    return cycleValues.length > 0 && cycleValues.every((v) => typeof v === 'string' && ISO_Z_PATTERN.test(v));
}

async function fetchJson(url: string, timeoutMs: number): Promise<unknown> {
    const res = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) throw new Error(`fetch ${url} -> ${res.status}`);
    return res.json();
}

async function loadFromBlob(baseUrl: string): Promise<ServedModelData> {
    const manifest = await fetchJson(`${baseUrl}/${MANIFEST_PATHNAME}`, MANIFEST_TIMEOUT_MS);
    if (!isPlausibleManifest(manifest, baseUrl)) throw new Error('implausible model-data manifest');
    // Regression guard: never serve arm cycles older than the ones
    // in effect (the verified cache, else the bundled snapshot).
    const floorCycles = cache ? cache.data.cycles : modelForecastData.cycles;
    if (manifest.cycles !== undefined) {
        if (!cyclesNotOlder(manifest.cycles, floorCycles)) {
            throw new Error('manifest arm cycles are older than the ones in effect');
        }
    } else {
        const floorMax = Object.values(floorCycles ?? {}).reduce((a, b) => (a > b ? a : b), '');
        if (floorMax && manifest.cycleUtc < floorMax) {
            throw new Error('manifest names an older cycle than the one in effect');
        }
    }
    if (cache && cache.sha256 === manifest.sha256) {
        cache.checkedAtMs = Date.now();
        return cachedResult();
    }
    const res = await fetch(manifest.snapshotUrl, {
        cache: 'no-store',
        signal: AbortSignal.timeout(SNAPSHOT_TIMEOUT_MS),
    });
    if (!res.ok) throw new Error(`fetch snapshot -> ${res.status}`);
    const bytes = Buffer.from(await res.arrayBuffer());
    const digest = createHash('sha256').update(bytes).digest('hex');
    if (digest !== manifest.sha256) throw new Error('snapshot hash mismatch');
    const parsed: unknown = JSON.parse(bytes.toString('utf8'));
    if (!isPlausibleData(parsed)) throw new Error('implausible model-data snapshot');
    // Manifest and snapshot must agree about the cycles they describe:
    // identical per-arm cycles where both state them, and cycleUtc
    // equal to their maximum (a merged snapshot can carry a different
    // cycle per arm under the tiered cadence).
    const snapshotCycles = parsed.cycles ?? {};
    for (const [arm, cycle] of Object.entries(snapshotCycles)) {
        if (manifest.cycles?.[arm] !== undefined && manifest.cycles[arm] !== cycle) {
            throw new Error('manifest and snapshot cycles disagree');
        }
    }
    const cycleValues = Object.values(snapshotCycles);
    if (cycleValues.length > 0 && manifest.cycleUtc !== cycleValues.reduce((a, b) => (a > b ? a : b))) {
        throw new Error('manifest and snapshot cycles disagree');
    }
    // The snapshot's own cycles must clear the same per-arm floor
    // (the manifest could omit cycles the snapshot carries).
    if (!cyclesNotOlder(snapshotCycles, floorCycles)) {
        throw new Error('snapshot arm cycles are older than the ones in effect');
    }
    cache = { sha256: manifest.sha256, data: parsed, cycleUtc: manifest.cycleUtc, checkedAtMs: Date.now() };
    return cachedResult();
}

export async function loadServedModelData(): Promise<ServedModelData> {
    const baseUrl = blobBaseUrl();
    if (!baseUrl) return bundledResult();
    const now = Date.now();
    if (cache && now - cache.checkedAtMs < ATTEMPT_INTERVAL_MS) return cachedResult();
    if (now - lastAttemptAtMs < ATTEMPT_INTERVAL_MS) {
        return cache ? cachedResult() : bundledResult();
    }
    if (!inFlight) {
        lastAttemptAtMs = now;
        inFlight = (async () => {
            try {
                return await loadFromBlob(baseUrl);
            } catch {
                return cache ? cachedResult() : bundledResult();
            } finally {
                inFlight = null;
            }
        })();
    }
    return inFlight;
}

/** Test seam: forget the cached snapshot and attempt clock. */
export function resetModelDataSourceCacheForTests(): void {
    cache = null;
    lastAttemptAtMs = 0;
    inFlight = null;
}
