/**
 * Server-side cache for Open-Meteo responses.
 *
 * The browser used to call api.open-meteo.com directly, so every page load
 * raced several large/slow upstream requests (and, in dev, React's effect
 * double-invocation doubled the burst). The Next route handler in
 * src/app/api/open-meteo/route.ts proxies those calls through this cache:
 * one upstream trip per URL, bounded retries, and repeats served locally.
 *
 * Only successful (2xx) upstream responses are stored. Errors are never
 * cached; a retryable upstream failure may be answered from an expired
 * entry inside the stale-if-error window instead.
 */

export const OPEN_METEO_FORECAST_TTL_MS = 30 * 60 * 1000; // 30 minutes
export const OPEN_METEO_META_TTL_MS = 5 * 60 * 1000; // 5 minutes
export const OPEN_METEO_HISTORICAL_TTL_MS = 60 * 60 * 1000; // 60 minutes
export const OPEN_METEO_STALE_IF_ERROR_MS = 6 * 60 * 60 * 1000; // 6 hours
export const OPEN_METEO_MAX_UPSTREAM_ATTEMPTS = 2;
export const OPEN_METEO_DEFAULT_MAX_ENTRIES = 128;
export const OPEN_METEO_DEFAULT_MAX_BYTES = 32 * 1024 * 1024; // 32 MiB
export const OPEN_METEO_UPSTREAM_TIMEOUT_MS = 15 * 1000; // per-attempt deadline, headers + body

const RETRY_AFTER_CAP_MS = 3 * 1000;
const BASE_BACKOFF_MS = 250;

export class OpenMeteoAllowlistError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'OpenMeteoAllowlistError';
    }
}

type FetchImpl = (input: string, init?: RequestInit) => Promise<Response>;
type SleepImpl = (ms: number) => Promise<void>;

export interface OpenMeteoCacheOptions {
    fetchImpl?: FetchImpl;
    now?: () => number;
    sleep?: SleepImpl;
    maxEntries?: number;
    maxBytes?: number;
    staleIfErrorMs?: number;
    maxAttempts?: number;
    upstreamTimeoutMs?: number;
    baseBackoffMs?: number;
    retryAfterCapMs?: number;
}

interface CacheEntry {
    body: string;
    status: number;
    statusText: string;
    contentType: string | null;
    retryAfter: string | null;
    storedAtMs: number;
    expiresAtMs: number;
    byteSize: number;
}

interface LoadOutcome {
    entry: CacheEntry;
    cacheStatus: 'MISS' | 'STALE';
}

const META_PATH_PATTERN = /^\/data\/[a-z0-9_.-]+\/static\/meta\.json$/i;

/**
 * Allowlist for the proxy: only the Open-Meteo endpoints the app itself
 * uses. Anything else (other hosts, plain HTTP, credentialed URLs,
 * unexpected paths) is rejected so the route can never become an open
 * proxy.
 */
export function assertAllowedOpenMeteoUrl(input: string | URL): URL {
    let url: URL;
    try {
        url = input instanceof URL ? new URL(input.toString()) : new URL(input);
    } catch {
        throw new OpenMeteoAllowlistError('Invalid upstream URL');
    }

    if (url.protocol !== 'https:' || url.username || url.password || (url.port !== '' && url.port !== '443')) {
        throw new OpenMeteoAllowlistError(`Upstream URL is not allowed: ${url.toString()}`);
    }

    switch (url.hostname) {
        case 'api.open-meteo.com':
            if (url.pathname === '/v1/forecast' || url.pathname === '/v1/ecmwf' || META_PATH_PATTERN.test(url.pathname)) {
                return url;
            }
            break;
        case 'historical-forecast-api.open-meteo.com':
            if (url.pathname === '/v1/forecast') return url;
            break;
        case 'ensemble-api.open-meteo.com':
            if (url.pathname === '/v1/ensemble') return url;
            break;
        default:
            break;
    }

    throw new OpenMeteoAllowlistError(`Upstream URL is not allowed: ${url.toString()}`);
}

export function isAllowedOpenMeteoUrl(input: string | URL): boolean {
    try {
        assertAllowedOpenMeteoUrl(input);
        return true;
    } catch {
        return false;
    }
}

/** Per-endpoint freshness lifetimes, aligned with the client cache policy. */
export function openMeteoTtlMs(url: URL): number {
    if (url.hostname === 'historical-forecast-api.open-meteo.com') {
        return OPEN_METEO_HISTORICAL_TTL_MS;
    }
    if (META_PATH_PATTERN.test(url.pathname)) {
        return OPEN_METEO_META_TTL_MS;
    }
    return OPEN_METEO_FORECAST_TTL_MS;
}

