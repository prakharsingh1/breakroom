'use client';
import { useState } from 'react';
import { Invitation } from '@/lib/workspace';
import { teamApi } from '@/lib/team';

export default function ProjectInvitations({ projectId, csrf, items, refresh }: { projectId: string; csrf: string | null; items: Invitation[]; refresh: () => Promise<void> }) {
  const [email, setEmail] = useState(''); const [role, setRole] = useState<'viewer' | 'developer'>('viewer');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [link, setLink] = useState('');
  const base = 'projects/' + encodeURIComponent(projectId) + '/invitations';
  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(''); setNotice(''); setLink('');
    try { const result = await teamApi<Invitation>(base, { method: 'POST', csrf, body: { email, role, expires_days: 7 } }); if (!result.invite_url) throw new Error('Invitation link unavailable. Revoke this invitation and create a new one.'); const url = new URL(result.invite_url); if (url.origin !== window.location.origin || url.pathname !== '/invite' || !url.hash.startsWith('#token=')) throw new Error('The invitation origin does not match this workspace. Ask the operator to check its public URL.'); setLink(url.href); setEmail(''); await refresh(); setNotice('Invitation created. Share the link directly with your teammate. No email was sent.'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Invitation failed.'); }
    finally { setBusy(false); }
  }
  async function revoke(invite: Invitation) {
    setBusy(true); setError(''); setNotice('');
    try { await teamApi(base + '/' + encodeURIComponent(invite.id), { method: 'DELETE', csrf }); setLink(''); await refresh(); setNotice('Invitation revoked.'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Revocation failed.'); }
    finally { setBusy(false); }
  }
  return <div className="project-invitations"><h3>Invite a teammate</h3><p>Create a private, single-use link for their verified email. It expires in seven days.</p>{error && <p role="alert" className="light-error">{error}</p>}{notice && <p role="status" className="team-notice">{notice}</p>}<form className="team-form" onSubmit={create}><label>Invite email<input type="email" required maxLength={254} autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} /></label><label>Invitation role<select value={role} onChange={e => setRole(e.target.value as 'viewer' | 'developer')}><option value="viewer">Viewer — read reports</option><option value="developer">Developer — upload and manage suites</option></select></label><button className="button accent" disabled={busy}>{busy ? 'Working…' : 'Create invitation link'}</button></form>{link && <div className="invite-link-box"><label>New invitation link<input readOnly value={link} onFocus={e => e.target.select()} /></label><p>The link is shown only now. Send it yourself to the intended teammate.</p><div className="team-actions"><button className="button outline small" onClick={async () => { try { await navigator.clipboard.writeText(link); setNotice('Invitation link copied.'); } catch { setError('Clipboard unavailable. Select and copy the invitation link.'); } }}>Copy invitation link</button><button className="text-button" onClick={() => setLink('')}>Dismiss link</button></div></div>}{items.length > 0 && <div className="invitation-list"><h4>Invitations</h4>{items.map(invite => { const state = invite.accepted_at ? 'Accepted' : invite.revoked_at ? 'Revoked' : Date.parse(invite.expires_at) <= Date.now() ? 'Expired' : 'Pending'; return <article className="member-row" key={invite.id}><div><strong>{invite.email}</strong><small>{invite.role} · {state} · expires {new Date(invite.expires_at).toLocaleDateString()}</small></div>{state === 'Pending' && <button className="button outline small" disabled={busy} onClick={() => void revoke(invite)}>Revoke invitation for {invite.email}</button>}</article>; })}</div>}</div>;
}
