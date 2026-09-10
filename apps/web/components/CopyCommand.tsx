'use client';
import { useState } from 'react';
export default function CopyCommand({ command }: { command: string }) {
  const [message, setMessage] = useState('Copy');
  async function copy() {
    try { await navigator.clipboard.writeText(command); setMessage('Copied'); }
    catch { setMessage('Select the command to copy'); }
  }
  return <div className="command"><pre><code>{command}</code></pre><button onClick={copy} aria-label={`Copy command: ${command}`}>{message}</button><span className="sr-only" role="status">{message === 'Copied' ? 'Command copied to clipboard' : ''}</span></div>;
}
