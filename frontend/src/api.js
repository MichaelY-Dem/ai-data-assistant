// Talks to the FastAPI backend through the Vite dev proxy (see vite.config.js).

const BACKEND_DOWN =
  "Can't reach the backend. Make sure the FastAPI server is running on http://127.0.0.1:8000 and try again.";

export class ChatError extends Error {}

export async function askQuestion(question) {
  let response;
  try {
    response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
  } catch {
    throw new ChatError(BACKEND_DOWN);
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body: when the backend is down, the Vite proxy answers with an empty 5xx.
  }

  if (!response.ok) {
    throw new ChatError(errorMessage(response.status, body));
  }
  if (!body || typeof body.answer !== "string") {
    throw new ChatError("The backend sent an unexpected response. Please try again.");
  }
  return body; // { answer, tools_used, duration_ms }
}

function errorMessage(status, body) {
  const detail = body?.detail;
  if (status === 504) {
    return "The question took too long to answer. Try a narrower question, or ask again.";
  }
  if (status === 422) {
    return "The question couldn't be accepted. Please check it isn't empty or longer than 4,000 characters.";
  }
  if (typeof detail === "string" && detail) {
    return `Something went wrong while analyzing the portfolio. ${detail}`;
  }
  if (status >= 500 && body === null) {
    return BACKEND_DOWN;
  }
  return `The backend returned an error (HTTP ${status}). Please try again.`;
}

export async function checkHealth() {
  try {
    const response = await fetch("/health");
    return response.ok;
  } catch {
    return false;
  }
}

// --------------------------------------------------------------------------
// Documents (Phase 6)
// --------------------------------------------------------------------------

export const MAX_UPLOAD_MB = 10;
export const ALLOWED_EXTENSIONS = [".pdf", ".xlsx"];

async function requestJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new ChatError(BACKEND_DOWN);
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    // empty body: backend down behind the Vite proxy
  }
  if (!response.ok) {
    const detail = body?.detail;
    if (response.status === 504) {
      throw new ChatError("Claude took too long to answer. Please try again.");
    }
    if (typeof detail === "string" && detail) throw new ChatError(detail);
    if (Array.isArray(detail) && detail[0]?.msg) throw new ChatError(detail[0].msg);
    if (response.status >= 500 && body === null) throw new ChatError(BACKEND_DOWN);
    throw new ChatError(`The backend returned an error (HTTP ${response.status}).`);
  }
  return body;
}

export function listCompanies() {
  return requestJson("/companies");
}

export function listDocuments() {
  return requestJson("/documents");
}

export function uploadDocument(file, companyCode) {
  const form = new FormData();
  form.append("file", file);
  if (companyCode) form.append("company_code", companyCode);
  return requestJson("/documents/upload", { method: "POST", body: form });
}

export function summarizeDocument(id) {
  return requestJson(`/documents/${encodeURIComponent(id)}/summarize`, { method: "POST" });
}

export function onePageSummary(companyCode) {
  return requestJson(`/companies/${encodeURIComponent(companyCode)}/one-page-summary`, { method: "POST" });
}
