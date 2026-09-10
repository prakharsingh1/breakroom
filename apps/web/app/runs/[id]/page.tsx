import { RunDetail } from '@/components/Runs';
export default async function RunPage({ params }: { params: Promise<{ id: string }> }) { const { id } = await params; return <RunDetail id={id} />; }
