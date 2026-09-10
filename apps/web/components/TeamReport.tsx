'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { teamApi, TeamReport as ReportRecord, useTeamSession } from '@/lib/team';
import { Checks, Status } from './Evidence';
import { TeamLogin, TeamUnavailable } from './Team';
import { money } from '@/lib/contracts';

export default function TeamReport({ projectId, reportId }: { projectId: string; reportId: string }) {
  const { session, error: sessionError, refresh } = useTeamSession();
  const [record, setRecord] = useState<ReportRecord | null>(null);
  const [error, setError] = useState(''); const [exporting, setExporting] = useState(false);
  const base = 'projects/' + encodeURIComponent(projectId) + '/reports/' + encodeURIComponent(reportId);
  useEffect(() => { if (session?.user) void teamApi<ReportRecord>(base).then(setRecord).catch(e => setError(e.message)); },[base,session]);
  async function download() {
    setExporting(true); setError('');
    try { const payload = await teamApi(base + '/export'); const url = URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)+'\n'], {type:'application/json'})); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'breakroom-customer-report.json'; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url),1000); }
    catch (e) { setError(e instanceof Error ? e.message : 'Export unavailable.'); }
    finally { setExporting(false); }
  }
  if (sessionError) return <div className="wrap inner-page"><TeamUnavailable error={sessionError} /></div>;
  if (session && !session.user) return <div className="wrap inner-page"><TeamLogin session={session} onLogin={refresh} /></div>;
  const report = record?.upload?.report;
  if (!record || !report) return <div className="wrap inner-page"><h1>{error ? 'Report unavailable' : 'Loading private evidence…'}</h1>{error && <p role="alert">{error}</p>}<Link href={'/projects/'+projectId}>Back to project →</Link></div>;
  return <div className="wrap inner-page team-page"><Link href={'/projects/'+projectId} className="back-link">← Project reports</Link><span className="eyebrow">PRIVATE UPLOAD / CUSTOMER-GENERATED</span><div className="section-heading"><h1>{record.case_id}</h1><Status value={record.verdict} /></div><p className="lead">Imported evidence. No agent was executed by this service.</p><div className="provenance-notice"><strong>Customer-generated report</strong><p>The checks and effects below are claims from a customer-owned worker. Schema and integrity validation do not certify that the reported execution happened honestly. This file was minimized before upload; excluded evidence cannot be recovered here.</p></div><p className="scope-note">Uploaded {new Date(record.created_at).toLocaleString()} · expires {new Date(record.expires_at).toLocaleString()} · reported execution: {report.execution.status}</p><button className="button accent" disabled={exporting} onClick={download}>{exporting ? 'Preparing download…' : 'Download minimized JSON'}</button>{error && <p role="alert">{error}</p>}<div className="investigator"><aside><span className="eyebrow">CUSTOMER-SUPPLIED CHECKS</span><Checks checks={report.checks} /></aside><section><h2>Reported effects</h2>{report.final_state.available === false || !Array.isArray(report.final_state.refunds) ? <p>Unknown: this upload does not establish refund records.</p> : report.final_state.refunds.length === 0 ? <p>The uploaded state contains no refund records.</p> : report.final_state.refunds.map(r => <div className="team-record" key={r.id}><strong>{money(r.amount_minor,r.currency)} · {r.status}</strong><p><code>{r.id}</code></p><p>Order <code>{r.order_id}</code> · operation <code>{r.logical_request_id}</code></p></div>)}<h2>Reported event sequence</h2>{report.events.map((event,index) => <details className="light-event" key={String(event.id || index)}><summary><code>{String(event.id || index)}</code> {String(event.kind || event.type || 'event')}</summary><pre>{JSON.stringify(event,null,2)}</pre></details>)}</section><section><h2>Contract & privacy</h2><div className="contract-meta"><span>Case v{report.case.case_version}</span><span>Engine {report.engine_version}</span><span>Oracle {report.oracle_version}</span><span>Seed {report.seed}</span></div>{(['initial_state','final_state'] as const).map(key => <details className="light-event" key={key}><summary>{key.replaceAll('_',' ')}</summary><pre>{JSON.stringify(report[key],null,2)}</pre></details>)}<details className="light-event"><summary>Minimization & original provenance</summary><pre>{JSON.stringify(record.privacy,null,2)}</pre></details><h3>Limitations</h3><ul>{report.limitations.map((l,i) => <li key={i}>{l}</li>)}</ul></section></div></div>;
}