function isRetryableStatus(status: number): boolean {
    return status === 429 || status >= 500;
}

function retryAfterMs(response: Response, nowMs: number, capMs: number): number | null {
    const raw = response.headers.get('retry-after');
    if (!raw) return null;
    const seconds = Number(raw);
    if (Number.isFinite(seconds)) {
        return Math.min(Math.max(seconds, 0) * 1000, capMs);
    }
    const dateMs = Date.parse(raw);
    if (Number.isFinite(dateMs)) {
        return Math.min(Math.max(dateMs - nowMs, 0), capMs);
    }
    return null;
}

const textEncoder = new TextEncoder();

export class OpenMeteoCache {
    private readonly fetchImpl: FetchImpl;
    private readonly now: () => number;
    private readonly sleep: SleepImpl;
    private readonly maxEntries: number;
    private readonly maxBytes: number;
    private readonly staleIfErrorMs: number;
    private readonly maxAttempts: number;
    private readonly upstreamTimeoutMs: number;
    private readonly baseBackoffMs: number;
    private readonly retryAfterCapMs: number;

    private readonly entries = new Map<string, CacheEntry>();
    private readonly inflight = new Map<string, Promise<LoadOutcome>>();
    private totalBytes = 0;

    constructor(options: OpenMeteoCacheOptions = {}) {
        this.fetchImpl = options.fetchImpl ?? ((input: string, init?: RequestInit) => fetch(input, init));
        this.now = options.now ?? (() => Date.now());
        this.sleep = options.sleep ?? ((ms: number) => new Promise<void>((resolve) => { setTimeout(resolve, ms); }));
        this.maxEntries = Math.max(1, options.maxEntries ?? OPEN_METEO_DEFAULT_MAX_ENTRIES);
        this.maxBytes = Math.max(1, options.maxBytes ?? OPEN_METEO_DEFAULT_MAX_BYTES);
        this.staleIfErrorMs = options.staleIfErrorMs ?? OPEN_METEO_STALE_IF_ERROR_MS;
        this.maxAttempts = Math.max(1, options.maxAttempts ?? OPEN_METEO_MAX_UPSTREAM_ATTEMPTS);
        this.upstreamTimeoutMs = Math.max(1, options.upstreamTimeoutMs ?? OPEN_METEO_UPSTREAM_TIMEOUT_MS);
        this.baseBackoffMs = options.baseBackoffMs ?? BASE_BACKOFF_MS;
        this.retryAfterCapMs = options.retryAfterCapMs ?? RETRY_AFTER_CAP_MS;
    }

    get size(): number {
        return this.entries.size;
    }

    get byteSize(): number {
        return this.totalBytes;
    }

    clear(): void {
        this.entries.clear();
        this.totalBytes = 0;
    }

    async fetch(input: string | URL): Promise<Response> {
        const url = assertAllowedOpenMeteoUrl(input);
        const key = url.toString();

        const fresh = this.freshEntry(key);
        if (fresh) {
            return this.toResponse(fresh, 'HIT');
        }

        let pending = this.inflight.get(key);
        if (!pending) {
            pending = this.load(key, url);
            this.inflight.set(key, pending);
        }

        try {
            const outcome = await pending;
            return this.toResponse(outcome.entry, outcome.cacheStatus);
        } finally {
            if (this.inflight.get(key) === pending) {
                this.inflight.delete(key);
            }
        }
    }

    private freshEntry(key: string): CacheEntry | null {
        const entry = this.entries.get(key);
        if (!entry) return null;
        if (this.now() >= entry.expiresAtMs) return null;
        // Refresh LRU order on use.
        this.entries.delete(key);
        this.entries.set(key, entry);
        return entry;
    }

    private staleEntry(key: string): CacheEntry | null {
        const entry = this.entries.get(key);
        if (!entry) return null;
        if (this.now() - entry.storedAtMs > this.staleIfErrorMs) return null;
        this.entries.delete(key);
        this.entries.set(key, entry);
        return entry;
    }

