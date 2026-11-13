// Talks to the FastAPI backend through the Vite dev proxy (see vite.config.js).
// Every request except /health and /login carries the signed-in user's token.

const BACKEND_DOWN =
  "Can't reach the backend. Make sure the FastAPI server is running on http://127.0.0.1:8000 and try again.";

export class ChatError extends Error {}

// --------------------------------------------------------------------------
// Session: the token lives in memory and in sessionStorage (cleared when the tab closes).
// --------------------------------------------------------------------------

const SESSION_KEY = "portfolio-assistant-session";
let session = null; // { token, expiresAt, user: { username, display_name, role } }
let onSessionEnded = () => {};

export function loadSession() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    if (saved?.token && new Date(saved.expiresAt) > new Date()) {
      session = saved;
      return saved;
    }
  } catch {
    // storage blocked or corrupt: start signed out
  }
  clearSession();
  return null;
}

function saveSession(value) {
  session = value;
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
  } catch {
    // storage blocked: the session still works until the page is reloaded
  }
}

export function clearSession() {
  session = null;
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // ignore
  }
}

// Called with a message when the backend rejects the token (expired, revoked, ...).
export function setSessionEndedHandler(handler) {
  onSessionEnded = handler;
}

export async function signIn(username, password) {
  let response;
  try {
    response = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw new ChatError(BACKEND_DOWN);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) throw new ChatError("Wrong username or password.");
    if (response.status === 422) throw new ChatError("Enter your username and password.");
    if (typeof body?.detail === "string") throw new ChatError(body.detail);
    if (response.status >= 500 && body === null) throw new ChatError(BACKEND_DOWN);
    throw new ChatError(`Sign-in failed (HTTP ${response.status}).`);
  }
  const value = { token: body.access_token, expiresAt: body.expires_at, user: body.user };
  saveSession(value);
  return value;
}

export function signOut() {
  clearSession();
}

// fetch() with the bearer token. A 401 ends the session (the token expired or was revoked).
async function authFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (session?.token) headers.set("Authorization", `Bearer ${session.token}`);
  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch {
    throw new ChatError(BACKEND_DOWN);
  }
  if (response.status === 401) {
    const body = await response.json().catch(() => null);
    const message = typeof body?.detail === "string" ? body.detail : "Please sign in again.";
    clearSession();
    onSessionEnded(message);
    throw new ChatError(message);
  }
  return response;
}

export async function fetchCurrentUser() {
  const response = await authFetch("/me");
  if (!response.ok) throw new ChatError(`Couldn't check your session (HTTP ${response.status}).`);
  return response.json();
}

export async function askQuestion(question) {
  const response = await authFetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

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
  if (status === 429) {
    return typeof detail === "string" ? detail : "You're asking too quickly. Wait a minute and try again.";
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
  const response = await authFetch(url, options);
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
