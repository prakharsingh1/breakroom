'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import AccountEntry from './AccountEntry';
import { useTeamSession } from '@/lib/team';

export default function AccountPage({ mode }: { mode: 'signin' | 'signup' }) {
  const { session, loading, error, refresh } = useTeamSession();
  const router = useRouter();
  useEffect(() => { if (session?.user) router.replace('/projects'); }, [session, router]);
  return <div className="wrap account-page"><aside className="account-story"><span className="eyebrow">BUILT FOR THE WORK BEFORE RELEASE</span><h1>Give your agent<br />a proving ground<span>.</span></h1><p>Find the failures that happy-path tests miss. Keep the evidence. Ship the correction.</p><ol><li><strong>Connect your agent</strong><span>Use the Python adapter in your own environment.</span></li><li><strong>Choose the hard cases</strong><span>Build a suite from 24 versioned Fire Drills.</span></li><li><strong>Review with your team</strong><span>Turn failed checks into your next regression test.</span></li></ol><Link className="text-link" href="/demo">Explore the interactive example →</Link></aside><div>{loading && !session && <p role="status">Opening account access…</p>}{error && <div className="light-error"><p role="alert">{error}</p><button className="button outline" onClick={() => void refresh()}>Try again</button></div>}{session && !session.user && <AccountEntry session={session} onLogin={refresh} initialMode={mode} />}</div></div>;
}
