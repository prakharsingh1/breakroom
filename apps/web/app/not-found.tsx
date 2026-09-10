import Link from 'next/link';
export default function NotFound() { return <div className="wrap inner-page"><span className="eyebrow">404 / NO RECORD HERE</span><h1>A missing page.<br />Not a missing response.</h1><p className="lead">This route does not exist. The Fire Drills are a good place to start.</p><Link className="button accent" href="/fire-drills">Open Fire Drills →</Link></div>; }
