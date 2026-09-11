'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { digestJson, Member, Project, Role, Suite, teamApi, TeamKey, TeamReport, TeamComparison, Upload, useTeamSession } from '@/lib/team';
import { Status } from './Evidence';
import { TeamLogin, TeamUnavailable } from './Team';
import CopyCommand from './CopyCommand';
import TeamBilling from './TeamBilling';
import ProjectOverview from './ProjectOverview';
import ProjectConnect from './ProjectConnect';
import ProjectSuites from './ProjectSuites';
import ProjectInvitations from './ProjectInvitations';
import ProjectSandbox from './ProjectSandbox';
import { CatalogCase, Invitation, ProjectInsights } from '@/lib/workspace';

type Tab = 'sandbox' | 'overview' | 'reports' | 'connect' | 'suites' | 'members' | 'keys' | 'settings' | 'billing';
export default function TeamProject({ id }: { id: string }) {
  const { session, error: sessionError, loading, refresh } = useTeamSession();
  const base = 'projects/' + encodeURIComponent(id);
  const [project, setProject] = useState<Project | null>(null);
  const [reports, setReports] = useState<TeamReport[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [keys, setKeys] = useState<TeamKey[]>([]);
  const [suites, setSuites] = useState<Suite[]>([]);
  const [insights, setInsights] = useState<ProjectInsights | null>(null); const [insightError, setInsightError] = useState('');
  const [catalog, setCatalog] = useState<CatalogCase[]>([]); const [catalogError, setCatalogError] = useState('');
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [query, setQuery] = useState(''); const [verdict, setVerdict] = useState('all'); const [sort, setSort] = useState('newest');
  const [tab, setTab] = useState<Tab>('reports');
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false);
  const [upload, setUpload] = useState<Upload | null>(null); const [fileName, setFileName] = useState('');
  const [memberEmail, setMemberEmail] = useState(''); const [memberRole, setMemberRole] = useState<Role>('viewer');
  const [keyName, setKeyName] = useState(''); const [keyScopes, setKeyScopes] = useState(['reports:write']); const [keyDays, setKeyDays] = useState(30); const [secret, setSecret] = useState(''); const [copied, setCopied] = useState(false);
  const [retention, setRetention] = useState(30); const [deleteName, setDeleteName] = useState(''); const [deleted, setDeleted] = useState(false);
  const [baseline, setBaseline] = useState(''); const [candidate, setCandidate] = useState(''); const [comparison, setComparison] = useState<TeamComparison | null>(null);

  const loadInsights = useCallback(async () => {
    setInsightError('');
    try { setInsights(await teamApi<ProjectInsights>(base + '/insights')); }
    catch (e) { setInsights(null); setInsightError(e instanceof Error ? e.message : 'Project insights are unavailable.'); }
  }, [base]);

  const load = useCallback(async () => {
    const p = await teamApi<Project>(base);
    const [r, m, s, k] = await Promise.all([
      teamApi<{items: TeamReport[]}>(base + '/reports'), teamApi<{items: Member[]}>(base + '/members'),
      teamApi<{items: Suite[]}>(base + '/suites'), p.role === 'owner' ? teamApi<{items: TeamKey[]}>(base + '/keys') : Promise.resolve({items: []})
    ]);
    setProject(p); setRetention(p.retention_days); setReports(r.items); setMembers(m.items); setSuites(s.items); setKeys(k.items);
    await loadInsights();
    if (p.role === 'owner') setInvitations((await teamApi<{items: Invitation[]}>(base + '/invitations')).items);
  }, [base, loadInsights]);
  useEffect(() => { if (session?.user) void load().catch(e => setError(e.message)); }, [session, load]);
  useEffect(() => { if (!session?.user) return; let active = true; void teamApi<{items: CatalogCase[]}>('catalog').then(data => { if (active) { setCatalog(data.items); setCatalogError(''); } }).catch(e => { if (active) setCatalogError(e.message); }); return () => { active = false; }; }, [session?.user]);
  async function act(operation: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('');
    try { await operation(); await load(); setNotice(message); }
    catch (e) { setError(e instanceof Error ? e.message : 'Request failed.'); }
    finally { setBusy(false); }
  }
  const mutate = (path: string, method: string, body?: unknown, headers?: Record<string,string>) => teamApi(base + path, { method, body, headers, csrf: session?.csrf_token });
  async function chooseFile(file: File | undefined) {
    setUpload(null); setError(''); setFileName('');
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { setError('Prepared reports must be at most 2 MiB.'); return; }
    try {
      const value = JSON.parse(await file.text());
      if (value.kind !== 'customer_generated_report' || value.privacy?.format !== 'breakroom-minimized-v1' || !value.report?.case || !Array.isArray(value.report?.checks)) throw new Error('Choose a minimized report produced by breakroom prepare-upload. Raw reports and archives are not accepted.');
      setUpload(value as Upload); setFileName(file.name);
    } catch (e) { setError(e instanceof Error ? e.message : 'Invalid prepared report.'); }
  }
  async function submitUpload(e: React.FormEvent) {
    e.preventDefault(); if (!upload) return;
    await act(async () => { const digest = await digestJson(upload); await mutate('/reports', 'POST', upload, { 'Idempotency-Key': digest }); setUpload(null); setFileName(''); }, 'Reviewed report uploaded. It is labeled customer-generated evidence.');
  }
  async function addMember(e: React.FormEvent) { e.preventDefault(); await act(async () => { await mutate('/members','POST',{email: memberEmail,role: memberRole}); setMemberEmail(''); },'Project membership updated.'); }
  async function createKey(e: React.FormEvent) {
    e.preventDefault(); await act(async () => { const key = await teamApi<TeamKey>(base + '/keys',{method:'POST',csrf:session?.csrf_token,body:{name:keyName,scopes:keyScopes,expires_days:keyDays}}); setSecret(key.secret || ''); setCopied(false); setKeyName(''); }, 'Key created. Save its secret now; it cannot be retrieved again.');
  }
  async function createSuite(name: string, cases: Suite['cases']) { let success = false; await act(async () => { await mutate('/suites','POST',{name,cases}); success = true; }, 'Private suite metadata saved.'); return success; }
  async function compare(e: React.FormEvent) { e.preventDefault(); setComparison(null); await act(async () => { const result = await teamApi<TeamComparison>(base + '/compare',{method:'POST',csrf:session?.csrf_token,body:{baseline_id:baseline,candidate_id:candidate}}); setComparison(result); },'Customer report comparison loaded.'); }
  async function removeProject(e: React.FormEvent) {
    e.preventDefault(); if (deleteName !== project?.name) return; setBusy(true); setError('');
    try { await mutate('', 'DELETE'); setSecret(''); setDeleted(true); }
    catch (e) { setError(e instanceof Error ? e.message : 'Deletion failed.'); }
    finally { setBusy(false); }
  }
  if (deleted) return <div className="wrap inner-page"><h1>Project deleted.</h1><p>Its active reports, keys, memberships and private suite metadata were removed. Operator-managed backup copies follow the backup retention policy.</p><Link href="/projects" className="button accent">All projects →</Link></div>;
  if (sessionError) return <div className="wrap inner-page"><TeamUnavailable error={sessionError} /></div>;
  if (session && !session.user) return <div className="wrap inner-page"><TeamLogin session={session} onLogin={refresh} /></div>;
  if (!project) return <div className="wrap inner-page"><h1>{error ? 'Project unavailable' : 'Loading project…'}</h1>{error && <p role="alert">{error}</p>}{loading && <p role="status">Checking your membership…</p>}<Link href="/projects">All projects →</Link></div>;
  const owner = project.role === 'owner'; const writer = project.role !== 'viewer';
  const caseNames = new Map(catalog.map(c => [c.case_id, c.name]));
  const visibleReports = reports.filter(r => (verdict === 'all' || r.verdict === verdict) && [r.case_id, caseNames.get(r.case_id) || '', r.id].join(' ').toLowerCase().includes(query.trim().toLowerCase())).sort((a, b) => sort === 'oldest' ? Date.parse(a.created_at) - Date.parse(b.created_at) : sort === 'case' ? (caseNames.get(a.case_id) || a.case_id).localeCompare(caseNames.get(b.case_id) || b.case_id) : Date.parse(b.created_at) - Date.parse(a.created_at));
  return <div className="wrap inner-page team-page">
    <Link href="/projects" className="back-link">← All projects</Link><div className="project-heading"><div><span className="eyebrow">PRIVATE TEAM PROJECT / {project.role}</span><h1>{project.name}<span>.</span></h1><p className="lead">Your agent’s evidence, ready for the next release.</p></div><div className="project-context"><span className="project-privacy">Private workspace</span><span>{members.length} {members.length === 1 ? 'member' : 'members'} · {project.retention_days}-day report retention</span></div></div>
    <nav className="team-tabs" aria-label="Project sections">{(['overview','sandbox','reports','connect','suites','members','keys','settings',...(owner?['billing']:[])] as Tab[]).map(t => <button aria-current={tab === t ? 'page' : undefined} onClick={() => {setTab(t);setError('');setNotice('');}} key={t}>{{sandbox:'Agent sandbox',overview:'Overview',reports:'Reports',connect:'Connect agent',suites:'Private suites',members:'Members',keys:'API keys',settings:'Settings',billing:'Billing'}[t]}</button>)}</nav>
    {error && <div className="light-error" role="alert">{error}</div>}{notice && <p className="team-notice" role="status">{notice}</p>}
    {secret && <section className="one-time-key" aria-label="New API key"><h2>Save this key once</h2><p>Store it in your CI secret manager. This page keeps it in memory only until you dismiss it or leave.</p><code>{secret}</code><div className="team-actions"><button className="button accent" onClick={async () => { try { await navigator.clipboard.writeText(secret); setCopied(true); } catch { setError('Clipboard unavailable. Select and copy the key.'); } }}>{copied ? 'Copied' : 'Copy secret'}</button><button className="button outline" onClick={() => {setSecret('');setNotice('Key secret dismissed. It is not retrievable.');}}>I’ve saved this key</button></div></section>}
    {tab === 'sandbox' && <ProjectSandbox projectId={id} csrf={session?.csrf_token || null} role={project.role} catalog={catalog} />}
    {tab === 'billing' && owner && <TeamBilling projectId={id} csrf={session?.csrf_token||null} />}
    {tab === 'overview' && <ProjectOverview id={id} insights={insights} catalog={catalog} error={insightError} retry={() => void loadInsights()} onConnect={() => setTab('connect')} onSuites={() => setTab('suites')} />}
    {tab === 'connect' && <ProjectConnect projectId={id} canManageKeys={owner} onKeys={() => setTab('keys')} onUpload={() => setTab('reports')} />}
    {tab === 'reports' && <>
      <section className="team-section"><div className="section-heading"><div><h2>Uploaded reports</h2><p className="scope-note">Customer-generated evidence, shared only with this project.</p></div><span className="scope-note">{reports.length} retained {reports.length === 1 ? 'report' : 'reports'}</span></div>{reports.length ? <><div className="report-filters"><label>Search reports<input type="search" placeholder="Find a drill or report…" value={query} onChange={e => setQuery(e.target.value)} /></label><label>Report verdict<select value={verdict} onChange={e => setVerdict(e.target.value)}><option value="all">All verdicts</option>{['PASS','FAIL','INCONCLUSIVE','UNSUPPORTED'].map(v => <option key={v}>{v}</option>)}</select></label><label>Sort reports<select value={sort} onChange={e => setSort(e.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="case">Fire Drill name</option></select></label></div><p className="report-filter-count" role="status">Showing {visibleReports.length} of {reports.length} reports</p><div className="project-report-list">{visibleReports.map(r => <Link className="project-report-row" key={r.id} href={'/projects/' + id + '/reports/' + r.id}><Status value={r.verdict} /><div><strong>{caseNames.get(r.case_id) || r.case_id}</strong><small>{r.case_id} · {new Date(r.created_at).toLocaleString()}</small></div><span aria-hidden="true">↗</span></Link>)}</div>{!visibleReports.length && <div className="workspace-soft"><h3>No reports match these filters.</h3><button className="text-button" onClick={() => {setQuery('');setVerdict('all');}}>Clear report filters</button></div>}</> : <div className="workspace-empty"><span className="empty-mark" aria-hidden="true">↗</span><h3>Bring your first test result.</h3><p>Run your agent against a Fire Drill, prepare a private sharing copy, then upload it below. Your team’s evidence will appear here.</p><div className="team-actions"><button className="button accent" onClick={() => setTab('connect')}>Connect your agent →</button><button className="button outline" onClick={() => setTab('suites')}>Choose Fire Drills</button></div></div>}</section>
      <section className="team-section"><h2>Review an explicit upload</h2><p>Prepare one report locally. The minimizer removes raw messages, customer contacts, source paths and unrestricted tool payloads. Review the resulting file: automated redaction is imperfect.</p><CopyCommand command="breakroom prepare-upload ./artifacts/candidate --case refund-response-lost --out ./artifacts/prepared-report.json" />{writer ? <form className="team-form" onSubmit={submitUpload}><label>Prepared JSON report (maximum 2 MiB)<input type="file" accept="application/json,.json" onChange={e => {void chooseFile(e.target.files?.[0]);}} /></label>{upload && <div className="upload-preview"><h3>Review before upload</h3><p>{fileName} · {upload.report.case.case_id} · {upload.report.verdict}</p><p>No data has been uploaded by selecting this file.</p><details><summary>Inspect the prepared JSON</summary><pre>{JSON.stringify(upload,null,2)}</pre></details><button className="button accent" disabled={busy}>{busy ? 'Uploading…' : 'Upload reviewed report'}</button><button type="button" className="button outline" onClick={() => {setUpload(null);setFileName('');}}>Discard selection</button></div>}</form> : <p className="scope-note">Your viewer role can read reports. A developer or owner can upload.</p>}</section>
      <section className="team-section"><h2>Compare retained reports</h2><p>Only compatible cases, fixtures, seeds and evaluation contracts can be compared. Uploaded verdicts remain customer-supplied claims.</p><form className="compare-form" onSubmit={compare}><label>Baseline<select value={baseline} required onChange={e => setBaseline(e.target.value)}><option value="">Choose a report</option>{reports.map(r => <option key={r.id} value={r.id}>{r.case_id} · {r.verdict} · {new Date(r.created_at).toLocaleTimeString()}</option>)}</select></label><label>Candidate<select value={candidate} required onChange={e => setCandidate(e.target.value)}><option value="">Choose a report</option>{reports.map(r => <option key={r.id} value={r.id}>{r.case_id} · {r.verdict} · {new Date(r.created_at).toLocaleTimeString()}</option>)}</select></label><button className="button accent" disabled={busy || reports.length < 2}>Compare reports</button></form>{comparison && <div className="team-comparison"><h3>{comparison.compatible ? 'Compatible report contracts' : 'Incompatible report contracts'}</h3><p>These are imported customer-generated results, not a new independent execution.</p><p>{comparison.baseline_trial_count} baseline / {comparison.candidate_trial_count} candidate trials.</p>{comparison.pairs.map((pair,i) => <section key={i}><h3>{pair.case_id} · seed {pair.seed}</h3><div className="team-actions"><Status value={pair.baseline_verdict} /><span aria-hidden="true">→</span><Status value={pair.candidate_verdict} /></div>{pair.changed_checks?.length ? pair.changed_checks.map(check => <div className="changed-check" key={check.id}><strong>{check.id.replaceAll("_"," ")}</strong><Status value={check.baseline || "unknown"} /><span aria-hidden="true">→</span><Status value={check.candidate || "unknown"} /></div>) : <p>No check statuses changed.</p>}</section>)}<details><summary>Inspect comparison data</summary><pre>{JSON.stringify(comparison,null,2)}</pre></details></div>}</section>
    </>}
    {tab === 'suites' && <ProjectSuites id={id} suites={suites} insights={insights} catalog={catalog} catalogError={catalogError} writer={writer} busy={busy} onCreate={createSuite} onDelete={s => void act(() => mutate('/suites/' + s.id,'DELETE'),'Suite metadata deleted.')} />}
    {tab === 'members' && <section className="team-section"><h2>Project members</h2><p>Viewer: read evidence. Developer: upload reports and manage private suites. Owner: manage members, keys, retention and deletion.</p>{members.map(m => <div className="member-row" key={m.user_id}><div><strong>{m.email}</strong><small>{m.display_name}</small></div>{owner ? <><label className="sr-only" htmlFor={'role-'+m.user_id}>Role for {m.email}</label><select id={'role-'+m.user_id} value={m.role} disabled={busy} onChange={e => void act(() => mutate('/members/'+m.user_id,'PATCH',{role:e.target.value}),'Role updated.')}><option>viewer</option><option>developer</option><option>owner</option></select><button className="button outline small" disabled={busy} onClick={() => void act(() => mutate('/members/'+m.user_id,'DELETE'),'Membership removed.')}>Remove {m.email}</button></> : <span>{m.role}</span>}</div>)}{owner && <ProjectInvitations projectId={id} csrf={session?.csrf_token || null} items={invitations} refresh={load} />}{owner && <form className="team-form" onSubmit={addMember}><h3>Add an existing account</h3><p>The member must have signed in already with a verified account. This action grants access without sending an invitation.</p><label>Member email<input required type="email" value={memberEmail} onChange={e => setMemberEmail(e.target.value)} /></label><label>Project role<select value={memberRole} onChange={e => setMemberRole(e.target.value as Role)}><option>viewer</option><option>developer</option><option>owner</option></select></label><button className="button accent" disabled={busy}>Add member</button></form>}</section>}
    {tab === 'keys' && <section className="team-section"><h2>Scoped API keys</h2><p>Keys only authorize their selected project and scopes. Keep them in an environment variable or CI secret, never a URL.</p>{!owner ? <p>Only a project owner can manage keys.</p> : <>{keys.map(k => <article className="team-record" key={k.id}><h3>{k.name}</h3><p><code>{k.prefix}…</code> · {k.scopes.join(', ')} · {k.revoked_at ? 'Revoked' : 'Expires '+new Date(k.expires_at).toLocaleDateString()}</p>{!k.revoked_at && <button className="button outline small" disabled={busy} onClick={() => void act(() => mutate('/keys/'+k.id,'DELETE'),'Key revoked.')}>Revoke {k.name}</button>}</article>)}<form className="team-form" onSubmit={createKey}><label>Key name<input required maxLength={120} value={keyName} onChange={e => setKeyName(e.target.value)} placeholder="CI upload" /></label><fieldset><legend>Allowed scopes</legend>{['reports:read','reports:write','suites:write'].map(scope => <label className="checkbox-label" key={scope}><input type="checkbox" checked={keyScopes.includes(scope)} onChange={e => setKeyScopes(e.target.checked ? [...keyScopes,scope] : keyScopes.filter(s => s!==scope))} />{scope}</label>)}</fieldset><label>Expires in (days)<input type="number" min={1} max={365} required value={keyDays} onChange={e => setKeyDays(Number(e.target.value))} /></label><button className="button accent" disabled={busy || !keyScopes.length}>Create scoped key</button></form></>}</section>}
    {tab === 'settings' && <section className="team-section"><h2>Retention & deletion</h2>{!owner ? <p>Only a project owner can change retention or delete this project.</p> : <><form className="team-form" onSubmit={e => {e.preventDefault();void act(() => mutate('','PATCH',{retention_days:retention}),'Retention updated.');}}><label>Report retention (days)<input type="number" required min={1} max={365} value={retention} onChange={e => setRetention(Number(e.target.value))} /></label><p>Shortening retention can expire existing reports immediately. Expired reports and their exports become inaccessible.</p><button className="button accent" disabled={busy}>Save retention</button><button type="button" className="button outline" disabled={busy} onClick={() => void act(() => mutate('/retention/cleanup','POST'),'Expired reports removed.')}>Delete expired reports now</button></form><form className="team-form danger-zone" onSubmit={removeProject}><h3>Delete {project.name}</h3><p>Deletes this project’s reports, exports, API keys, memberships and private suite metadata from active storage. This cannot be undone here. Operator-managed backups follow a separate retention policy.</p><label>Type the project name to confirm<input value={deleteName} onChange={e => setDeleteName(e.target.value)} autoComplete="off" /></label><button className="button danger" disabled={busy || deleteName !== project.name}>Delete project permanently</button></form></>}</section>}
  </div>;
}
