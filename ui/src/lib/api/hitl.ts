import type { PendingApproval, Decision, DecisionResponse } from '$lib/types';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const USE_MOCK = true;

// Mock data for development
const mockPendingJobs: PendingApproval[] = [
	{
		job_id: 'job-001',
		job_posting: {
			title: 'Senior Full Stack Developer',
			company: 'ACME Corp',
			description: `We are seeking a talented Senior Full Stack Developer to join our innovative engineering team. You will be responsible for designing, developing, and maintaining complex web applications that serve millions of users.

Our tech stack includes React, TypeScript, Node.js, and PostgreSQL. You'll work in an agile environment with a focus on clean code, test-driven development, and continuous integration.

As a senior member of the team, you'll mentor junior developers, participate in architecture decisions, and contribute to our engineering culture. We value collaboration, continuous learning, and delivering exceptional user experiences.`,
			location: 'Remote (US)',
			salary: '$120k - $160k',
			posted_at: '2 hours ago',
			requirements: [
				'5+ years of experience with React and TypeScript',
				'Strong Node.js backend development skills',
				'Experience with PostgreSQL or similar RDBMS',
				'Understanding of RESTful API design principles',
				'Experience with cloud platforms (AWS, GCP, or Azure)',
				'Strong communication and collaboration skills',
			],
		},
		cv_json: {},
		pdf_path: '/api/jobs/job-001/pdf',
		retry_count: 0,
		created_at: new Date().toISOString(),
		source: 'linkedin',
		application_url: 'https://careers.acme.com/apply/senior-fullstack',
	},
	{
		job_id: 'job-002',
		job_posting: {
			title: 'Machine Learning Engineer',
			company: 'DataDriven AI',
			description: `DataDriven AI is looking for a Machine Learning Engineer to help build and deploy cutting-edge ML models. You'll work on natural language processing, recommendation systems, and predictive analytics.

We're a fast-growing startup backed by top-tier VCs, and we're building the future of AI-powered business intelligence. Our team consists of PhDs from top universities and experienced engineers from FAANG companies.

You'll have the opportunity to work on challenging problems at scale, publish research papers, and contribute to open-source projects.`,
			location: 'San Francisco, CA (Hybrid)',
			salary: '$150k - $200k + equity',
			posted_at: '1 day ago',
			requirements: [
				'MS/PhD in Computer Science, ML, or related field',
				'3+ years of production ML experience',
				'Proficiency in Python, PyTorch or TensorFlow',
				'Experience with NLP and transformer models',
				'Familiarity with MLOps tools (MLflow, Kubeflow)',
				'Publications in top ML conferences a plus',
			],
		},
		cv_json: {},
		pdf_path: '/api/jobs/job-002/pdf',
		retry_count: 1,
		created_at: new Date(Date.now() - 86400000).toISOString(),
		source: 'url',
		application_url: 'https://datadriven.ai/careers/ml-engineer',
	},
	{
		job_id: 'job-003',
		job_posting: {
			title: 'DevOps Engineer',
			company: 'CloudScale Systems',
			description: `CloudScale Systems is seeking a DevOps Engineer to help us build and maintain our cloud infrastructure. You'll work with Kubernetes, Terraform, and modern CI/CD pipelines.

We're a B2B SaaS company serving Fortune 500 clients. Our infrastructure handles millions of requests per day with 99.99% uptime requirements. Security and reliability are our top priorities.

This role offers the opportunity to work with cutting-edge cloud technologies and contribute to our open-source infrastructure tools.`,
			location: 'New York, NY',
			salary: '$130k - $170k',
			posted_at: '3 days ago',
			requirements: [
				'4+ years of DevOps/SRE experience',
				'Expert knowledge of Kubernetes and Docker',
				'Infrastructure as Code (Terraform, Pulumi)',
				'Experience with AWS or GCP',
				'Strong scripting skills (Bash, Python)',
				'Understanding of security best practices',
			],
		},
		cv_json: {},
		pdf_path: '/api/jobs/job-003/pdf',
		retry_count: 0,
		created_at: new Date(Date.now() - 259200000).toISOString(),
		source: 'manual',
		application_url: 'https://cloudscale.io/jobs/devops',
	},
];

