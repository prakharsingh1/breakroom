import type { NextConfig } from 'next';

const apiOrigin = process.env.BREAKROOM_API_ORIGIN || 'http://127.0.0.1:8000';
const teamOrigin = process.env.BREAKROOM_TEAM_API_ORIGIN || 'http://127.0.0.1:8001';
const config: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  async rewrites() {
    if (process.env.BREAKROOM_CLOUDFLARE_BUILD === '1') return [];
    return [
      { source: '/api/team/:path*', destination: `${teamOrigin}/api/team/:path*` },
      { source: '/api/:path*', destination: `${apiOrigin}/api/:path*` }
    ];
  },
  async headers() {
    return [{ source: '/:path*', headers: [
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'Referrer-Policy', value: 'same-origin' },
      { key: 'X-Frame-Options', value: 'DENY' }
    ] }];
  }
};
export default config;
