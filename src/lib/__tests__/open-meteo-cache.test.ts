import { describe, expect, it, vi } from 'vitest';
import {
    assertAllowedOpenMeteoUrl,
    isAllowedOpenMeteoUrl,
    OPEN_METEO_FORECAST_TTL_MS,
    OPEN_METEO_HISTORICAL_TTL_MS,
    OPEN_METEO_META_TTL_MS,
    OpenMeteoAllowlistError,
    OpenMeteoCache,
    openMeteoTtlMs,
} from '../open-meteo-cache';

const FORECAST_URL = 'https://api.open-meteo.com/v1/forecast?latitude=39.19&longitude=-120.24';
const META_URL = 'https://api.open-meteo.com/data/ncep_hrrr_conus/static/meta.json';

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
    return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
        ...init,
    });
}

function makeCache(fetchImpl: (input: string, init?: RequestInit) => Promise<Response>, options: {
    maxEntries?: number;
    maxBytes?: number;
    upstreamTimeoutMs?: number;
} = {}) {
    let nowMs = 1_000_000;
    const sleep = vi.fn(async () => undefined);
    const cache = new OpenMeteoCache({
        fetchImpl,
        now: () => nowMs,
        sleep,
        ...options,
    });
    return {
        cache,
        sleep,
        advance: (ms: number) => { nowMs += ms; },
        setNow: (ms: number) => { nowMs = ms; },
        startMs: 1_000_000,
    };
}

describe('Open-Meteo allowlist', () => {
    it('accepts exactly the Open-Meteo endpoints the app uses', () => {
        for (const url of [
            'https://api.open-meteo.com/v1/forecast?latitude=1&longitude=2',
            'https://api.open-meteo.com/v1/ecmwf?latitude=1&longitude=2',
            META_URL,
            'https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=1&longitude=2',
            'https://ensemble-api.open-meteo.com/v1/ensemble?latitude=1&longitude=2',
        ]) {
            expect(isAllowedOpenMeteoUrl(url)).toBe(true);
            expect(assertAllowedOpenMeteoUrl(url).toString()).toBe(url);
        }
    });

    it('rejects other hosts, plain HTTP, credentialed URLs and unexpected paths', async () => {
        const fetchImpl = vi.fn(async () => jsonResponse({ ok: true }));
        const { cache } = makeCache(fetchImpl);
        for (const url of [
            'https://evil.example.com/v1/forecast?latitude=1&longitude=2',
            'https://api.open-meteo.com.evil.example.com/v1/forecast',
            'http://api.open-meteo.com/v1/forecast?latitude=1&longitude=2',
            'https://user:pass@api.open-meteo.com/v1/forecast?latitude=1&longitude=2',
            'https://api.open-meteo.com/v1/other?latitude=1&longitude=2',
            'https://api.open-meteo.com/data/ncep_hrrr_conus/other.json',
            'https://historical-forecast-api.open-meteo.com/v1/ensemble',
            'not a url',
        ]) {
            expect(isAllowedOpenMeteoUrl(url)).toBe(false);
            await expect(cache.fetch(url)).rejects.toBeInstanceOf(OpenMeteoAllowlistError);
        }
        expect(fetchImpl).not.toHaveBeenCalled();
    });
});

describe('OpenMeteoCache TTL policy', () => {
    it('assigns 30 min to forecasts, 5 min to meta.json and 60 min to historical forecasts', () => {
        expect(openMeteoTtlMs(new URL(FORECAST_URL))).toBe(OPEN_METEO_FORECAST_TTL_MS);
        expect(openMeteoTtlMs(new URL('https://api.open-meteo.com/v1/ecmwf?x=1'))).toBe(OPEN_METEO_FORECAST_TTL_MS);
        expect(openMeteoTtlMs(new URL('https://ensemble-api.open-meteo.com/v1/ensemble?x=1'))).toBe(OPEN_METEO_FORECAST_TTL_MS);
        expect(openMeteoTtlMs(new URL(META_URL))).toBe(OPEN_METEO_META_TTL_MS);
        expect(openMeteoTtlMs(new URL('https://historical-forecast-api.open-meteo.com/v1/forecast?x=1'))).toBe(OPEN_METEO_HISTORICAL_TTL_MS);
        expect(OPEN_METEO_FORECAST_TTL_MS).toBe(30 * 60 * 1000);
        expect(OPEN_METEO_META_TTL_MS).toBe(5 * 60 * 1000);
        expect(OPEN_METEO_HISTORICAL_TTL_MS).toBe(60 * 60 * 1000);
    });

    it('expires entries at their TTL and refetches', async () => {
        const fetchImpl = vi.fn(async () => jsonResponse({ value: fetchImpl.mock.calls.length }));
        const { cache, advance } = makeCache(fetchImpl);

        const first = await cache.fetch(META_URL);
        expect(first.headers.get('X-Cache')).toBe('MISS');
        advance(OPEN_METEO_META_TTL_MS - 1);
        const stillFresh = await cache.fetch(META_URL);
        expect(stillFresh.headers.get('X-Cache')).toBe('HIT');
        expect(fetchImpl).toHaveBeenCalledTimes(1);

        advance(2);
        const expired = await cache.fetch(META_URL);
        expect(expired.headers.get('X-Cache')).toBe('MISS');
        expect(fetchImpl).toHaveBeenCalledTimes(2);
    });
});

