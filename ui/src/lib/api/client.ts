import type {
  JobSubmitRequest,
  JobSubmitResponse,
  JobStatusResponse,
  TemplateName,
  LLMProvider,
  LLMModel,
} from "$lib/types";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export async function submitJob(
  jobDescription: string,
  templateName: TemplateName = "compact",
  llmProvider: LLMProvider = "openai",
  llmModel: LLMModel = "gpt-4o-mini",
): Promise<JobSubmitResponse> {
  // Generate placeholders in case LLM extraction fails
  const timestamp = new Date().toISOString().split("T")[0]; // YYYY-MM-DD

  const requestBody: JobSubmitRequest = {
    source: "manual",
    mode: "mvp",
    job_description: {
      title: `mvp-${timestamp}-title`, // Placeholder if LLM fails
      company: `mvp-${timestamp}-company`, // Placeholder if LLM fails
      description: jobDescription,
      requirements: "", // LLM will extract or leave empty
      template_name: templateName,
      llm_provider: llmProvider,
      llm_model: llmModel,
    },
  };

  const response = await fetch(`${API_BASE_URL}/api/jobs/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(requestBody),
    credentials: "include",
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API error: ${response.statusText} - ${errorText}`);
  }

  return response.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/status`, {
    credentials: "include",
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API error: ${response.statusText} - ${errorText}`);
  }

  return response.json();
}

export async function downloadPDF(jobId: string): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/pdf`, {
    credentials: "include",
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(
      `PDF download failed: ${response.statusText} - ${errorText}`,
    );
  }

  return response.blob();
}

export async function triggerLinkedInSearch(): Promise<{
  status: string;
  message: string;
}> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/linkedin-search`, {
    method: "POST",
    credentials: "include",
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to trigger search: ${response.statusText} - ${errorText}`);
  }

  return response.json();
}

export type UserLastRun = {
  time: string;
  jobs_found: number;
  reason: "ok" | "no_results" | "no_users" | "scrape_failed" | "auth_failed";
  search_url: string | null;
  message: string | null;
};

export async function getLinkedInSearchStatus(): Promise<{
  enabled: boolean;
  running: boolean;
  last_run_time: string | null;
  last_run_jobs: number;
  next_run_time: string | null;
  queue_size: number;
  user_last_run: UserLastRun | null;
}> {
  const response = await fetch(
    `${API_BASE_URL}/api/jobs/linkedin-search/status`,
    { credentials: "include" },
  );

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to get search status: ${response.statusText} - ${errorText}`);
  }

  return response.json();
}

// Helper to trigger browser download
export function triggerDownload(blob: Blob, filename: string): boolean {
  try {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    return true; // Auto-download succeeded
  } catch (error) {
    console.error("Auto-download failed:", error);
    return false; // Auto-download blocked, need fallback button
  }
}