    private async load(key: string, url: URL): Promise<LoadOutcome> {
        let lastError: unknown = null;
        let lastHttpEntry: CacheEntry | null = null;

        for (let attempt = 1; attempt <= this.maxAttempts; attempt += 1) {
            let response: Response;
            let entry: CacheEntry;
            try {
                const fetched = await this.fetchUpstreamWithDeadline(url);
                response = fetched.response;
                entry = this.entryFromBody(response, fetched.body, url);
            } catch (error) {
                // Network errors, refused redirects, unreadable bodies and
                // deadline expiries are all retryable failure classes.
                lastError = error;
                if (attempt < this.maxAttempts) {
                    await this.sleep(this.backoffMs(attempt, null));
                    continue;
                }
                break;
            }

            if (response.ok) {
                this.store(key, entry);
                return { entry, cacheStatus: 'MISS' };
            }

            lastHttpEntry = entry;
            if (!isRetryableStatus(response.status)) {
                // 4xx (other than 429) is an authoritative answer: pass it
                // through uncached, never masked by stale data.
                return { entry, cacheStatus: 'MISS' };
            }

            if (attempt < this.maxAttempts) {
                await this.sleep(this.backoffMs(attempt, response));
            }
        }

        const stale = this.staleEntry(key);
        if (stale) {
            return { entry: stale, cacheStatus: 'STALE' };
        }
        if (lastHttpEntry) {
            return { entry: lastHttpEntry, cacheStatus: 'MISS' };
        }
        throw lastError instanceof Error ? lastError : new Error('Open-Meteo upstream request failed');
    }

    private backoffMs(attempt: number, response: Response | null): number {
        if (response) {
            const fromHeader = retryAfterMs(response, this.now(), this.retryAfterCapMs);
            if (fromHeader !== null) return fromHeader;
        }
        return this.baseBackoffMs * 2 ** (attempt - 1);
    }

    /**
     * One upstream attempt with a hard deadline covering both the response
     * headers and the body read, so a stalled upstream cannot hold an
     * in-flight entry (and its waiting callers) forever. Redirects are
     * refused: the allowlist validates the initial URL, and a redirect
     * target would escape that validation.
     */
    private async fetchUpstreamWithDeadline(url: URL): Promise<{ response: Response; body: string }> {
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout> | undefined;
        const work = (async () => {
            const response = await this.fetchImpl(url.toString(), {
                redirect: 'error',
                signal: controller.signal,
            });
            const body = await response.text();
            return { response, body };
        })();
        const deadline = new Promise<never>((_resolve, reject) => {
            timer = setTimeout(() => {
                controller.abort();
                reject(new Error(`Open-Meteo upstream timed out after ${this.upstreamTimeoutMs} ms`));
            }, this.upstreamTimeoutMs);
        });
        try {
            return await Promise.race([work, deadline]);
        } finally {
            if (timer !== undefined) clearTimeout(timer);
        }
    }

    private entryFromBody(response: Response, body: string, url: URL): CacheEntry {
        const storedAtMs = this.now();
        return {
            body,
            status: response.status,
            statusText: response.statusText,
            contentType: response.headers.get('content-type'),
            retryAfter: response.headers.get('retry-after'),
            storedAtMs,
            expiresAtMs: storedAtMs + openMeteoTtlMs(url),
            byteSize: textEncoder.encode(body).length,
        };
    }

    private store(key: string, entry: CacheEntry): void {
        const existing = this.entries.get(key);
        if (existing) {
            this.totalBytes -= existing.byteSize;
            this.entries.delete(key);
        }
        if (entry.byteSize > this.maxBytes) {
            // Too large to cache safely; the caller still gets the body.
            return;
        }
        this.entries.set(key, entry);
        this.totalBytes += entry.byteSize;
        while (this.entries.size > this.maxEntries || this.totalBytes > this.maxBytes) {
            const oldest = this.entries.keys().next();
            if (oldest.done) break;
            const evicted = this.entries.get(oldest.value);
            if (!evicted) break;
            this.entries.delete(oldest.value);
            this.totalBytes -= evicted.byteSize;
        }
    }

    private toResponse(entry: CacheEntry, cacheStatus: 'HIT' | 'MISS' | 'STALE'): Response {
        const headers = new Headers();
        if (entry.contentType) headers.set('content-type', entry.contentType);
        if (entry.retryAfter) headers.set('Retry-After', entry.retryAfter);
        headers.set('X-Cache', cacheStatus);
        headers.set('Age', String(Math.max(0, Math.floor((this.now() - entry.storedAtMs) / 1000))));
        // 204/205/304 responses must carry a null body; passing '' throws.
        const body = entry.status === 204 || entry.status === 205 || entry.status === 304 ? null : entry.body;
        return new Response(body, {
            status: entry.status,
            statusText: entry.statusText,
            headers,
        });
    }
}

const globalCacheSlot = globalThis as typeof globalThis & {
    __mysnowOpenMeteoCache?: OpenMeteoCache;
};

/** Process-wide cache instance shared by every proxy route invocation. */
export function getSharedOpenMeteoCache(): OpenMeteoCache {
    if (!globalCacheSlot.__mysnowOpenMeteoCache) {
        globalCacheSlot.__mysnowOpenMeteoCache = new OpenMeteoCache();
    }
    return globalCacheSlot.__mysnowOpenMeteoCache;
}
