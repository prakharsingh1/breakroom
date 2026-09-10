'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { teamApi, TeamApiError } from '@/lib/team';

type Usage = { seats:number; reports:number; storage_bytes:number };
type Billing = {
  mode:'disabled'|'test'; configured:boolean; provider:string; amount_minor:number; currency:string; interval:string;
  status:string; access:'local_development'|'paid'|'read_only'; cancel_at_period_end:boolean;
  current_period_end:string|null; paid_through:string|null; can_cancel:boolean;
  sync_state:'ready'|'pending'|'retrying'|'unknown'; limits:Usage; usage:Usage; checkout_available:boolean; notice:string;
};
const date = (value:string|null) => value ? new Date(value).toLocaleString() : 'Not established';
const size = (bytes:number) => (bytes/(1024*1024)).toLocaleString(undefined,{maximumFractionDigits:2})+' MiB';

export default function TeamBilling({projectId,csrf}:{projectId:string;csrf:string|null}) {
  const base='projects/'+encodeURIComponent(projectId)+'/billing';
  const [billing,setBilling]=useState<Billing|null>(null);
  const [error,setError]=useState(''); const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState(''); const [confirmCancel,setConfirmCancel]=useState(false);
  const attempts=useRef<Record<string,string>>({});
  const refresh=useCallback(async()=>{setError('');try{setBilling(await teamApi<Billing>(base));}catch(e){setError(e instanceof Error?e.message:'Billing state unavailable.');}},[base]);
  useEffect(()=>{void refresh();},[refresh]);
  const storageName=(operation:string)=>'breakroom:billing-attempt:'+projectId+':'+operation;
  function key(operation:string) {
    // These random retry identifiers are not credentials. Keeping them for the
    // browser tab lets a reload retry an uncertain provider request safely.
    if(!attempts.current[operation]) {try{const saved=sessionStorage.getItem(storageName(operation));if(saved&&/^[a-f0-9]{64}$/.test(saved))attempts.current[operation]=saved;}catch{/* Memory retry remains available when storage is blocked. */}}
    if(!attempts.current[operation]) attempts.current[operation]=Array.from(crypto.getRandomValues(new Uint8Array(32)),n=>n.toString(16).padStart(2,'0')).join('');
    try{sessionStorage.setItem(storageName(operation),attempts.current[operation]);}catch{/* This is a convenience, not authorization. */}
    return attempts.current[operation];
  }
  function clearKey(operation:string){delete attempts.current[operation];try{sessionStorage.removeItem(storageName(operation));}catch{/* No retained browser copy. */}}
  async function synchronize(){
    setBusy(true);setError('');
    try{if(billing?.mode==='test')setBilling(await teamApi<Billing>(base+'/reconcile',{method:'POST',csrf}));else await refresh();}
    catch(e){setError(e instanceof Error?e.message:'Provider synchronization failed.');}
    finally{setBusy(false);}
  }
  async function checkout() {
    setBusy(true);setError('');setNotice('');
    try {
      const result=await teamApi<{url:string;test_mode:boolean}>(base+'/checkout',{method:'POST',csrf,body:{plan:'team'},headers:{'Idempotency-Key':key('checkout')}});
      const target=new URL(result.url);
      if(!result.test_mode || target.protocol!=='https:' || target.hostname!=='checkout.stripe.com' || target.username || target.password || target.port) throw new Error('The provider returned an unsupported checkout destination.');
      window.location.assign(target.href);
    } catch(e){if(e instanceof TeamApiError&&e.status===410)clearKey('checkout');setError(e instanceof Error?e.message:'Test checkout failed.');}
    finally{setBusy(false);}
  }
  async function cancel(atPeriodEnd:boolean) {
    setBusy(true);setError('');setNotice('');
    const operation=atPeriodEnd?'cancel-period-end':'cancel-now';
    try {
      const result=await teamApi<Billing>(base+'/cancel',{method:'POST',csrf,body:{at_period_end:atPeriodEnd},headers:{'Idempotency-Key':key(operation)}});
      setBilling(result);setConfirmCancel(false);
      setNotice(atPeriodEnd?'Cancellation requested for the end of the paid period. Refresh to check the confirmed provider state.':'Immediate cancellation requested. Refresh to check the confirmed provider state before deleting the project.');
      clearKey(operation);
    } catch(e){setError(e instanceof Error?e.message:'Cancellation failed.');}
    finally{setBusy(false);}
  }
  return <section className="team-section billing-section">
    <div className="section-heading"><h2>Subscription & usage</h2><button className="button outline small" disabled={busy} onClick={()=>void synchronize()}>Refresh billing state</button></div>
    {error&&<p className="light-error" role="alert">{error}</p>}
    {notice&&<p className="team-notice" role="status">{notice}</p>}
    {!billing&&!error&&<p role="status">Loading billing state…</p>}
    {billing&&<>
      <div className="development-note"><strong>{billing.mode==='disabled'?'Checkout unavailable — no billing configured':'TEST MODE · No live subscription charges'}</strong><p>{billing.notice}</p></div>
      <p className="billing-intro">Customer-run tests use your own compute and optional model account. The local core stays free to run without a subscription.</p>
      <dl className="billing-facts"><div><dt>Subscription state</dt><dd>{billing.status.replaceAll('_',' ')}</dd></div><div><dt>Project access</dt><dd>{billing.access.replaceAll('_',' ')}</dd></div><div><dt>Provider synchronization</dt><dd>{billing.sync_state}</dd></div><div><dt>Paid through</dt><dd>{date(billing.paid_through)}</dd></div></dl>
      {billing.cancel_at_period_end&&<p className="team-notice">Cancellation is scheduled. Current period ends {date(billing.current_period_end)}.</p>}
      <h3>Project limits</h3><div className="usage-table"><table><caption>Current usage and configured limits</caption><thead><tr><th scope="col">Resource</th><th scope="col">Used</th><th scope="col">Limit</th></tr></thead><tbody><tr><th scope="row">Project seats</th><td>{billing.usage.seats}</td><td>{billing.limits.seats}</td></tr><tr><th scope="row">Retained reports</th><td>{billing.usage.reports}</td><td>{billing.limits.reports}</td></tr><tr><th scope="row">Report storage</th><td>{size(billing.usage.storage_bytes)}</td><td>{size(billing.limits.storage_bytes)}</td></tr></tbody></table></div>
      {billing.access==='read_only'&&<p>New uploads and membership additions require an active entitlement. Authorized members can still read and export retained reports; owners can manage deletion.</p>}
      {billing.checkout_available&&billing.mode==='test'&&<div className="billing-checkout"><h3>Try the configured test plan</h3><p>{new Intl.NumberFormat(undefined,{style:'currency',currency:billing.currency}).format(billing.amount_minor/100)} / {billing.interval}, in the provider’s test environment. Returning from checkout does not grant access; the server verifies subscription and payment state.</p><button className="button accent" disabled={busy} onClick={()=>void checkout()}>Open test checkout ↗</button></div>}
      {billing.can_cancel&&billing.mode==='test'&&<div className="billing-cancel"><h3>Cancel this test subscription</h3><p>End-of-period cancellation keeps the confirmed paid access until its expiry. Immediate cancellation ends access now and is required before deleting a project with an active subscription.</p><div className="team-actions"><button className="button outline" disabled={busy||billing.cancel_at_period_end} onClick={()=>void cancel(true)}>Cancel at period end</button><button className="button outline" disabled={busy} onClick={()=>setConfirmCancel(true)}>Cancel immediately…</button></div>{confirmCancel&&<div className="danger-zone"><p>Confirm immediate cancellation of this project’s test subscription. New uploads and seat additions will stop when the cancellation is confirmed.</p><div className="team-actions"><button className="button danger" disabled={busy} onClick={()=>void cancel(false)}>Confirm immediate cancellation</button><button className="button outline" disabled={busy} onClick={()=>setConfirmCancel(false)}>Keep subscription</button></div></div>}</div>}
    </>}
  </section>;
}
