import { Check } from './contracts';
import { Role } from './team';

export type CatalogCase = { case_id: string; case_version: string; manifest_hash: string; name: string; summary: string; severity: string; tags: string[] };
export type ReadinessCase = { case_id: string; case_version: string; manifest_hash: string; status: string; report_id: string | null };
export type SuiteReadiness = { id: string; name: string; status: 'PASS' | 'FAIL' | 'INCONCLUSIVE'; counts: Record<'PASS' | 'FAIL' | 'INCONCLUSIVE' | 'UNSUPPORTED' | 'MISSING' | 'INCOMPATIBLE', number>; cases: ReadinessCase[] };
export type ProjectInsights = {
  project_id: string; provenance: 'customer_generated'; notice: string;
  report_counts: { total: number; PASS: number; FAIL: number; INCONCLUSIVE: number; UNSUPPORTED: number };
  latest_cases: { case_id: string; report_id: string; verdict: string; created_at: string; case_version: string; manifest_hash: string; checks: Check[]; recommendations: string[] }[];
  coverage: { catalog_cases: number; reported_cases: number; missing_case_ids: string[]; incompatible_case_ids: string[] };
  suites: SuiteReadiness[];
};
export type Invitation = { id: string; email: string; role: Exclude<Role, 'owner'>; created_at: string; expires_at: string; revoked_at: string | null; accepted_at: string | null; invite_url?: string };

export function shellQuote(value: string) { return "'" + value.replaceAll("'", "'\\''") + "'"; }
export function reportHref(projectId: string, reportId: string) { return '/projects/' + encodeURIComponent(projectId) + '/reports/' + encodeURIComponent(reportId); }
