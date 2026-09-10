'use client';
import { useState } from 'react';
import Link from 'next/link';
import { Suite } from '@/lib/team';
import { CatalogCase, ProjectInsights, reportHref } from '@/lib/workspace';
import { Status } from './Evidence';

export default function ProjectSuites({ id, suites, insights, catalog, catalogError, writer, busy, onCreate, onDelete }: { id: string; suites: Suite[]; insights: ProjectInsights | null; catalog: CatalogCase[]; catalogError: string; writer: boolean; busy: boolean; onCreate: (name: string, cases: Suite['cases']) => Promise<boolean>; onDelete: (suite: Suite) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [query, setQuery] = useState('');
  const [advancedName, setAdvancedName] = useState('');
  const [advancedCases, setAdvancedCases] = useState('');
  const [error, setError] = useState('');
  const visible = catalog.filter(c => [c.name, c.summary, c.case_id, ...c.tags].join(' ').toLowerCase().includes(query.trim().toLowerCase()));
  const names = new Map(catalog.map(c => [c.case_id, c.name]));
  async function create(e: React.FormEvent) {
    e.preventDefault(); setError('');
    const cases = catalog.filter(c => selected.includes(c.case_id)).map(({ case_id, case_version, manifest_hash }) => ({ case_id, case_version, manifest_hash }));
    if (!cases.length) { setError('Choose at least one Fire Drill.'); return; }
    if (await onCreate(name, cases)) { setName(''); setSelected([]); }
  }
  async function advanced(e: React.FormEvent) {
    e.preventDefault(); setError('');
    try { const cases = JSON.parse(advancedCases); if (!Array.isArray(cases)) throw new Error('Case metadata must be a JSON array.'); if (await onCreate(advancedName, cases)) { setAdvancedName(''); setAdvancedCases(''); } }
    catch (e) { setError(e instanceof Error ? e.message : 'Check your JSON array.'); }
  }
  return <section className="team-section"><div className="section-heading"><div><span className="eyebrow">MAKE EVERY REQUIRED CASE COUNT</span><h2>Release suites</h2></div><span className="scope-note">{suites.length} private {suites.length === 1 ? 'suite' : 'suites'}</span></div><p>Choose the Fire Drills your agent needs to pass. Suite checks use the latest compatible retained upload for each pinned case.</p><p className="scope-note">Uploads may come from different agent builds. This evidence snapshot is not a release certification.</p>
    {!suites.length && <div className="workspace-soft"><h3>No release suites yet.</h3><p>Select your required cases below. A suite stays inconclusive until it has evidence for every required case.</p></div>}
    {suites.map(s => { const readiness = insights?.suites.find(r => r.id === s.id); return <article className="team-record release-suite" key={s.id}><div className="section-heading"><h3>{s.name}</h3><Status value={readiness?.status || 'INCONCLUSIVE'} /></div><p>{s.cases.length} required {s.cases.length === 1 ? 'case' : 'cases'} · {readiness ? `${readiness.counts.PASS} passing · ${readiness.counts.FAIL} failing · ${readiness.counts.MISSING} missing` : 'Coverage unavailable; no passing result established.'}</p>{readiness && <ul className="suite-case-list">{readiness.cases.map(c => <li key={c.case_id}><div>{c.report_id ? <Link href={reportHref(id, c.report_id)}>{names.get(c.case_id) || c.case_id} →</Link> : <span>{names.get(c.case_id) || c.case_id}</span>}<small>v{c.case_version}</small></div><Status value={c.status} /></li>)}</ul>}<details><summary>Versions and manifest hashes</summary><pre>{JSON.stringify(s.cases, null, 2)}</pre></details>{writer && <button className="button outline small" disabled={busy} onClick={() => onDelete(s)}>Delete suite {s.name}</button>}</article>; })}
    {writer && <><form className="suite-builder" onSubmit={create}><div className="section-heading"><div><h3>Build a release suite</h3><p>Pick from the versioned support refund pack.</p></div><span className="selection-count">{selected.length} selected</span></div>{error && <p className="light-error" role="alert">{error}</p>}<div className="suite-controls"><label>Release suite name<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="Before our next release" /></label><label>Find a Fire Drill<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Retries, permissions, pending…" /></label></div>{catalogError ? <p className="light-error" role="alert">{catalogError}</p> : !catalog.length ? <p role="status">Loading versioned Fire Drills…</p> : <><div className="team-actions selection-actions"><button type="button" className="text-button" onClick={() => setSelected(catalog.map(c => c.case_id))}>Select all {catalog.length} drills</button><button type="button" className="text-button" onClick={() => setSelected([])}>Clear selection</button></div><fieldset className="drill-picker"><legend className="sr-only">Required Fire Drills</legend>{visible.map(c => <label className={'drill-choice' + (selected.includes(c.case_id) ? ' selected' : '')} key={c.case_id}><input type="checkbox" checked={selected.includes(c.case_id)} onChange={e => setSelected(e.target.checked ? [...selected, c.case_id] : selected.filter(value => value !== c.case_id))} /><div><span className="drill-choice-title">{c.name}</span><span className="drill-choice-summary">{c.summary}</span><span className="drill-choice-meta">{c.severity} · v{c.case_version}</span></div></label>)}</fieldset>{!visible.length && <p>No Fire Drills match that search.</p>}</>}<div className="builder-footer"><p>Case versions and hashes are pinned when you save. Saving a suite does not run your agent.</p><button className="button accent" disabled={busy || !selected.length}>{busy ? 'Saving…' : 'Save release suite'}</button></div></form>
    <section className="advanced-suite"><span className="eyebrow">CUSTOM CASES</span><h3>Private suite metadata</h3><p>Already maintain a private pack? Save its IDs, versions and hashes here. Case contents stay on your worker.</p><form className="team-form" onSubmit={advanced}><label>Suite name<input required maxLength={120} value={advancedName} onChange={e => setAdvancedName(e.target.value)} /></label><label>Case metadata (JSON array)<textarea required rows={4} maxLength={64000} value={advancedCases} onChange={e => setAdvancedCases(e.target.value)} placeholder={'[{"case_id":"private-case","case_version":"1.0.0","manifest_hash":"64-character SHA-256 hash"}]'} /></label><button className="button outline" disabled={busy}>Save private suite</button></form></section></>}
  </section>;
}
