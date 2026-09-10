'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { shellQuote } from '@/lib/workspace';
import CopyCommand from './CopyCommand';

export default function ProjectConnect({ projectId, canManageKeys, onKeys, onUpload }: { projectId: string; canManageKeys: boolean; onKeys: () => void; onUpload: () => void }) {
  const [origin, setOrigin] = useState('');
  useEffect(() => { setOrigin(window.location.origin); }, []);
  const local = /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
  const uploadCommand = origin ? `breakroom upload-report ./artifacts/prepared-report.json \\\n  --server ${shellQuote(origin)} \\\n  --project ${shellQuote(projectId)} \\\n  --token-env BREAKROOM_API_TOKEN${local ? ' \\\n  --allow-localhost-http' : ''}` : '';
  return <section className="team-section connect-section"><span className="eyebrow">FROM YOUR RUNNER TO YOUR TEAM</span><h2>Connect your agent</h2><p className="section-intro">Run tests on your machine or CI. Review the results here with your team.</p>
    <ol className="connect-steps">
      <li><div className="step-number" aria-hidden="true">01</div><div><h3>Install the local runner</h3><p>Clone the repository and create a Python 3.12+ environment. The core runs without a cloud account or model key.</p><CopyCommand command={'git clone https://github.com/prakharsingh1/breakroom.git\ncd breakroom\npython3 -m venv venv\nsource venv/bin/activate\npython -m pip install -e ./packages/breakroom-core\nbreakroom doctor'} /><Link href="/docs#quickstart">Open the setup guide →</Link></div></li>
      <li><div className="step-number" aria-hidden="true">02</div><div><h3>Map your agent to the test tools</h3><p>Start with the included adapter, then use its injected TestPay and TestDesk tools for simulated actions. Customer code runs as trusted code on your worker.</p><CopyCommand command={'cd examples/customer-adapter\nbreakroom run --agent adapter:run --pack support-refunds --case normal-refund --case refund-response-lost --case refund-before-commit --case ticket-write-failure --case similar-customers --out ../../artifacts/candidate\ncd ../..'} /><p className="scope-note">The example supports these five initial drills. Replace its implementation with your own agent. Exit 1 means a detected failure; exit 2 means invalid or incomplete evidence.</p><Link href="/docs#adapter">Read the adapter contract →</Link></div></li>
      <li><div className="step-number" aria-hidden="true">03</div><div><h3>Prepare and review one report</h3><p>Minimize the evidence before sharing. Review the file: automated redaction can miss sensitive information.</p><CopyCommand command="breakroom prepare-upload ./artifacts/candidate --case refund-response-lost --out ./artifacts/prepared-report.json" /><button className="button outline small" onClick={onUpload}>Review a file in the browser →</button></div></li>
      <li><div className="step-number" aria-hidden="true">04</div><div><h3>Connect your CI uploads</h3><p>Create a key with <code>reports:write</code> and save it as <code>BREAKROOM_API_TOKEN</code> in your worker’s secret store. Then run this project-specific command.</p>{canManageKeys ? <button className="button outline small" onClick={onKeys}>Create an upload key →</button> : <p className="scope-note">Ask a project owner for a scoped upload key.</p>}{uploadCommand && <CopyCommand command={uploadCommand} />}<p className="scope-note">Uploads run only when you execute this command. No scheduled workflow is enabled by connecting a project.</p></div></li>
    </ol><div className="workspace-soft"><h3>Keep the source with your agent.</h3><p>The workspace stores the minimized report you choose to upload. Agent source, production credentials and local case contents stay on your worker.</p><Link href="/docs#privacy">How private reports work →</Link></div>
  </section>;
}
