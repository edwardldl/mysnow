import type { NextRequest } from 'next/server';
import { getSharedOpenMeteoCache, OpenMeteoAllowlistError } from '../../../lib/open-meteo-cache';

/**
 * Caching proxy for Open-Meteo (see src/lib/open-meteo-cache.ts).
 *
 * The browser calls /api/open-meteo?url=<encoded upstream URL> instead of
 * api.open-meteo.com directly, so upstream requests are deduped, retried
 * and cached server-side. The upstream URL is validated against a strict
 * Open-Meteo allowlist inside the cache module; this route is not an open
 * proxy.
 */

// Responses must come from the cache module, never from a static/ISR layer.
export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest): Promise<Response> {
    const upstream = request.nextUrl.searchParams.get('url');
    if (!upstream) {
        return Response.json({ error: 'Missing url query parameter' }, { status: 400 });
    }

    try {
        return await getSharedOpenMeteoCache().fetch(upstream);
    } catch (error) {
        if (error instanceof OpenMeteoAllowlistError) {
            return Response.json({ error: error.message }, { status: 400 });
        }
        console.error('Open-Meteo proxy upstream failure:', error);
        return Response.json({ error: 'Open-Meteo upstream request failed' }, { status: 502 });
    }
}
