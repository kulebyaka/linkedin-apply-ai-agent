// API Request/Response Types

export type TemplateName = 'compact' | 'modern' | 'profile-card';
export type LLMProvider = 'openai' | 'anthropic';
export type LLMModel = 'gpt-5-mini' | 'gpt-4o-mini' | 'gpt-4o' | 'gpt-3.5-turbo' | 'claude-haiku-4.5';

export interface JobSubmitRequest {
	source: 'manual';
	mode: 'mvp';
	job_description: {
		title: string;
		company: string;
		description: string;
		requirements: string;
		template_name?: TemplateName;
		llm_provider?: LLMProvider;
		llm_model?: LLMModel;
	};
}

export interface JobSubmitResponse {
	job_id: string;
	status: 'queued';
	message: string;
}

export interface JobStatusResponse {
	job_id: string;
	source: 'manual';
	mode: 'mvp';
	status: 'queued' | 'extracting' | 'composing_cv' | 'generating_pdf' | 'completed' | 'failed';
	job_posting?: {
		title: string;
		company: string;
		description: string;
		requirements: string;
	};
	pdf_path?: string;
	error_message?: string;
	attempt_count: number;
	created_at: string;
}

// Client State Types

export type AppStatus = 'idle' | 'submitting' | 'polling' | 'completed' | 'failed';
export type WorkflowStep = 'queued' | 'extracting' | 'composing_cv' | 'generating_pdf' | 'completed' | 'failed';

export interface AppState {
	// Input
	jobDescription: string;

	// Workflow state
	status: AppStatus;
	currentStep: WorkflowStep;

	// Job tracking
	jobId: string | null;

	// Download state
	pdfBlob: Blob | null;
	autoDownloadFailed: boolean;

	// Error handling
	errorMessage: string | null;

	// Polling control
	pollingInterval: number | null;
}

// HITL Review Types

export interface JobPosting {
	title: string;
	company: string;
	description: string;
	location?: string;
	salary?: string;
	posted_at?: string;
	requirements?: string[];
}

export interface FilterResult {
	score: number;
	red_flags: string[];
	disqualified: boolean;
	disqualifier_reason: string | null;
	reasoning: string;
}

export interface UserFilterPreferences {
	natural_language_prefs: string;
	custom_prompt: string | null;
	reject_threshold: number;
	warning_threshold: number;
	enabled: boolean;
}

export interface PendingApproval {
	job_id: string;
	job_posting: JobPosting;
	cv_json: Record<string, unknown>;
	pdf_path: string;
	attempt_count: number;
	created_at: string;
	source: 'url' | 'manual' | 'linkedin';
	application_url: string;
	filter_result?: FilterResult;
}

export type Decision = 'approved' | 'declined' | 'retry';

export interface DecisionPayload {
	decision: Decision;
	feedback?: string;
}

export interface DecisionResponse {
	job_id: string;
	status: 'applying' | 'declined' | 'retrying';
	message: string;
}
