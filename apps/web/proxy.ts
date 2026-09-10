import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

export function proxy(request: NextRequest) {
  const headers = new Headers(request.headers);
  // A caller cannot supply the trusted local reverse-proxy credential. This
  // setting is server-only and is never included in a browser response.
  headers.delete('X-Breakroom-Local-Proxy');
  const secret = process.env.BREAKROOM_TEAM_LOCAL_PROXY_SECRET;
  if (secret) headers.set('X-Breakroom-Local-Proxy', secret);
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: '/api/team/:path*' };
