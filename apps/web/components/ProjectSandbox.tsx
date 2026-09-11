'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Role, teamApi } from '@/lib/team';
import { CatalogCase } from '@/lib/workspace';
import { Deployment, Provider, SandboxOverview, SandboxRun } from '@/lib/sandbox';
import { Checks, EvidencePair, Status } from './Evidence';

const active = (run: SandboxRun) => ['queued', 'running'].includes(run.status);
function download(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export default function ProjectSandbox({ projectId, csrf, role, catalog }: { projectId: string; csrf: string | null; role: Role; catalog: CatalogCase[] }) {
  const base = 'projects/' + encodeURIComponent(projectId) + '/sandbox';
  const [data, setData] = useState<SandboxOverview | null>(null);
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false);
  const [sourceKind, setSourceKind] = useState<'zip' | 'github'>('zip'); const [name, setName] = useState('');
  const [file, setFile] = useState<File | null>(null); const [repository, setRepository] = useState(''); const [commit, setCommit] = useState(''); const [githubToken, setGithubToken] = useState('');
  const [deployment, setDeployment] = useState(''); const [cases, setCases] = useState(['refund-response-lost']);
  const [seeds, setSeeds] = useState([0]); const [repetitions, setRepetitions] = useState(1);
  const [provider, setProvider] = useState<Provider | ''>(''); const [model, setModel] = useState('');
  const [maxCalls, setMaxCalls] = useState(20); const [maxOutput, setMaxOutput] = useState(512); const [consent, setConsent] = useState(false);
  const [keyProvider, setKeyProvider] = useState<Provider>('openai'); const [secret, setSecret] = useState('');
  const [selected, setSelected] = useState<SandboxRun | null>(null); const [position, setPosition] = useState(0);
  const attempt = useRef<{ body: string; key: string } | null>(null);
  const writer = role !== 'viewer'; const count = cases.length * seeds.length * repetitions;
  const reload = useCallback(async () => {
    const next = await teamApi<SandboxOverview>(base); setData(next);
    setDeployment(current => next.deployments.some(d => d.id === current) ? current : next.deployments[0]?.id || '');
  }, [base]);
  useEffect(() => { void reload().catch(e => setError(e.message)); }, [reload]);
  useEffect(() => {
    if (!data?.runs.some(active)) return;
    const timer = setTimeout(async () => {
      try { await reload(); if (selected && active(selected)) setSelected(await teamApi<SandboxRun>(base + '/runs/' + selected.id)); }
      catch (e) { setError(e instanceof Error ? e.message : 'Run updates unavailable.'); }
    }, 2000);
    return () => clearTimeout(timer);
  }, [base, data, reload, selected]);
  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await work(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Sandbox request failed.'); }
    finally { setBusy(false); }
  }
  async function deploy(event: React.FormEvent) {
    event.preventDefault();
    await action(async () => {
      let archive: string | undefined;
      if (sourceKind === 'zip') {
        if (!file || file.size > 1048576) throw new Error('Choose an agent ZIP of at most 1 MiB.');
        archive = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('Could not read this ZIP.')); reader.readAsDataURL(file);
        });
      }
      let result: Deployment;
      try { result = await teamApi<Deployment>(base + '/deployments', { method: 'POST', csrf, body: {
        name, source_kind: sourceKind, ...(sourceKind === 'zip' ? { archive_base64: archive } : { repository, commit_sha: commit, ...(githubToken ? { github_token: githubToken } : {}) })
      } }); } finally { setGithubToken(''); }
      await reload(); setDeployment(result.id); setNotice('Agent version saved. Choose its Fire Drills and start a sandbox run.');
    });
  }
  async function start(event: React.FormEvent) {
    event.preventDefault();
    await action(async () => {
      const body = { deployment_id: deployment, cases, seeds, repetitions, provider: provider || null, model: provider ? model : null, max_calls: maxCalls, max_output_tokens: maxOutput, accept_model_cost: !!provider && consent };
      const encoded = JSON.stringify(body);
      if (attempt.current?.body !== encoded) attempt.current = { body: encoded, key: crypto.randomUUID().replaceAll('-', '') };
      const run = await teamApi<SandboxRun>(base + '/runs', { method: 'POST', csrf, body, headers: { 'Idempotency-Key': attempt.current.key } });
      attempt.current = null; setSelected({ ...run, trials: [] }); setPosition(0); await reload(); setNotice('Run queued. Results will appear as each trial finishes.');
    });
  }
  async function inspect(run: SandboxRun) { await action(async () => { setSelected(await teamApi<SandboxRun>(base + '/runs/' + run.id)); setPosition(0); }); }
  async function remove(path: string, message: string) { await action(async () => { await teamApi(base + path, { method: 'DELETE', csrf }); await reload(); if (selected && path === '/deployments/' + selected.deployment_id) setSelected(null); setNotice(message); }); }
  if (!data) return <section className="team-section"><h2>Agent sandbox</h2>{error ? <><p role="alert" className="light-error">{error}</p><button className="button outline" onClick={() => void action(reload)}>Retry sandbox</button></> : <p role="status">Loading sandbox…</p>}</section>;
  const currentReport = selected?.trials?.find(t => t.position === position)?.report;
  const deploymentName = (id: string) => data.deployments.find(d => d.id === id)?.name || 'Deleted agent';
  return <div className="sandbox-workspace">
    <section className="sandbox-intro"><div><span className="eyebrow">YOUR CODE / CONTROLLED CONSEQUENCES</span><h2>Find the failure<br />before your customers do.</h2><p>Deploy your Python adapter. Push it through failed APIs, duplicate requests and interrupted workflows. Inspect what it actually changed.</p></div><div className="sandbox-runtime"><span className={'sandbox-dot ' + (data.available ? 'ready' : '')} /><strong>{data.available ? 'Worker available' : data.enabled ? 'Worker offline' : 'Execution not configured'}</strong><p>{data.notice}</p><a href="/docs#sandbox">Runtime and adapter guide ↗</a></div></section>
    {!data.available && <p className="team-notice" role="status">{data.enabled ? 'Saved agents and evidence remain accessible. An operator must start the configured worker before new runs can begin.' : 'This installation needs an isolated worker and encrypted source storage. Download the starter now; an operator can enable execution using the setup guide.'}</p>}
    {error && <p role="alert" className="light-error">{error}</p>}{notice && <p role="status" className="team-notice">{notice}</p>}
    <div className="sandbox-columns"><section className="team-section"><div className="section-heading"><div><span className="micro">01 / DEPLOY</span><h3>Bring your agent</h3></div><a className="button outline small" href={'/api/team/' + base + '/starter.zip'}>Download starter ZIP</a></div>
      <p className="scope-note">Python adapter with <code>breakroom-agent.json</code>. Up to 1 MiB compressed, 4 MiB expanded. Bundle pure Python dependencies; this runtime does not install packages. Remove embedded credentials before uploading.</p>
      {writer ? <form className="team-form" onSubmit={deploy}><fieldset disabled={busy || !data.enabled}>
        <label>Agent name<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="Refund assistant v2" /></label>
        <label>Agent source<select value={sourceKind} onChange={e => setSourceKind(e.target.value as 'zip' | 'github')}><option value="zip">ZIP upload</option><option value="github">GitHub repository</option></select></label>
        {sourceKind === 'zip' ? <label>Agent ZIP<input type="file" required accept=".zip,application/zip" onChange={e => setFile(e.target.files?.[0] || null)} /></label> : <><label>GitHub repository<input required placeholder="owner/repository" maxLength={160} value={repository} onChange={e => setRepository(e.target.value)} /></label><label>Commit SHA<input required pattern="[a-fA-F0-9]{40}" minLength={40} maxLength={40} placeholder="Full 40-character commit SHA" value={commit} onChange={e => setCommit(e.target.value)} /></label><label>Private repository read token (optional)<input type="password" autoComplete="off" maxLength={255} value={githubToken} onChange={e => setGithubToken(e.target.value)} /></label><p className="scope-note">Read token is used only for this import and is not saved. Use a small dedicated agent repository.</p></>}
        <button className="button accent">Save agent version</button>
      </fieldset></form> : <p className="scope-note">A developer or owner can add agent versions.</p>}
      {data.deployments.length > 0 && <details className="sandbox-versions"><summary>{data.deployments.length} saved agent {data.deployments.length === 1 ? 'version' : 'versions'}</summary>{data.deployments.map(agent => <article key={agent.id}><strong>{agent.name}</strong><small>{agent.source_kind} · {agent.manifest.entrypoint}</small><code title="Archive SHA-256">{agent.sha256}</code>{agent.repository && <small>{agent.repository} @ {agent.commit_sha?.slice(0, 12)}</small>}{writer && <button className="text-button" disabled={busy || data.runs.some(r => r.deployment_id === agent.id && active(r))} onClick={() => { if (confirm('Delete this agent source and all of its saved sandbox runs?')) void remove('/deployments/' + agent.id, 'Agent source and associated runs deleted.'); }}>Delete {agent.name}</button>}</article>)}</details>}
    </section>
    <section className="team-section"><span className="micro">02 / CHALLENGE</span><h3>Set the test limits</h3><form className="team-form" onSubmit={start}><fieldset disabled={busy || !writer || !data.enabled}>
      <label>Agent version<select required value={deployment} onChange={e => setDeployment(e.target.value)}><option value="">Choose a saved agent</option>{data.deployments.map(agent => <option value={agent.id} key={agent.id}>{agent.name}</option>)}</select></label>
      <div className="sandbox-presets" aria-label="Test presets"><button type="button" className="button outline small" onClick={() => {setCases(['refund-response-lost']);setSeeds([0]);setRepetitions(1);}}>Quick check</button><button type="button" className="button outline small" disabled={!catalog.length} onClick={() => {setCases(catalog.map(c => c.case_id));setSeeds([0]);setRepetitions(1);}}>All 24 drills</button><button type="button" className="button outline small" disabled={!catalog.length} onClick={() => {setCases(catalog.map(c => c.case_id));setSeeds([0,1,2]);setRepetitions(1);}}>Stress: 72 trials</button></div>
      <details className="sandbox-case-picker"><summary>{cases.length} selected Fire {cases.length === 1 ? 'Drill' : 'Drills'} · customize</summary>{catalog.map(item => <label key={item.case_id} className="sandbox-check"><input type="checkbox" checked={cases.includes(item.case_id)} onChange={e => setCases(current => e.target.checked ? [...current, item.case_id] : current.filter(c => c !== item.case_id))} />{item.name}</label>)}</details>
      <div className="sandbox-numbers"><label>Reviewed variations<select value={seeds.join(',')} onChange={e => setSeeds(e.target.value.split(',').map(Number))}><option value="0">Seed 0</option><option value="1">Seed 1</option><option value="2">Seed 2</option><option value="0,1,2">All three seeds</option></select></label><label>Repetitions<input type="number" min={1} max={3} required value={repetitions} onChange={e => setRepetitions(Number(e.target.value))} /></label></div>
      <label>Model access<select value={provider} onChange={e => {const next = e.target.value as Provider | '';setProvider(next);setModel(next ? data.models[next][0] || '' : '');setConsent(false);}}><option value="">Disabled — no model charges</option>{(['openai','anthropic'] as Provider[]).map(p => <option key={p} value={p} disabled={!data.credentials.some(k => k.provider === p)}>{p === 'openai' ? 'OpenAI' : 'Anthropic'}{!data.credentials.some(k => k.provider === p) ? ' — owner key required' : ''}</option>)}</select></label>
      {provider && <><label>Approved model<select required value={model} onChange={e => setModel(e.target.value)}>{data.models[provider].map(m => <option key={m}>{m}</option>)}</select></label><div className="sandbox-numbers"><label>Model calls per run<input type="number" min={1} max={100} required value={maxCalls} onChange={e => setMaxCalls(Number(e.target.value))} /></label><label>Output tokens per call<input type="number" min={1} max={2048} required value={maxOutput} onChange={e => setMaxOutput(Number(e.target.value))} /></label></div><label className="sandbox-check"><input type="checkbox" required checked={consent} onChange={e => setConsent(e.target.checked)} />I authorize usage billed by my model provider. Prompts leave the sandbox through the broker; call and token limits are not a fixed price.</label></>}
      <div className="sandbox-plan"><strong>{count} {count === 1 ? 'trial' : 'trials'}</strong><span>60 seconds per trial · 15 minutes per run · 512 MiB per container</span><small>Incomplete, unsupported and untriggered checks cannot pass. A pass covers these simulated cases only.</small></div>
      <button className="button accent" disabled={!data.available || !deployment || count < 1 || count > 72 || (!!provider && (!consent || !model))}>Start sandbox run →</button>{count > 72 && <p role="alert">Reduce cases, variations or repetitions to at most 72 trials.</p>}
    </fieldset></form></section></div>
    <section className="team-section"><div className="section-heading"><div><span className="micro">03 / INSPECT</span><h3>Sandbox runs</h3><p className="scope-note">Evaluated from simulated records outside the agent container. Model output and agent claims may appear in private evidence.</p></div><button className="button outline small" disabled={busy} onClick={() => void action(async () => {await reload();if(selected)setSelected(await teamApi<SandboxRun>(base+'/runs/'+selected.id));})}>Refresh runs</button></div>
      {data.runs.length ? <div className="sandbox-run-list">{data.runs.map(run => <button className={'sandbox-run-row ' + (selected?.id === run.id ? 'selected' : '')} key={run.id} onClick={() => void inspect(run)}><Status value={run.verdict} /><span><strong>{deploymentName(run.deployment_id)}</strong><small>{run.plan.length} trials · {new Date(run.created_at).toLocaleString()}</small></span><span className="sandbox-run-state">{run.cancel_requested && active(run) ? 'Stopping…' : run.status}</span><span aria-hidden="true">↗</span></button>)}</div> : <div className="workspace-soft"><h4>No sandbox runs yet.</h4><p>Save an agent version and start a quick check. Each result will include its checks, observed tool calls and committed effects.</p></div>}
      {selected && <div className="sandbox-investigator"><div className="section-heading"><div><h4>{deploymentName(selected.deployment_id)} <Status value={selected.verdict} /></h4><p role="status">{selected.trials?.length || 0} / {selected.plan.length} trials recorded · {selected.status}{selected.cancel_requested && active(selected) ? ' · cancellation requested' : ''}</p></div><div className="team-actions"><button className="button outline small" onClick={() => download(selected, 'breakroom-sandbox-' + selected.id + '.json')}>Download run evidence</button>{writer && active(selected) && <button className="button outline small" disabled={busy || selected.cancel_requested} onClick={() => void action(async () => {const result = await teamApi<SandboxRun>(base+'/runs/'+selected.id+'/cancel',{method:'POST',csrf});setSelected({...result,trials:selected.trials});await reload();})}>Cancel run</button>}{writer && !active(selected) && <button className="text-button" disabled={busy} onClick={() => {if(confirm('Delete this run and its trial evidence?')) void action(async () => {await teamApi(base+'/runs/'+selected.id,{method:'DELETE',csrf});setSelected(null);await reload();});}}>Delete run</button>}</div></div>
        <div className="sandbox-run-meta"><span>{selected.runtime} · {selected.image_id ? 'Pinned image ' + selected.image_id.slice(7,19) : 'Waiting for worker'}</span><span>{selected.provider ? selected.calls_used + ' / ' + selected.max_calls + ' model calls · ' + selected.model : 'Model access disabled'}</span><span>Evidence expires {new Date(selected.expires_at).toLocaleDateString()}</span>{selected.provider && <span>{selected.usage.input_tokens} known input tokens · {selected.usage.output_tokens} known output tokens · {selected.usage.unknown_calls} calls with unknown usage</span>}</div>
        {selected.error && <p className="light-error" role="alert">{selected.error}</p>}
        {!!selected.trials?.length && <label className="sandbox-trial-select">Inspect trial<select value={position} onChange={e => setPosition(Number(e.target.value))}>{selected.trials.map(trial => <option key={trial.position} value={trial.position}>{trial.position+1}. {catalog.find(c => c.case_id === trial.report.case.case_id)?.name || trial.report.case.case_id} · seed {trial.report.seed} · {trial.report.verdict}</option>)}</select></label>}
        {currentReport && <><div className="sandbox-trial-heading"><Status value={currentReport.verdict} /><span>Execution: {currentReport.execution.status}</span><button className="text-button" onClick={() => download(currentReport, 'breakroom-trial-' + currentReport.run_id + '.json')}>Download trial JSON</button></div><EvidencePair report={currentReport} headingLevel={3} /><Checks checks={currentReport.checks} /></>}
      </div>}
    </section>
    <section className="team-section"><details><summary className="sandbox-key-summary">Model provider keys · {data.credentials.length} configured</summary><p>Keys are encrypted in project storage and stay outside agent containers. Developers can use configured keys only through capped runs. Revocation stops subsequent requests; a request already sent may still be billed.</p>{data.credentials.map(key => <div className="member-row" key={key.id}><div><strong>{key.provider}</strong><small>Saved {new Date(key.created_at).toLocaleDateString()} · secret is not retrievable</small></div>{role === 'owner' && <button className="button outline small" disabled={busy} onClick={() => void remove('/credentials/'+key.id, 'Provider key revoked. Further model calls using it will be denied.')}>Revoke {key.provider} key</button>}</div>)}{role === 'owner' ? <form className="team-form" onSubmit={e => {e.preventDefault();void action(async () => {try {await teamApi(base+'/credentials',{method:'POST',csrf,body:{provider:keyProvider,secret}});} finally {setSecret('');}await reload();setNotice('Provider key encrypted and saved. It has not been tested or charged.');});}}><fieldset disabled={busy || !data.enabled}><label>Key provider<select value={keyProvider} onChange={e => setKeyProvider(e.target.value as Provider)}><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select></label><label>Provider API key<input type="password" autoComplete="off" required minLength={20} maxLength={255} value={secret} onChange={e => setSecret(e.target.value)} /></label><p className="scope-note">Saving a replacement revokes the old key for existing runs. Use a dedicated provider key with your own account spending limits.</p><button className="button outline">Save provider key</button></fieldset></form> : <p className="scope-note">Only project owners can add or revoke provider keys.</p>}</details></section>
  </div>;
}
