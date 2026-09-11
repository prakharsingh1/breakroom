import Link from 'next/link';

export default function HostingNotice() {
  if (process.env.NEXT_PUBLIC_BREAKROOM_SITE_ONLY !== '1') return null;
  return <aside className="hosting-notice" aria-label="Service availability"><strong>Documentation is live.</strong> Hosted accounts and agent runs are not available on this deployment yet. <Link href="/docs#quickstart">Run Breakroom locally →</Link></aside>;
}
