'use client';
import { useCallback, useEffect, useState } from 'react';
import { Report } from './contracts';

export type Role = 'owner' | 'developer' | 'viewer';
export type TeamUser = { id: string; email: string; display_name: string; email_verified?: boolean; auth_method?: 'password' | 'oidc' | 'development' };
export type Project = { id: string; name: string; role: Role; retention_days: number; created_at: string };
export type TeamSession = { user: TeamUser | null; csrf_token: string | null; projects: Project[]; auth: { oidc_available: boolean; dev_login_available: boolean } };
export type Upload = { schema_version: string; kind: 'customer_generated_report'; report: Report; privacy: { format: string; [key: string]: unknown } };
export type TeamReport = { id: string; project_id: string; created_at: string; expires_at: string; case_id: string; verdict: string; provenance: 'customer_generated'; privacy: Record<string, unknown>; upload?: Upload };
export type Member = { user_id: string; email: string; display_name: string; role: Role };
export type TeamKey = { id: string; name: string; prefix: string; scopes: string[]; created_at: string; expires_at: string; revoked_at: string | null; secret?: string };
export type Suite = { id: string; name: string; cases: { case_id: string; case_version: string; manifest_hash: string }[]; created_at: string };
export type TeamComparison = { compatible: boolean; baseline_trial_count: number; candidate_trial_count: number; pairs: { case_id: string; seed: number; baseline_verdict: string; candidate_verdict: string; changed_checks?: {id: string; baseline: string; candidate: string}[] }[] };

export class TeamApiError extends Error {
  constructor(message:string,readonly status:number){super(message);this.name='TeamApiError';}
}

export async function teamApi<T>(path: string, options: { method?: string; body?: unknown; csrf?: string | null; headers?: Record<string, string> } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch('/api/team/' + path, {
      method: options.method || 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: { ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(options.csrf ? { 'X-CSRF-Token': options.csrf } : {}), ...options.headers },
      body: options.body === undefined ? undefined : JSON.stringify(options.body)
    });
  } catch { throw new Error('Your workspace is temporarily unavailable. Please try again.'); }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const explanation = typeof data?.detail === 'string' ? data.detail : Array.isArray(data?.detail?.pairs) ? 'Incompatible reports: ' + data.detail.pairs.flatMap((pair: {incompatibilities?: string[]}) => pair.incompatibilities || []).join(', ') : `Team request failed (${response.status}).`;
    throw new TeamApiError(explanation,response.status);
  }
  if (!data) throw new Error('The team service returned an invalid response.');
  return data as T;
}

export function useTeamSession() {
  const [session, setSession] = useState<TeamSession | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    setLoading(true); setError('');
    try { const next = await teamApi<TeamSession>('me'); setSession(next); return next; }
    catch (e) { setError(e instanceof Error ? e.message : 'Session unavailable.'); return null; }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return { session, error, loading, refresh };
}

export async function digestJson(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, '0')).join('');
}
