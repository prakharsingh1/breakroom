'use client';
import { useRef, useState } from 'react';
import Link from 'next/link';
import { api, Run, saveRecording } from '@/lib/contracts';
import { Checks, EvidencePair, Status } from './Evidence';
import ExportLink from './ExportLink';

export default function Demo({ caseId = 'refund-response-lost', compact = false }: { caseId?: string; compact?: boolean }) {
  const [runs, setRuns] = useState<Partial<Record<'faulty' | 'corrected', Run>>>({});
  const [selected, setSelected] = useState<'faulty' | 'corrected'>('faulty');
  const [pending, setPending] = useState<'faulty' | 'corrected' | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const controller = useRef<AbortController | null>(null);
  const run = runs[selected];
  if (process.env.NEXT_PUBLIC_BREAKROOM_SITE_ONLY === '1') return <section className="local-drill"><span className="eyebrow">RUN WITH THE PYTHON ENGINE</span>{compact ? <h3>Try the crash test locally</h3> : <h2>Try the crash test locally</h2>}<p>Hosted test execution is not connected to this website yet. Run the faulty and corrected reference agents locally, inspect their actual effects, and export a regression test.</p><Link className="button accent" href="/docs#quickstart">Open the quickstart →</Link></section>;
  async function execute(agent: 'faulty' | 'corrected') {
    setError(''); setNotice(''); setPending(agent);
    const request = new AbortController(); controller.current = request;
    try {
      const fresh = await api<Run>('runs', { agent, case_id: caseId }, request.signal);
      setRuns(previous => ({ ...previous, [agent]: fresh })); setSelected(agent);
      if (!saveRecording(fresh)) setNotice('Browser storage is unavailable. This run remains available here until you leave.');
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') setNotice('Stopped waiting. The bounded built-in worker may still finish on the local API.');
      else setError(error instanceof Error ? error.message : 'The run could not be completed.');
    } finally { if (controller.current === request) { setPending(null); controller.current = null; } }
  }
  return <div className="demo-shell" id="inspector" aria-busy={!!pending}>
    <div className="inspector-top"><div className="inspector-brand"><span className="live-dot" /> BREAKROOM / TEST ENVIRONMENT</div><span className="micro">Simulated services · Scripted agents</span></div>
    <div className="inspector-toolbar"><div className="run-switch" role="group" aria-label="Reference run selection">
      {(['faulty', 'corrected'] as const).map(agent => <button key={agent} className={selected === agent ? 'selected' : ''} onClick={() => setSelected(agent)} aria-pressed={selected === agent}>{agent === 'faulty' ? 'Faulty reference' : 'Corrected reference'}{runs[agent] && <span className={`small-dot ${runs[agent]?.report.verdict === 'PASS' ? 'green' : 'red'}`} />}</button>)}
    </div><button className="button accent small" onClick={() => execute(selected)} disabled={!!pending}>{pending ? 'Executing real trial…' : run ? '↻ Run again' : '▶ Run ' + selected + ' reference'}</button></div>
    <div className="sr-only" role="status">{pending ? 'Executing scripted reference agent in a new isolated simulator.' : run ? `Run complete. Business verdict ${run.report.verdict}.` : ''}</div>
    {pending && <div className="running-notice" role="status">A new isolated trial is executing. <button onClick={() => controller.current?.abort()}>Stop waiting</button></div>}
    {error && <div className="inline-error" role="alert"><strong>Run unavailable</strong><p>{error}</p><Link href="/docs#quickstart">Open local quickstart →</Link></div>}
    {notice && <p className="inline-notice" role="status">{notice}</p>}
    {run ? <>
      <div className="run-banner"><div><span className="micro">Newly executed run · {new Date(run.created_at).toLocaleTimeString()}</span><p>Execution <strong>{run.report.execution.status}</strong><span className="banner-divider">/</span>Business verdict <Status value={run.report.verdict} /></p></div><Link className="text-link" href={`/runs/${run.id}`}>Inspect full evidence ↗</Link></div>
      <EvidencePair report={run.report} headingLevel={compact ? 3 : 2} />
      {!compact && <div className="demo-checks"><h2>Release Checks</h2><Checks checks={run.report.checks} /></div>}
    </> : <div className="evidence-pair ready-pair"><section className="agent-pane"><header><span className="pane-number">01</span>{compact ? <h3>What the agent saw</h3> : <h2>What the agent saw</h2>}</header><div className="ready-content"><span className="terminal-label">AGENT VIEW</span><div className="ready-prompt"><span>›</span><p>Refund ₹1,000 from a ₹5,000 order.<br />Then update the support ticket.</p></div><p className="muted">Execute a reference agent to inspect its actual tool calls and responses.</p></div></section><section className="effects-pane"><header><span className="pane-number">02</span>{compact ? <h3>What actually happened</h3> : <h2>What actually happened</h2>}</header><div className="ready-content"><span className="terminal-label">INDEPENDENT EVIDENCE</span><div className="empty-record"><span>∅</span><div><strong>No run yet</strong><p>Committed records will appear here.</p></div></div><p className="muted">The evaluator checks the fake business state independently of the agent’s final answer.</p></div></section></div>}
    <div className="inspector-bottom"><span>Real test executions. Simulated customers and money.</span><div>{run && <ExportLink id={run.id} />}{runs.faulty && runs.corrected ? <Link className="text-link" href={`/compare?baseline=${runs.faulty.id}&candidate=${runs.corrected.id}`}>Compare runs →</Link> : <span className="muted">Run both references to compare</span>}</div></div>
    <div className="reference-note">The corrected scripted reference reuses the original operation key and retries only the failed step. It does not modify your agent.</div>
  </div>;
}
