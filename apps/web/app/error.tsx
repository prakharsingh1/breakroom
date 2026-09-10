'use client';
export default function ErrorPage({ reset }: { reset: () => void }) { return <div className="wrap inner-page"><h1>This view could not load.</h1><p role="alert">No successful test result is implied. Try reloading the view or use the local CLI.</p><button className="button accent" onClick={reset}>Try again</button></div>; }
