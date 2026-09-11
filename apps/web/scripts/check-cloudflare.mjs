import assert from 'node:assert/strict';

const origin = process.env.BREAKROOM_CHECK_ORIGIN || 'http://127.0.0.1:8787';
const get = (path, init) => fetch(new URL(path, origin), { signal: AbortSignal.timeout(20000), ...init });
const results = [];
for (const path of ['/', '/docs', '/pricing', '/demo', '/fire-drills', '/signup', '/signin', '/projects', '/projects/test-project', '/projects/test-project/reports/test-report', '/runs/test-run']) {
  const response = await get(path);
  assert.equal(response.status, 200, path);
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff', path);
  const html = await response.text();
  assert.ok(html.includes('Documentation is live.'), path);
  assert.ok(!html.includes('Executing real trial'), path);
  results.push({ path, status: response.status });
}
const catalog = await (await get('/api/drills')).json();
assert.equal(catalog.drills.length, 24);
assert.equal(new Set(catalog.drills.map(d => d.case_id)).size, 24);
for (const drill of catalog.drills) {
  assert.equal(drill.provenance, 'synthetic');
  assert.equal(drill.demo_available, false);
  const response = await get('/api/drills/' + drill.case_id);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), drill);
}
assert.equal((await get('/api/drills/unknown')).status, 404);
assert.equal((await get('/page-that-does-not-exist')).status, 404);
for (const [path, method] of [['/api/team/me', 'GET'], ['/api/team/auth/password/signup', 'POST'], ['/api/team/projects', 'POST'], ['/api/runs', 'POST'], ['/api/drills', 'POST'], ['/api/unknown', 'GET']]) {
  const response = await get(path, { method });
  assert.equal(response.status, 503, path);
  assert.equal(response.headers.get('cache-control'), 'no-store', path);
  assert.equal(response.headers.get('set-cookie'), null, path);
  assert.ok((await response.json()).detail.includes('not available on this deployment'), path);
}
console.log(JSON.stringify({ origin, pages: results, catalog_drills: 24, unavailable_service_checks: 6, passed: true }, null, 2));
