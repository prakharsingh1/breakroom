'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { teamApi, useTeamSession } from '@/lib/team';
import { AuthConfig } from './AccountEntry';
import { TeamLogin, TeamUnavailable } from './Team';

export default function AccountSettings() {
  const { session, error, loading, refresh } = useTeamSession();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [current, setCurrent] = useState(''); const [password, setPassword] = useState(''); const [confirmation, setConfirmation] = useState('');
  const [notice, setNotice] = useState(''); const [actionError, setActionError] = useState(''); const [busy, setBusy] = useState(false);
  useEffect(() => { void teamApi<AuthConfig>('auth/config').then(setConfig).catch(e => setActionError(e.message)); }, []);
  async function verify() {
    setBusy(true); setNotice(''); setActionError('');
    try { const result = await teamApi<{verification_sent?:boolean}>('auth/request-verification', { method:'POST',csrf:session?.csrf_token,body:{} }); setNotice(result.verification_sent ? 'Verification email sent. Open the link in your inbox to confirm your address.' : 'A verification email could not be sent. Please try again later.'); }
    catch(e) { setActionError(e instanceof Error?e.message:'Verification request failed.'); } finally { setBusy(false); }
  }
  async function change(event:React.FormEvent) {
    event.preventDefault(); setActionError(''); setNotice('');
    if(password!==confirmation){setActionError('The new passwords do not match.');return;}
    setBusy(true);
    try { await teamApi('auth/change-password',{method:'POST',csrf:session?.csrf_token,body:{current_password:current,password}});setCurrent('');setPassword('');setConfirmation('');await refresh();setNotice('Password updated. Other sessions have been signed out.'); }
    catch(e){setActionError(e instanceof Error?e.message:'Password change failed.');}finally{setBusy(false);}
  }
  if(error)return <div className="wrap inner-page"><TeamUnavailable error={error}/></div>;
  if(loading&&!session)return <div className="wrap inner-page" role="status">Loading account…</div>;
  if(session&&!session.user)return <div className="wrap account-token-page"><TeamLogin session={session} onLogin={refresh}/></div>;
  return <div className="wrap inner-page account-settings"><Link className="back-link" href="/projects">← Your workspace</Link><span className="eyebrow">ACCOUNT SETTINGS</span><h1>Your account<span>.</span></h1><p className="lead">Manage sign-in and keep access to your workspace secure.</p>{actionError&&<p className="account-error" role="alert">{actionError}</p>}{notice&&<p className="account-notice" role="status">{notice}</p>}
    <section><h2>Profile</h2><p><strong>{session?.user?.display_name}</strong><br/>{session?.user?.email}</p><span className="status">{session?.user?.email_verified?'Email verified':'Email verification pending'}</span>{session?.user?.email_verified===false&&<><p style={{marginTop:20}}>Verify ownership of your email before accepting an invitation to another team. Production workspaces require verification.</p>{config?.mail_available?<button className="button outline" disabled={busy} onClick={verify}>Send verification email</button>:<p className="account-notice">This installation has no email service connected. Your password account works locally; verification and password recovery need the operator’s email configuration.</p>}</>}</section>
    {session?.user?.auth_method === 'password' ? <section><h2>Change password</h2><p>Choose a new password to replace your current one and sign out other sessions.</p><form className="team-form" onSubmit={change}><label>Current password<input type="password" required minLength={12} maxLength={128} autoComplete="current-password" value={current} onChange={e=>setCurrent(e.target.value)}/></label><label>New password<input type="password" required minLength={12} maxLength={128} autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)}/></label><label>Confirm new password<input type="password" required minLength={12} maxLength={128} autoComplete="new-password" value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label><p>12–128 characters. Spaces and passphrases are supported.</p><button className="button accent" disabled={busy}>{busy?'Updating…':'Update password'}</button></form></section> : <section><h2>Sign-in method</h2><p>{session?.user?.auth_method === 'oidc' ? 'This account signs in through your identity provider. Manage its password in that provider’s account settings.' : 'This is an explicitly enabled development identity. Sign out and create a password account to use real account authentication.'}</p></section>}
  </div>;
}
