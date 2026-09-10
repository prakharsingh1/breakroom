import TeamReport from '@/components/TeamReport';
export default async function ProjectReportPage({ params }: { params: Promise<{id:string;reportId:string}> }) { const {id,reportId} = await params; return <TeamReport projectId={id} reportId={reportId} />; }
