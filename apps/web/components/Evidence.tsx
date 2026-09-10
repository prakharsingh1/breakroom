import { Check, Report, money } from '@/lib/contracts';

export function Status({ value }: { value: string }) {
  return <span className={`status status-${value.toLowerCase()}`}>{value === 'PASS' || value === 'pass' ? '✓ ' : value === 'FAIL' || value === 'fail' ? '× ' : '· '}{value.replaceAll('_', ' ')}</span>;
}

export function Checks({ checks }: { checks: Check[] }) {
  return <div className="checks">{checks.map(check => <div className="check" key={check.id}>
    <div><span className="micro">{check.category}</span><strong>{check.id.replaceAll('_', ' ')}</strong><p>{check.message}</p></div><Status value={check.status} />
  </div>)}</div>;
}

export function Effects({ report }: { report: Report }) {
  if (report.final_state.available === false || !Array.isArray(report.final_state.refunds)) {
    return <div className="effects"><div className="effect-total"><span>Successful refunds</span><strong data-testid="refund-total">Unknown</strong><small>Authoritative worker state is unavailable.</small></div><p className="source-note">No amount or effect count can be established. Unknown evidence does not pass a Release Check.</p></div>;
  }
  const refunds = report.final_state.refunds || [];
  const successful = refunds.filter(r => r.status === 'succeeded');
  const task = report.case.manifest.task;
  return <div className="effects">
    <div className="effect-total"><span>Successful refunds</span><strong data-testid="refund-total">{money(successful.reduce((sum, r) => sum + r.amount_minor, 0), task.currency)}</strong><small>Requested: {money(task.amount_minor, task.currency)} · {successful.length} committed {successful.length === 1 ? 'refund' : 'refunds'}</small></div>
    {refunds.length === 0 ? <p className="muted">No refund records were created.</p> : refunds.map((refund, index) => <div className="refund-record" key={refund.id}>
      <span className="record-icon">↗</span><div><strong>Refund {String(index + 1).padStart(2, '0')}</strong><code>{refund.id}</code><code>key: {refund.operation_key}</code></div><div className="record-amount"><strong>{money(refund.amount_minor, refund.currency)}</strong><span>{refund.status}</span></div>
    </div>)}
    <p className="source-note">Source: authoritative TestPay records after execution.</p>
  </div>;
}

export function AgentView({ report }: { report: Report }) {
  const events = report.events.filter(e => ['tool_call', 'tool_response', 'tool_error'].includes(String(e.kind)));
  const result = report.agent_result || report.result;
  return <><div className="observations">{events.map((event, index) => {
    const data = event.data || {};
    const error = event.kind === 'tool_error';
    const heading = event.kind === 'tool_call' ? 'Called' : error ? 'Received error' : 'Received response';
    return <details className={`observation ${error ? 'observation-error' : ''}`} key={String(event.id || index)}>
      <summary><span className="event-order">{String(index + 1).padStart(2, '0')}</span><span>{heading}<strong>{String(event.tool || 'Tool')}</strong></span><span className="event-mark">{error ? '!' : '↗'}</span></summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>;
  })}</div><div className="agent-claim"><span className="micro">Agent’s final response</span><p>{result?.customer_text || 'Unknown: no parseable customer-facing response was recorded.'}</p></div></>;
}

export function EvidencePair({ report, headingLevel = 2 }: { report: Report; headingLevel?: 2 | 3 }) {
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return <div className="evidence-pair"><section className="agent-pane"><header><span className="pane-number">01</span><Heading>What the agent saw</Heading><span className="micro">Observed</span></header><AgentView report={report} /></section><section className="effects-pane"><header><span className="pane-number">02</span><Heading>What actually happened</Heading><span className="micro">Verified state</span></header><Effects report={report} /></section></div>;
}
