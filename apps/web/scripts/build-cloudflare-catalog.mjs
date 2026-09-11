import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Only the checked-in synthetic pack is publishable; no API, customer uploads,
// local database, or runtime reports are read during a website build.
const source = new URL('../../packages/breakroom-core/src/breakroom/data/support-refunds/', new URL('../', import.meta.url));
const names = (await readdir(source)).filter(name => name.endsWith('.json')).sort();
if (names.length !== 24) throw new Error('Review the public catalog before changing its 24-drill scope.');
const drills = await Promise.all(names.map(async name => {
  const drill = JSON.parse(await readFile(new URL(name, source), 'utf8'));
  if (drill.provenance !== 'synthetic' || `${drill.case_id}.json` !== name) throw new Error(`Unreviewed public drill: ${name}`);
  return { ...drill, demo_available: false };
}));
drills.sort((a, b) => a.number - b.number);
const destination = new URL('../cloudflare/catalog.json', import.meta.url);
await mkdir(new URL('./', destination), { recursive: true });
await writeFile(destination, JSON.stringify(drills));
console.log(`Prepared ${drills.length} reviewed public drills: ${fileURLToPath(destination)}`);
