'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Project, teamApi, TeamSession, useTeamSession } from '@/lib/team';
import AccountEntry from './AccountEntry';
import { Status } from './Evidence';

type Counts = { total: number; PASS: number; FAIL: number; INCONCLUSIVE: number; UNSUPPORTED: number };
type Workspace = { projects: (Project & { report_counts: Counts; suite_count: number; last_report_at: string | null })[]; totals: Omit<Counts, 'total'> & { projects: number; reports: number }; recent_reports: { id: string; project_id: string; project_name: string; case_id: string; verdict: string; created_at: string }[]; notice: string };
export function TeamUnavailable({ error }: { error: string }) {
  return <div className="light-error"><p role="alert">{error}</p><p>Your saved data stays in your workspace. Refresh this page to reconnect.</p><Link href="/demo" className="text-link">Explore an example while you wait →</Link></div>;
}
export function TeamLogin({ session, onLogin }: { session: TeamSession; onLogin: () => Promise<unknown> }) { return <AccountEntry session={session} onLogin={onLogin} />; }
export default function TeamHome() {
  const { session, error, loading, refresh } = useTeamSession();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [workspaceError, setWorkspaceError] = useState('');
  const [name, setName] = useState(''); const [retention, setRetention] = useState(30); const [query, setQuery] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false);
  const loadWorkspace = useCallback(async () => { setWorkspaceError(''); try { setWorkspace(await teamApi<Workspace>('workspace')); } catch (e) { setWorkspaceError(e instanceof Error ? e.message : 'Unable to load workspace.'); } }, []);
  useEffect(() => { if (session?.user) void loadWorkspace(); else setWorkspace(null); }, [session, loadWorkspace]);
  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setNotice('');
    try { const project = await teamApi<Project>('projects', { method: 'POST', csrf: session?.csrf_token, body: { name, retention_days: retention } }); setName(''); await refresh(); setNotice('Created ' + project.name + '.'); }
    catch (e) { setNotice(e instanceof Error ? e.message : 'Project creation failed.'); } finally { setBusy(false); }
  }
  async function logout() { setBusy(true); try { await teamApi('auth/logout', { method: 'POST', csrf: session?.csrf_token }); setWorkspace(null); await refresh(); } catch (e) { setNotice(String(e)); } finally { setBusy(false); } }
  if (!session?.user) return <div className="wrap account-page"><aside className="account-story"><span className="eyebrow">THE WORKSPACE FOR YOUR AGENT</span><h1>From first test<br />to a better release<span>.</span></h1><p>Create a private project, connect your agent, and give every failure a place to be investigated.</p><Link href="/demo" className="text-link">See how a Fire Drill works →</Link></aside><div>{loading && <p role="status">Opening your workspace…</p>}{error && <TeamUnavailable error={error} />}{session && <TeamLogin session={session} onLogin={refresh} />}</div></div>;
  const projects = (workspace?.projects || []).filter(p => p.name.toLowerCase().includes(query.toLowerCase()));
  return <div className="wrap workspace-home">
    <div className="workspace-heading"><div><span className="eyebrow">WORKSPACE / OVERVIEW</span><h1>Welcome back, {session.user.display_name.split(' ')[0]}<span>.</span></h1><p>Your agents, their release checks, and the evidence behind them.</p></div><a className="button accent" href="#new-project">+ New project</a></div>
    <div className="workspace-identity"><span>{session.user.email}</span><div><Link href="/account">Account settings</Link><button onClick={logout} disabled={busy}>Sign out</button></div></div>
    {session.user.email_verified === false && <div className="verification-banner"><span><strong>Verify your email</strong> to accept team invitations and secure account recovery.</span><Link href="/account">Manage verification →</Link></div>}
    {notice && <p className="team-notice" role="status">{notice}</p>}
    {workspaceError && <div className="light-error"><p role="alert">{workspaceError}</p><button className="button outline" onClick={() => void loadWorkspace()}>Retry workspace</button></div>}
    {!workspace && !workspaceError && <p role="status">Loading your projects and release history…</p>}
    {workspace && <>
      <div className="workspace-stats" role="group" aria-label="Workspace report summary"><div><span>Private projects</span><strong>{workspace.totals.projects}</strong><small>Shared only with your team</small></div><div><span>Retained reports</span><strong>{workspace.totals.reports}</strong><small>From your explicit uploads</small></div><div><span>Failed reports</span><strong>{workspace.totals.FAIL}</strong><small>Failures stay visible</small></div><div><span>Unresolved reports</span><strong>{workspace.totals.INCONCLUSIVE + workspace.totals.UNSUPPORTED}</strong><small>Unknown or unsupported evidence</small></div></div>
      {!workspace.totals.reports && <section className="workspace-onboarding"><div><span className="eyebrow">MAKE IT YOURS</span><h2>Your first release check starts here.</h2><p>Run the agent you already have. Bring back the evidence you choose to share.</p></div><ol><li><span className={workspace.totals.projects ? 'step-number done' : 'step-number'}>{workspace.totals.projects ? '✓' : '01'}</span><div><strong>Create a private project</strong><p>Give your agent a home and choose who can access its reports.</p></div></li><li><span className="step-number">02</span><div><strong>Connect and run your agent</strong><p>Follow Connect agent inside your project to run a Fire Drill locally.</p></div></li><li><span className="step-number">03</span><div><strong>Upload and investigate</strong><p>Review a minimized report, inspect failed checks, and compare your correction.</p></div></li></ol></section>}
      <div className="workspace-columns"><section className="workspace-projects"><div className="workspace-section-title"><h2>Your projects</h2><label className="workspace-search"><span className="sr-only">Search projects</span><input type="search" placeholder="Search projects…" value={query} onChange={e => setQuery(e.target.value)} /></label></div><div className="project-list">{projects.map(p => <Link key={p.id} href={'/projects/' + p.id} className="workspace-project-row"><span className="project-avatar" aria-hidden="true">{p.name.slice(0,2).toUpperCase()}</span><div><h3>{p.name}</h3><p>{p.role} · {p.report_counts.total} reports · {p.suite_count} suites</p></div><span aria-hidden="true">↗</span></Link>)}</div>{!projects.length && <p className="workspace-empty">{query ? 'No projects match your search.' : 'No projects yet. Create your first one to begin.'}</p>}</section>
      <section id="new-project" className="workspace-create"><span className="eyebrow">A HOME FOR YOUR AGENT</span><h2>Create a private project</h2><form className="team-form" onSubmit={create}><label>Project name<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="Customer support agent" /></label><label>Keep reports for (days)<input type="number" min={1} max={365} required value={retention} onChange={e => setRetention(Number(e.target.value))} /></label><p>You can invite teammates and adjust retention after creating your project.</p><button className="button accent" disabled={busy}>{busy ? 'Creating…' : 'Create project →'}</button></form></section></div>
      <section className="workspace-recent"><div className="workspace-section-title"><h2>Recent reports</h2><span>Latest 10 retained uploads</span></div>{workspace.recent_reports.length ? workspace.recent_reports.map(r => <Link key={r.id} className="workspace-report-row" href={'/projects/'+r.project_id+'/reports/'+r.id}><Status value={r.verdict}/><div><strong>{r.case_id.replaceAll('-',' ')}</strong><small>{r.project_name}</small></div><time dateTime={r.created_at}>{new Date(r.created_at).toLocaleString()}</time><span aria-hidden="true">↗</span></Link>) : <div className="workspace-empty"><strong>Your release history will appear here.</strong><p>Upload a report from your project to start reviewing actual results.</p></div>}<p className="scope-note">Uploaded reports are customer-generated evidence. Summary counts include retained history, not a release approval.</p></section>
    </>}
  </div>;
}
