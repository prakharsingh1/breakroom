import DrillDetail from '@/components/DrillDetail';
export default async function DrillPage({ params }: { params: Promise<{ slug: string }> }) { const { slug } = await params; return <DrillDetail slug={slug} />; }
