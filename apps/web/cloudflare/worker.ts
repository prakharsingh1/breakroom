import handler from 'vinext/server/fetch-handler';
import catalog from './catalog.json';

// This free deployment serves public documentation. The independently hosted
// Python API and gVisor worker are never replaced by simulated success here.
const headers = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'same-origin',
  'X-Frame-Options': 'DENY',
};

export async function publicApi(request: Request): Promise<Response | null> {
  const path = new URL(request.url).pathname;
  if (path !== '/api' && !path.startsWith('/api/')) return null;
  if (request.method === 'GET' && path === '/api/drills') {
    return Response.json({ drills: catalog }, { headers });
  }
  if (request.method === 'GET' && path.startsWith('/api/drills/')) {
    const id = path.slice('/api/drills/'.length);
    const drill = catalog.find(item => item.case_id === id);
    return Response.json(drill || { detail: 'This Fire Drill is not in the reviewed catalog.' }, { status: drill ? 200 : 404, headers });
  }
  const detail = path.startsWith('/api/team/')
    ? 'Hosted accounts and workspaces are not available on this deployment yet. You can run Breakroom locally using the quickstart in Docs.'
    : 'Hosted test execution is not available on this deployment yet. Run the Python engine locally using the quickstart in Docs.';
  return Response.json({ detail }, { status: 503, headers });
}

export default {
  async fetch(request: Request, env: Record<string, unknown>, ctx: { waitUntil(promise: Promise<unknown>): void }) {
    const apiResponse = await publicApi(request);
    if (apiResponse) return apiResponse;
    const page = await handler.fetch(request, env, ctx);
    const response = new Response(page.body, page);
    // Apply these on rendered responses too, not just the static asset rules.
    for (const [name, value] of Object.entries(headers)) {
      if (name !== 'Cache-Control') response.headers.set(name, value);
    }
    return response;
  },
};
