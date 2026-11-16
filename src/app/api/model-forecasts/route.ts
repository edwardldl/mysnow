import { NextResponse } from 'next/server';
import { loadServedModelData } from '@/lib/model-data-source';

export const dynamic = 'force-dynamic';

/**
 * Serves the current ENS-EB serving snapshot to the browser: the
 * Blob-published snapshot when one is available and verified, else the
 * snapshot bundled with this deployment (see lib/model-data-source.ts).
 * The client compares the payload's cycles against its bundled copy
 * and swaps only on a difference, so this route is also the freshness
 * channel for batches published after the deployment was built.
 */
export async function GET() {
    const served = await loadServedModelData();
    const headers: Record<string, string> = {
        'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=1800',
        'X-Model-Data-Source': served.source,
    };
    if (served.cycleUtc) headers['X-Model-Cycle'] = served.cycleUtc;
    if (served.sha256) headers['X-Model-Sha256'] = served.sha256;
    return NextResponse.json(served.data, { headers });
}
