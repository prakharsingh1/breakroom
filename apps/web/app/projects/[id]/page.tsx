import TeamProject from '@/components/TeamProject';
export default async function ProjectPage({ params }: { params: Promise<{id:string}> }) { const {id} = await params; return <TeamProject id={id} />; }
