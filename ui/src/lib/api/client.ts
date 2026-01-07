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
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API error: ${response.statusText} - ${errorText}`);
  }

  return response.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/status`);

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API error: ${response.statusText} - ${errorText}`);
  }

  return response.json();
}

export async function downloadPDF(jobId: string): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/pdf`);

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(
      `PDF download failed: ${response.statusText} - ${errorText}`,
    );
  }

  return response.blob();
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
