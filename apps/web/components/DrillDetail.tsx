'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api, Drill, money } from '@/lib/contracts';
import Demo from './Demo';
import CopyCommand from './CopyCommand';

export default function DrillDetail({ slug }: { slug: string }) {
  const [drill, setDrill] = useState<Drill | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { api<Drill>('drills/' + encodeURIComponent(slug)).then(setDrill).catch(e => setError(e.message)); }, [slug]);
  if (error) return <div className="wrap inner-page"><h1>Drill unavailable</h1><p role="alert">{error}</p><Link href="/fire-drills">Back to the library →</Link></div>;
  if (!drill) return <div className="wrap inner-page" role="status">Loading drill contract…</div>;
  const observations = drill.expectations?.observations || [];
  const tasks = drill.execution_plan?.tasks;
  const uniqueRequests = tasks ? new Set(tasks.map(task => task.request_id)).size : 1;
  const runCommand = 'breakroom run --agent breakroom.agents:corrected --pack support-refunds --case ' + drill.case_id + ' --out ./artifacts/' + drill.case_id;
  const controlCommand = 'breakroom mutation-check --pack support-refunds --case ' + drill.case_id + ' --out ./artifacts/controls-' + drill.case_id;
  const exportCommand = 'breakroom export-case ./artifacts/' + drill.case_id + ' --case ' + drill.case_id + ' --out ./regressions/' + drill.case_id + '\npython ./regressions/' + drill.case_id + '/test_regression.py --agent breakroom.agents:corrected';
  return <div className="wrap inner-page">
    <Link className="back-link" href="/fire-drills">← All Fire Drills</Link>
    <span className="eyebrow">FIRE DRILL {String(drill.number).padStart(2, '0')} / {drill.provenance}</span>
    <h1>{drill.name}<span>.</span></h1><p className="lead">{drill.summary}</p>
    <div className="contract-grid">
      <div><span className="micro">CONTRACT</span><strong>v{drill.case_version}</strong><p>Pack {drill.pack_version} · {drill.severity}</p></div>
      <div><span className="micro">REQUEST</span><strong>{money(drill.task.amount_minor, drill.task.currency)}</strong><p>{uniqueRequests} distinct logical {uniqueRequests === 1 ? 'request' : 'requests'} · {drill.execution_plan?.mode || 'single'} dispatch</p></div>
      <div><span className="micro">CHALLENGE</span><strong>{drill.faults.length ? drill.faults[0].phase.replaceAll('_', ' ') : observations.length ? 'Required behavior' : 'Ordinary control'}</strong><p>{drill.faults.map(f => f.tool + ', call ' + f.invocation).join('; ') || observations.map(o => o.replaceAll('_', ' ')).join(', ') || 'No injected fault'}</p></div>
    </div>
    <div className="detail-contract">
      <section><h2>Independent assertions</h2><ul>{drill.assertions.map(a => <li key={a}>{a.replaceAll('_', ' ')}</li>)}</ul>{observations.length > 0 && <><h3>Required observations</h3><ul>{observations.map(o => <li key={o}><code>{o}</code></li>)}</ul></>}</section>
      <section><h2>Scope & compatibility</h2><p>{drill.compatibility}</p><p>Required: {drill.required_capabilities.join(', ')}. Allowed outcomes: {drill.allowed_outcomes.join(', ')}.</p><ul>{drill.limitations.map(l => <li key={l}>{l}</li>)}</ul><p>Supported seeds: {drill.variation_constraints.supported_seeds.join(', ')}.</p></section>
    </div>
    {drill.demo_available ? <Demo caseId={slug} /> : <section className="local-drill"><span className="eyebrow">LOCAL ENGINE / SCRIPTED CONTROLS</span><h2>Run this drill locally</h2><p>The browser demo runs five initial drills. This drill uses the full local engine. Install the core using the <Link href="/docs#quickstart">local quickstart</Link>, then execute its corrected reference and independent checks:</p><CopyCommand command={runCommand} /><p>Execute the declared negative controls ({drill.negative_controls.join(', ')}) alongside the corrected reference. The resulting matrix records actual failures and whether the required challenge was exercised.</p><CopyCommand command={controlCommand} /><h3>Export a runnable regression</h3><CopyCommand command={exportCommand} /><p>The corrected reference is a scripted integration example. Use your own local adapter to evaluate your application; swapping references does not change your code.</p></section>}
  </div>;
}
