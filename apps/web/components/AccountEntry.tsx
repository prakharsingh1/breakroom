'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { teamApi, TeamSession } from '@/lib/team';

export type AuthConfig = { password_available: boolean; mail_available: boolean; verification_required: boolean; oidc_available: boolean };

export default function AccountEntry({ session, onLogin, initialMode = 'signin' }: { session: TeamSession; onLogin: () => Promise<unknown>; initialMode?: 'signin' | 'signup' }) {
  const [mode, setMode] = useState<'signin' | 'signup' | 'reset'>(initialMode);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { void teamApi<AuthConfig>('auth/config').then(setConfig).catch(e => setError(e.message)); }, []);
  function switchMode(next: typeof mode) { setMode(next); setError(''); setNotice(''); setPassword(''); }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      if (mode === 'reset') {
        await teamApi('auth/request-password-reset', { method: 'POST', body: { email } });
        setNotice('If an account can receive email at that address, a reset link is on its way.');
      } else {
        await teamApi(mode === 'signup' ? 'auth/register' : 'auth/password-login', { method: 'POST', body: mode === 'signup' ? { email, display_name: name, password } : { email, password } });
        setPassword(''); await onLogin();
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to sign in. Please try again.'); }
    finally { setBusy(false); }
  }
  async function localLogin(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await teamApi('auth/dev-login', { method: 'POST', body: { email, display_name: 'Local team member' } }); await onLogin(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Local sign-in failed.'); }
    finally { setBusy(false); }
  }
  return <section className="account-entry" aria-label="Account access">
    <span className="eyebrow">YOUR AGENT. YOUR WORKSPACE.</span>
    <h2>{mode === 'signup' ? 'Create your account' : mode === 'reset' ? 'Reset your password' : 'Welcome back.'}</h2>
    <p>{mode === 'signup' ? 'Bring your agent, build a test suite, and review release evidence with your team.' : mode === 'reset' ? 'We’ll email you a link to choose a new password.' : 'Sign in to your projects, test suites, and release history.'}</p>
    {!config && !error && <p role="status">Checking sign-in options…</p>}
    {config?.password_available && <form className="team-form account-form" onSubmit={submit}>
      {mode === 'signup' && <label>Your name<input name="name" required autoComplete="name" maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="Alex Morgan" /></label>}
      <label>Email address<input name="email" type="email" required autoComplete="email" maxLength={254} value={email} onChange={e => setEmail(e.target.value)} placeholder="you@company.com" /></label>
      {mode !== 'reset' && <><label htmlFor="account-password">Password</label><div className="password-field"><input id="account-password" name="password" type={showPassword ? 'text' : 'password'} required minLength={mode === 'signup' ? 12 : undefined} maxLength={128} autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} value={password} onChange={e => setPassword(e.target.value)} aria-describedby={mode === 'signup' ? 'password-help' : undefined} /><button type="button" aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword} onClick={() => setShowPassword(!showPassword)}>{showPassword ? 'Hide' : 'Show'}</button></div>{mode === 'signup' && <p id="password-help">Use at least 12 characters. A memorable passphrase works well.</p>}</>}
      {mode === 'reset' && config && !config.mail_available && <p className="account-notice">Email delivery has not been configured on this installation. Password recovery is unavailable until the operator connects an email service.</p>}
      <button className="button accent" disabled={busy || (mode === 'reset' && !config.mail_available)}>{busy ? 'Please wait…' : mode === 'signup' ? 'Create account' : mode === 'reset' ? 'Send reset link' : 'Sign in'}</button>
    </form>}
    {error && <p className="account-error" role="alert">{error}</p>}{notice && <p className="account-notice" role="status">{notice}</p>}
    {config?.password_available && <div className="account-switch">{mode === 'signin' ? <><span>New to Breakroom? <button onClick={() => switchMode('signup')}>Create an account</button></span><button onClick={() => switchMode('reset')}>Forgot password?</button></> : <button onClick={() => switchMode('signin')}>Back to sign in</button>}</div>}
    {(config?.oidc_available || session.auth.oidc_available) && <a href="/api/team/auth/login" className="button outline">Continue with your identity provider →</a>}
    {config && !config.password_available && !config.oidc_available && <p className="account-notice">Account sign-in is currently unavailable on this installation.</p>}
    {session.auth.dev_login_available && <details className="developer-login"><summary>Developer test sign-in</summary><form className="team-form" onSubmit={localLogin}><div className="development-note"><strong>Local development identity</strong><p>For testing on this machine only. This does not verify an identity and is disabled in production.</p></div><label>Email for this local identity<input type="email" required value={email} onChange={e => setEmail(e.target.value)} autoComplete="off" /></label><button className="button outline" disabled={busy}>Use local development identity</button></form></details>}
    <p className="account-footnote">Your projects are private. <Link href="/docs#privacy">How your data is handled →</Link></p>
  </section>;
}
