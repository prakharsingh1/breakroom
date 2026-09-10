'use client';
import { useState } from 'react';
export default function ExportLink({ id, children = 'Export runnable test ↓', className = 'text-link' }: { id: string; children?: React.ReactNode; className?: string }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function download(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault(); if (busy) return; setBusy(true); setError('');
    try {
      const response = await fetch('/api/runs/' + encodeURIComponent(id) + '/export');
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(typeof detail.detail === 'string' ? detail.detail : 'Export unavailable. Execute a new run and try again.');
      }
      if (!response.headers.get('content-type')?.includes('application/zip')) throw new Error('The export service returned an unsupported format.');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = 'breakroom-regression.zip'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 15000);
    } catch (e) { setError(e instanceof Error ? e.message : 'Export unavailable. Check the local API connection.'); }
    finally { setBusy(false); }
  }
  return <span className="export-control"><a className={className} href={'/api/runs/' + encodeURIComponent(id) + '/export'} onClick={download} aria-busy={busy}>{busy ? 'Preparing export…' : children}</a>{error && <span className="export-error" role="alert">{error}</span>}</span>;
}