function delay(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function fetchPendingApprovals(): Promise<PendingApproval[]> {
	if (USE_MOCK) {
		await delay(300);
		return [...mockPendingJobs];
	}

	const response = await fetch(`${API_BASE}/api/hitl/pending`);
	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to fetch pending approvals: ${response.statusText} - ${errorText}`);
	}
	return response.json();
}

export async function submitDecision(
	jobId: string,
	decision: Decision,
	feedback?: string
): Promise<DecisionResponse> {
	if (USE_MOCK) {
		await delay(800);
		const statusMap: Record<Decision, 'applying' | 'declined' | 'retrying'> = {
			approved: 'applying',
			declined: 'declined',
			retry: 'retrying',
		};
		return {
			job_id: jobId,
			status: statusMap[decision],
			message: `Application ${decision}`,
		};
	}

	const response = await fetch(`${API_BASE}/api/hitl/${jobId}/decide`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ decision, feedback }),
	});
	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to submit decision: ${response.statusText} - ${errorText}`);
	}
	return response.json();
}

export async function fetchCVHtml(jobId: string): Promise<string> {
	if (USE_MOCK) {
		await delay(200);
		return `
			<div style="font-family: 'DM Sans', sans-serif; padding: 2rem; max-width: 800px;">
				<h1 style="font-family: 'JetBrains Mono', monospace; font-size: 1.5rem; margin-bottom: 0.5rem;">John Developer</h1>
				<p style="color: #666; margin-bottom: 1.5rem;">Senior Software Engineer | john@example.com | (555) 123-4567</p>

				<h2 style="font-family: 'JetBrains Mono', monospace; font-size: 1rem; border-bottom: 2px solid #000; padding-bottom: 0.25rem; margin-bottom: 0.75rem;">PROFESSIONAL SUMMARY</h2>
				<p style="margin-bottom: 1.5rem;">Experienced software engineer with 8+ years of expertise in full-stack development, specializing in React, TypeScript, and Node.js. Proven track record of delivering high-quality applications at scale.</p>

				<h2 style="font-family: 'JetBrains Mono', monospace; font-size: 1rem; border-bottom: 2px solid #000; padding-bottom: 0.25rem; margin-bottom: 0.75rem;">EXPERIENCE</h2>
				<div style="margin-bottom: 1rem;">
					<strong>Senior Software Engineer</strong> - TechCorp Inc.<br>
					<span style="color: #666; font-size: 0.875rem;">2020 - Present</span>
					<ul style="margin-top: 0.5rem; padding-left: 1.25rem;">
						<li>Led development of microservices architecture serving 10M+ users</li>
						<li>Reduced API response time by 40% through optimization</li>
						<li>Mentored team of 5 junior developers</li>
					</ul>
				</div>

				<h2 style="font-family: 'JetBrains Mono', monospace; font-size: 1rem; border-bottom: 2px solid #000; padding-bottom: 0.25rem; margin-bottom: 0.75rem;">SKILLS</h2>
				<p>TypeScript, React, Node.js, PostgreSQL, AWS, Docker, Kubernetes, GraphQL</p>

				<p style="text-align: center; color: #999; font-size: 0.75rem; margin-top: 2rem;">[Mock CV for job ${jobId}]</p>
			</div>
		`;
	}

	const response = await fetch(`${API_BASE}/api/jobs/${jobId}/html`);
	if (!response.ok) {
		const errorText = await response.text();
		throw new Error(`Failed to fetch CV HTML: ${response.statusText} - ${errorText}`);
	}
	return response.text();
}

export function getPdfUrl(jobId: string): string {
	return `${API_BASE}/api/jobs/${jobId}/pdf`;
}

export function downloadPdf(jobId: string): void {
	window.open(getPdfUrl(jobId), '_blank');
}
