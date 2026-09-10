export type Check = { id: string; category: string; status: 'pass' | 'fail' | 'unknown' | 'not_applicable'; message: string; evidence_refs: string[] };
export type Refund = { id: string; order_id: string; amount_minor: number; currency: string; operation_key: string; logical_request_id: string; status: string };
export type Drill = {
  case_id: string; number: number; name: string; summary: string; severity: string;
  provenance: string; case_version: string; pack_version: string; status: string;
  compatibility: string; limitations: string[]; unsupported_behavior: string[];
  assertions: string[]; required_capabilities: string[]; allowed_outcomes: string[];
  faults: { id: string; tool: string; phase: string; action: string; invocation: number }[];
  task: { amount_minor: number; currency: string; order_id: string; ticket_id: string };
  demo_available?: boolean; negative_controls: string[];
  execution_plan?: { mode: string; tasks: { request_id: string; amount_minor: number; currency: string }[] };
  expectations?: { observations: string[]; forbid_new_effects: boolean };
  variation_constraints: { description: string; supported_seeds: number[] };
};
export type AuditEvent = Record<string, unknown> & { id?: string; sequence?: number; type?: string; kind?: string; tool?: string; phase?: string; timestamp?: number; data?: Record<string, unknown> };
export type Report = {
  run_id: string; schema_version: string; engine_version: string; oracle_version: string;
  case: { case_id: string; case_version: string; manifest_hash: string; fixture_hash: string; manifest: Drill };
  seed: number; verdict: 'PASS' | 'FAIL' | 'INCONCLUSIVE' | 'UNSUPPORTED';
  execution: { status: string; error?: string; duration_ms: number };
  final_state: { refunds: Refund[]; tickets: Record<string, unknown>[]; [key: string]: unknown };
  initial_state: Record<string, unknown>; events: AuditEvent[]; checks: Check[];
  agent_result?: { customer_text: string; claims: Record<string, unknown>[]; escalated: boolean } | null;
  result?: { customer_text: string; claims: Record<string, unknown>[]; escalated: boolean } | null;
  limitations: string[]; metrics: Record<string, unknown>;
};
export type Run = { id: string; created_at: string; expires_at: string; source: 'live_builtin'; agent: 'faulty' | 'corrected'; report: Report };

export function money(amount: number, currency = 'INR') {
  // Current simulator currencies use two minor-unit decimal places.
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency, minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(amount / 100);
}

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/${path}`, { method: body ? 'POST' : 'GET', headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined, cache: 'no-store', signal });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new Error('The demo service is offline. Start the local API, then try again. You can also use breakroom demo from your terminal.');
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const explanation = typeof data.detail === 'string' ? data.detail : response.status === 409 && data.detail?.pairs ? 'Incompatible reports: ' + data.detail.pairs.flatMap((pair: { incompatibilities?: string[] }) => pair.incompatibilities || []).join(', ') : typeof data.error === 'string' ? data.error : undefined;
    throw new Error(explanation || (response.status >= 500 ? 'The demo service is unavailable. Start the API and try again, or run breakroom demo locally.' : `Request could not be completed (${response.status}).`));
  }
  return response.json();
}

const STORAGE_KEY = 'BREAKROOM_demo_recordings_v1';
export function readRecordings(): Run[] {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(saved) ? saved.filter(r => r?.source === 'live_builtin' && r?.report?.schema_version === '1.0' && Array.isArray(r?.report?.checks)).slice(0, 20) : [];
  } catch { return []; }
}
export function saveRecording(run: Run): boolean {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify([run, ...readRecordings().filter(r => r.id !== run.id)].slice(0, 20))); return true; }
  catch { return false; }
}
export function clearRecordings() { localStorage.removeItem(STORAGE_KEY); }