describe('OpenMeteoCache behaviour', () => {
    it('keys entries by the full upstream URL: MISS, then HIT, different query is a new MISS', async () => {
        const fetchImpl = vi.fn(async (input: string) => jsonResponse({ url: input }));
        const { cache } = makeCache(fetchImpl);

        const miss = await cache.fetch(FORECAST_URL);
        expect(miss.headers.get('X-Cache')).toBe('MISS');
        expect(await miss.json()).toEqual({ url: FORECAST_URL });

        const hit = await cache.fetch(FORECAST_URL);
        expect(hit.headers.get('X-Cache')).toBe('HIT');
        expect(await hit.json()).toEqual({ url: FORECAST_URL });
        expect(fetchImpl).toHaveBeenCalledTimes(1);

        const other = await cache.fetch(`${FORECAST_URL}&forecast_days=3`);
        expect(other.headers.get('X-Cache')).toBe('MISS');
        expect(fetchImpl).toHaveBeenCalledTimes(2);
    });

    it('dedupes concurrent requests for the same URL into one upstream trip', async () => {
        let resolveUpstream: ((response: Response) => void) | null = null;
        const fetchImpl = vi.fn(() => new Promise<Response>((resolve) => { resolveUpstream = resolve; }));
        const { cache } = makeCache(fetchImpl);

        const first = cache.fetch(FORECAST_URL);
        const second = cache.fetch(FORECAST_URL);
        expect(fetchImpl).toHaveBeenCalledTimes(1);

        resolveUpstream!(jsonResponse({ shared: true }));
        const [a, b] = await Promise.all([first, second]);
        expect(await a.json()).toEqual({ shared: true });
        expect(await b.json()).toEqual({ shared: true });
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it('retries a 429 once, honouring Retry-After capped at 3 s, then caches the success', async () => {
        const fetchImpl = vi.fn()
            .mockResolvedValueOnce(new Response('slow down', { status: 429, headers: { 'Retry-After': '10' } }))
            .mockResolvedValueOnce(jsonResponse({ ok: true }));
        const { cache, sleep } = makeCache(fetchImpl);

        const res = await cache.fetch(FORECAST_URL);
        expect(res.status).toBe(200);
        expect(res.headers.get('X-Cache')).toBe('MISS');
        expect(fetchImpl).toHaveBeenCalledTimes(2);
        expect(sleep).toHaveBeenCalledTimes(1);
        expect(sleep).toHaveBeenCalledWith(3000);
    });

    it('stops after two upstream attempts and propagates a persistent 5xx when no stale entry exists', async () => {
        const fetchImpl = vi.fn(async () => new Response('upstream broken', { status: 503 }));
        const { cache, sleep } = makeCache(fetchImpl);

        const res = await cache.fetch(FORECAST_URL);
        expect(res.status).toBe(503);
        expect(await res.text()).toBe('upstream broken');
        expect(fetchImpl).toHaveBeenCalledTimes(2);
        expect(sleep).toHaveBeenCalledTimes(1);
    });

    it('does not retry or mask an authoritative 4xx', async () => {
        const fetchImpl = vi.fn(async () => Response.json({ reason: 'bad request' }, { status: 400 }));
        const { cache, sleep } = makeCache(fetchImpl);

        const res = await cache.fetch(FORECAST_URL);
        expect(res.status).toBe(400);
        expect(fetchImpl).toHaveBeenCalledTimes(1);
        expect(sleep).not.toHaveBeenCalled();
    });

    it('serves a stale entry on upstream failure inside 6 h, and propagates the failure beyond it', async () => {
        let failing = false;
        const fetchImpl = vi.fn(async () => {
            if (failing) throw new Error('connection reset');
            return jsonResponse({ fresh: true });
        });
        const { cache, advance, setNow, startMs } = makeCache(fetchImpl);

        const prime = await cache.fetch(FORECAST_URL);
        expect(prime.headers.get('X-Cache')).toBe('MISS');

        failing = true;
        advance(OPEN_METEO_FORECAST_TTL_MS + 1000); // expired, well inside stale window
        const stale = await cache.fetch(FORECAST_URL);
        expect(stale.headers.get('X-Cache')).toBe('STALE');
        expect(await stale.json()).toEqual({ fresh: true });
        // 1 prime + 2 failed attempts
        expect(fetchImpl).toHaveBeenCalledTimes(3);

        setNow(startMs + 6 * 60 * 60 * 1000 + 1); // beyond the stale-if-error window
        await expect(cache.fetch(FORECAST_URL)).rejects.toThrow('connection reset');
        expect(fetchImpl).toHaveBeenCalledTimes(5);
    });

    it('evicts least-recently-used entries beyond the entry bound', async () => {
        const fetchImpl = vi.fn(async (input: string) => jsonResponse({ url: input }));
        const { cache } = makeCache(fetchImpl, { maxEntries: 2 });
        const urlA = `${FORECAST_URL}&marker=a`;
        const urlB = `${FORECAST_URL}&marker=b`;
        const urlC = `${FORECAST_URL}&marker=c`;

        await cache.fetch(urlA);
        await cache.fetch(urlB);
        const hitA = await cache.fetch(urlA); // refresh A; B is now LRU
        expect(hitA.headers.get('X-Cache')).toBe('HIT');
        await cache.fetch(urlC); // evicts B
        expect(cache.size).toBe(2);

        const againA = await cache.fetch(urlA);
        expect(againA.headers.get('X-Cache')).toBe('HIT');
        const againB = await cache.fetch(urlB);
        expect(againB.headers.get('X-Cache')).toBe('MISS');
    });

    it('evicts entries beyond the total byte bound', async () => {
        const fetchImpl = vi.fn(async (input: string) => new Response(input.includes('marker=a') ? 'aaaa' : 'bbbb', {
            status: 200,
            headers: { 'content-type': 'text/plain' },
        }));
        const { cache } = makeCache(fetchImpl, { maxEntries: 10, maxBytes: 6 });
        const urlA = `${FORECAST_URL}&marker=a`;
        const urlB = `${FORECAST_URL}&marker=b`;

        await cache.fetch(urlA); // 4 bytes
        await cache.fetch(urlB); // 4 more bytes -> A evicted to satisfy the 6-byte bound
        expect(cache.size).toBe(1);
        expect(cache.byteSize).toBe(4);

        const againB = await cache.fetch(urlB);
        expect(againB.headers.get('X-Cache')).toBe('HIT');
        const againA = await cache.fetch(urlA);
        expect(againA.headers.get('X-Cache')).toBe('MISS');
    });

    it('bounds a stalled upstream fetch with its own deadline and retries it', async () => {
        const fetchImpl = vi.fn(() => new Promise<Response>(() => undefined));
        const { cache } = makeCache(fetchImpl, { upstreamTimeoutMs: 20 });

        await expect(cache.fetch(FORECAST_URL)).rejects.toThrow(/timed out/);
        expect(fetchImpl).toHaveBeenCalledTimes(2);
    });

    it('bounds a stalled upstream body read with the same deadline', async () => {
        const fetchImpl = vi.fn(async () => new Response(new ReadableStream({ start() { /* never emits */ } }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
        }));
        const { cache } = makeCache(fetchImpl, { upstreamTimeoutMs: 20 });

        await expect(cache.fetch(FORECAST_URL)).rejects.toThrow(/timed out/);
        expect(fetchImpl).toHaveBeenCalledTimes(2);
    });

    it('refuses redirects so a redirect target cannot escape the allowlist', async () => {
        const inits: Array<RequestInit | undefined> = [];
        const fetchImpl = vi.fn(async (_input: string, init?: RequestInit) => {
            inits.push(init);
            return jsonResponse({ ok: true });
        });
        const { cache } = makeCache(fetchImpl);

        await cache.fetch(FORECAST_URL);
        expect(inits.length).toBeGreaterThan(0);
        expect(inits.every((init) => init?.redirect === 'error')).toBe(true);
    });

    it('does not mask an authoritative 400 with a primed stale entry', async () => {
        let badRequest = false;
        const fetchImpl = vi.fn(async () => {
            if (badRequest) return Response.json({ reason: 'bad request' }, { status: 400 });
            return jsonResponse({ fresh: true });
        });
        const { cache, advance } = makeCache(fetchImpl);

        await cache.fetch(FORECAST_URL);
        badRequest = true;
        advance(OPEN_METEO_FORECAST_TTL_MS + 1000); // expired but inside the stale window
        const res = await cache.fetch(FORECAST_URL);
        expect(res.status).toBe(400);
        expect(res.headers.get('X-Cache')).toBe('MISS');
        // 1 prime + 1 authoritative answer (no retry, no stale substitution)
        expect(fetchImpl).toHaveBeenCalledTimes(2);
    });

    it('preserves Retry-After on a terminal 429 after retries are exhausted', async () => {
        const fetchImpl = vi.fn(async () => new Response('slow down', { status: 429, headers: { 'Retry-After': '7' } }));
        const { cache } = makeCache(fetchImpl);

        const res = await cache.fetch(FORECAST_URL);
        expect(res.status).toBe(429);
        expect(res.headers.get('Retry-After')).toBe('7');
        expect(fetchImpl).toHaveBeenCalledTimes(2);
    });

    it('round-trips a 204 without inventing a body', async () => {
        const fetchImpl = vi.fn(async () => new Response(null, { status: 204 }));
        const { cache } = makeCache(fetchImpl);

        const first = await cache.fetch(FORECAST_URL);
        expect(first.status).toBe(204);
        expect(await first.text()).toBe('');
        const second = await cache.fetch(FORECAST_URL);
        expect(second.status).toBe(204);
        expect(second.headers.get('X-Cache')).toBe('HIT');
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });
});
