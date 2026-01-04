// API Request/Response Types

export interface JobSubmitRequest {
	source: 'manual';
	mode: 'mvp';
	job_description: {
		title: string;
		company: string;
		description: string;
		requirements: string;
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
	retry_count: number;
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
