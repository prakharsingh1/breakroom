'use client';
import Link from 'next/link';
import { CatalogCase, ProjectInsights, reportHref } from '@/lib/workspace';
import { Status } from './Evidence';

export default function ProjectOverview({ id, insights, catalog, error, retry, onConnect, onSuites }: { id: string; insights: ProjectInsights | null; catalog: CatalogCase[]; error: string; retry: () => void; onConnect: () => void; onSuites: () => void }) {
  if (error) return <section className="team-section"><h2>Project overview</h2><div className="light-error" role="alert"><p>{error}</p><button className="button outline" onClick={retry}>Retry project insights</button></div></section>;
  if (!insights) return <p role="status">Loading project evidence…</p>;
  const names = new Map(catalog.map(c => [c.case_id, c.name]));
  const counts = insights.report_counts;
  const attention = insights.latest_cases.filter(c => c.verdict !== 'PASS');
  return <section className="team-section overview-section">
    <div className="section-heading"><div><span className="eyebrow">YOUR RELEASE WORKSPACE</span><h2>Project overview</h2></div><button className="button outline small" onClick={retry}>Refresh insights</button></div>
    <div className="workspace-stats" role="group" aria-label="Retained report counts">{[[counts.total, 'Reports retained'], [counts.PASS, 'Passing reports'], [counts.FAIL, 'Failing reports'], [counts.INCONCLUSIVE + counts.UNSUPPORTED, 'Need more evidence']].map(([value, label]) => <div className="workspace-stat" key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
    {!counts.total ? <div className="workspace-empty"><span className="empty-mark" aria-hidden="true">↗</span><h3>Your agent’s first release check starts here.</h3><p>Connect your local runner, choose the failures you care about, and bring back the evidence. This project is empty until you upload a report.</p><div className="team-actions"><button className="button accent" onClick={onConnect}>Connect your agent →</button><button className="button outline" onClick={onSuites}>Choose Fire Drills</button></div></div> : <div className="workspace-columns">
      <div><h3>What needs attention</h3>{attention.length ? <div className="attention-list">{attention.slice(0, 6).map(c => <article key={c.case_id} className="attention-card"><div className="attention-title"><Status value={c.verdict} /><h4>{names.get(c.case_id) || c.case_id}</h4></div>{c.recommendations.length ? <ul>{c.recommendations.map((text, i) => <li key={i}>{text}</li>)}</ul> : <p>Inspect this report’s failed or incomplete checks before your next release.</p>}<Link href={reportHref(id, c.report_id)}>Review evidence →</Link></article>)}</div> : <div className="workspace-soft"><h4>No failing latest reports.</h4><p>Check suite coverage before making a release decision. An untested or incompatible case still needs evidence.</p><button className="text-button" onClick={onSuites}>Review release suites →</button></div>}</div>
      <aside className="coverage-card"><span className="eyebrow">RETAINED COVERAGE</span><strong>{insights.coverage.reported_cases}<small> / {insights.coverage.catalog_cases}</small></strong><h3>Fire Drills with reports</h3><p>{insights.coverage.missing_case_ids.length} without a report. {insights.coverage.incompatible_case_ids.length} with incompatible evidence.</p><p className="scope-note">Coverage counts describe retained uploads. They do not establish that an agent is safe.</p><button className="button outline" onClick={onSuites}>Build a release suite</button></aside>
    </div>}
    <div className="suite-overview"><div className="section-heading"><h3>Release checks</h3><button className="text-button" onClick={onSuites}>Manage suites →</button></div>{insights.suites.length ? insights.suites.map(s => <div className="readiness-row" key={s.id}><div><strong>{s.name}</strong><small>{s.counts.PASS} passing · {s.counts.FAIL} failing · {s.counts.MISSING + s.counts.INCOMPATIBLE + s.counts.INCONCLUSIVE + s.counts.UNSUPPORTED} need evidence</small></div><Status value={s.status} /></div>) : <p className="scope-note">Choose required Fire Drills to see which release checks are still missing.</p>}</div>
    <p className="scope-note provenance-footnote">{insights.notice} Missing, unknown and incompatible evidence never becomes a passing release check.</p>
  </section>;
}
