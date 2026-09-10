'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { teamApi, useTeamSession } from '@/lib/team';
import { TeamLogin, TeamUnavailable } from './Team';

export default function ProjectInvite() {
  const { session, loading, error: sessionError, refresh } = useTeamSession();
  const [token, setToken] = useState(''); const [checked, setChecked] = useState(false);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [projectId, setProjectId] = useState('');
  useEffect(() => { const value = new URLSearchParams(window.location.hash.slice(1)).get('token') || ''; setToken(value.length <= 512 ? value : ''); setChecked(true); }, []);
  async function accept() {
    setBusy(true); setError('');
    try { const result = await teamApi<{project_id: string}>('invitations/accept', { method: 'POST', csrf: session?.csrf_token, body: { token } }); setProjectId(result.project_id); setToken(''); window.history.replaceState(null, '', '/invite'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Invitation could not be accepted.'); }
    finally { setBusy(false); }
  }
  async function signOut() {
    setBusy(true); setError('');
    try { await teamApi('auth/logout', { method: 'POST', csrf: session?.csrf_token }); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Sign-out failed.'); }
    finally { setBusy(false); }
  }
  return <div className="wrap inner-page invite-page"><span className="eyebrow">BETTER RELEASES, TOGETHER</span><h1>{projectId ? 'You’re on the team.' : 'Join your team.'}</h1>{projectId ? <div className="workspace-soft"><h2>Invitation accepted</h2><p>Your project membership is ready.</p><Link href={'/projects/' + encodeURIComponent(projectId)} className="button accent">Open project →</Link></div> : <><p className="lead">Sign in with the email your teammate invited.</p>{!checked || loading ? <p role="status">Checking your account…</p> : !token ? <div className="light-error"><h2>Invitation link missing</h2><p>Open the full link shared by your project owner, including the part after #. If it has expired, ask for a new invitation.</p><Link href="/projects">Go to your workspace →</Link></div> : sessionError ? <TeamUnavailable error={sessionError} /> : session && !session.user ? <TeamLogin session={session} onLogin={refresh} /> : session?.user ? <section className="workspace-soft"><h2>Accept project access</h2><p>Signed in as <strong>{session.user.email}</strong>. The invitation must match this account’s verified email.</p>{session.user.auth_method === 'password' && !session.user.email_verified && <p className="account-notice">Verify your email before joining. <Link href="/account">Open account settings →</Link> Then reopen this invitation link.</p>}<div className="team-actions"><button className="button accent" disabled={busy || (session.user.auth_method === 'password' && !session.user.email_verified)} onClick={() => void accept()}>{busy ? 'Working…' : 'Accept invitation'}</button><button className="button outline" disabled={busy} onClick={() => void signOut()}>Use a different account</button></div></section> : null}{error && <p role="alert" className="light-error">{error}</p>}</>}</div>;
}
