'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api, Drill } from '@/lib/contracts';
export default function DrillLibrary({ preview = false }: { preview?: boolean }) {
  const [drills, setDrills] = useState<Drill[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  function load() { setLoading(true); setError(''); api<{ drills: Drill[] }>('drills').then(data => setDrills(data.drills)).catch(e => setError(e.message)).finally(() => setLoading(false)); }
  useEffect(() => { let active = true; api<{ drills: Drill[] }>('drills').then(data => { if (active) setDrills(data.drills); }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, []);
  if (error) return <div className="light-error" role="alert"><p>{error}</p><button className="button outline" onClick={load}>Retry loading Fire Drills</button></div>;
  if (loading) return <p className="loading-text" role="status">Loading versioned Fire Drill manifests…</p>;
  if (!drills.length) return <p>No implemented drills are available from this API.</p>;
  const Heading = preview ? 'h3' : 'h2';
  return <div className="drill-list">{drills.slice(0, preview ? 3 : undefined).map(drill => <Link href={'/fire-drills/' + drill.case_id} className="drill-row" key={drill.case_id}><span className="drill-number">{String(drill.number).padStart(2, '0')}</span><div><Heading>{drill.name}</Heading><p>{drill.summary}</p></div><div className="drill-meta"><span className={'severity severity-' + drill.severity}>{drill.severity}</span><span>{drill.provenance} · v{drill.case_version}</span><span>{drill.demo_available ? 'Live demo + local' : 'Run locally'}</span></div><span className="row-arrow">↗</span></Link>)}</div>;
}
