import Compare from '@/components/Compare';
export const metadata = { title: 'Compare — Breakroom' };
export default async function ComparePage({ searchParams }: { searchParams: Promise<{ baseline?: string; candidate?: string }> }) { const query = await searchParams; return <Compare baselineId={query.baseline} candidateId={query.candidate} />; }
