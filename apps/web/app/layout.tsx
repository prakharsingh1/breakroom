import type { Metadata } from 'next';
import Link from 'next/link';
import HostingNotice from '@/components/HostingNotice';
import './globals.css';
import './team.css';
import './accounts.css';
export const metadata: Metadata = { title: 'Breakroom — Crash tests for AI agents', description: 'Test action-taking support agents against controlled failures, with simulated customers and money.' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const siteOnly = process.env.NEXT_PUBLIC_BREAKROOM_SITE_ONLY === '1';
  const github = process.env.BREAKROOM_GITHUB_URL || 'https://github.com/prakharsingh1/breakroom';
  const validGithub = github && /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/?$/.test(github);
  return <html lang="en"><body><a className="skip-link" href="#main">Skip to content</a><header className="site-header"><Link href="/" className="wordmark" aria-label="Breakroom home">break<span>room</span><i aria-hidden="true">.</i></Link><nav aria-label="Main navigation"><Link href="/fire-drills">Fire Drills</Link><Link href="/docs">Docs</Link><Link href="/pricing">Pricing</Link>{validGithub && <a href={github} target="_blank" rel="noreferrer">GitHub ↗</a>}</nav><div className="header-account"><Link href={siteOnly ? "/docs#quickstart" : "/signin"} className="header-signin">{siteOnly ? "Get started" : "Sign in"}</Link><Link href={siteOnly ? "/fire-drills" : "/projects"} className="header-cta">{siteOnly ? "Fire Drills" : "Workspace"} <span aria-hidden="true">↗</span></Link></div></header><HostingNotice /><main id="main" tabIndex={-1}>{children}</main><footer className="site-footer"><div><Link className="wordmark" href="/">breakroom<span>.</span></Link><p>A controlled environment for uncontrolled outcomes.</p></div><div><Link href={siteOnly ? "/docs#quickstart" : "/projects"}>{siteOnly ? "Run locally" : "Your workspace"}</Link><Link href="/docs#limits">Know the limits</Link><Link href="/docs#privacy">Privacy & execution</Link><span>Built for the work before release.</span></div></footer></body></html>;
}
