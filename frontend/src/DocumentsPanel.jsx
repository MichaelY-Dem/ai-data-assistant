import { useEffect, useMemo, useRef, useState } from "react";
import Markdown from "./Markdown.jsx";
import {
  ALLOWED_EXTENSIONS,
  MAX_UPLOAD_MB,
  listCompanies,
  listDocuments,
  onePageSummary,
  summarizeDocument,
  uploadDocument,
} from "./api.js";

let nextResultId = 1;

export default function DocumentsPanel() {
  const [companies, setCompanies] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [results, setResults] = useState([]); // newest first
  const [busy, setBusy] = useState({}); // key -> true while a Claude call runs
  const [onlyWithDocs, setOnlyWithDocs] = useState(true);

  async function refresh() {
    try {
      const [c, d] = await Promise.all([listCompanies(), listDocuments()]);
      setCompanies(c);
      setDocuments(d);
      setLoadError(null);
    } catch (err) {
      setLoadError(err.message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const companyName = useMemo(
    () => Object.fromEntries(companies.map((c) => [c.company_code, c.name])),
    [companies],
  );

  const docCount = useMemo(() => {
    const counts = {};
    for (const d of documents) if (d.company_code) counts[d.company_code] = (counts[d.company_code] || 0) + 1;
    return counts;
  }, [documents]);

  const shownCompanies = onlyWithDocs ? companies.filter((c) => docCount[c.company_code]) : companies;

  async function runClaude(key, title, call, describe) {
    if (busy[key]) return;
    setBusy((b) => ({ ...b, [key]: true }));
    const id = nextResultId++;
    setResults((r) => [{ id, title, pending: true }, ...r]);
    try {
      const body = await call();
      setResults((r) => r.map((x) => (x.id === id ? { id, title, text: body.summary, meta: describe(body) } : x)));
    } catch (err) {
      setResults((r) => r.map((x) => (x.id === id ? { id, title, error: err.message } : x)));
    } finally {
      setBusy((b) => ({ ...b, [key]: false }));
    }
  }

  function summarize(doc) {
    runClaude(`doc:${doc.id}`, `Summary: ${doc.filename}`, () => summarizeDocument(doc.id), (b) =>
      `${(b.duration_ms / 1000).toFixed(1)} s`,
    );
  }

  function onePager(company) {
    runClaude(
      `co:${company.company_code}`,
      `One-page summary: ${company.name} (${company.company_code})`,
      () => onePageSummary(company.company_code),
      (b) =>
        `Documents: ${b.documents_used.length ? b.documents_used.join(", ") : "none"} · ` +
        `Tools: ${b.tools_used.length ? [...new Set(b.tools_used)].join(", ") : "none"} · ` +
        `${(b.duration_ms / 1000).toFixed(1)} s`,
    );
  }

  return (
    <div className="docs-inner">
      {loadError && (
        <div className="banner" role="alert">
          {loadError}{" "}
          <button type="button" className="link-button" onClick={refresh}>
            Retry
          </button>
        </div>
      )}

      {results.map((r) => (
        <ResultCard key={r.id} result={r} onClose={() => setResults((all) => all.filter((x) => x.id !== r.id))} />
      ))}

      <UploadCard companies={companies} onUploaded={refresh} />

      <section className="card">
        <div className="card-head">
          <h2>Uploaded documents</h2>
          <button type="button" className="btn secondary" onClick={refresh}>
            Refresh
          </button>
        </div>
        {documents.length === 0 ? (
          <p className="muted">No documents yet. Upload a PDF or Excel file above.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Company</th>
                  <th>Contents</th>
                  <th>Uploaded</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {documents.map((d) => (
                  <tr key={d.id}>
                    <td className="filename">
                      <span className={`kind ${d.kind}`}>{d.kind === "pdf" ? "PDF" : "XLSX"}</span>
                      {d.filename}
                    </td>
                    <td>{d.company_code ? `${companyName[d.company_code] || ""} (${d.company_code})` : "—"}</td>
                    <td>
                      {d.section_count} {d.kind === "pdf" ? "page" : "sheet"}
                      {d.section_count === 1 ? "" : "s"} · {formatSize(d.size_bytes)}
                      {d.truncated && " · text cut"}
                    </td>
                    <td>{formatDate(d.uploaded_at)}</td>
                    <td className="actions">
                      <button type="button" className="btn" disabled={busy[`doc:${d.id}`]} onClick={() => summarize(d)}>
                        {busy[`doc:${d.id}`] ? "Summarizing..." : "Summarize"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Company one-pagers</h2>
          <label className="check">
            <input type="checkbox" checked={onlyWithDocs} onChange={(e) => setOnlyWithDocs(e.target.checked)} />
            Only companies with documents
          </label>
        </div>
        <p className="muted small">
          Combines the company's database figures with its uploaded documents, with sources cited.
        </p>
        {shownCompanies.length === 0 ? (
          <p className="muted">
            {companies.length ? "No company has linked documents yet." : "Companies couldn't be loaded."}
          </p>
        ) : (
          <ul className="company-list">
            {shownCompanies.map((c) => (
              <li key={c.company_code}>
                <div>
                  <strong>{c.name}</strong>
                  <span className="muted small">
                    {" "}
                    {c.company_code} · {c.sector.replace("_", " ")} · {docCount[c.company_code] || 0} doc
                    {docCount[c.company_code] === 1 ? "" : "s"}
                  </span>
                </div>
                <button
                  type="button"
                  className="btn"
                  disabled={busy[`co:${c.company_code}`]}
                  onClick={() => onePager(c)}
                >
                  {busy[`co:${c.company_code}`] ? "Writing..." : "One-page summary"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

    </div>
  );
}

function UploadCard({ companies, onUploaded }) {
  const [file, setFile] = useState(null);
  const [companyCode, setCompanyCode] = useState("");
  const [status, setStatus] = useState(null); // {kind: "ok"|"error", text}
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef(null);

  function pick(f) {
    setStatus(null);
    if (!f) return setFile(null);
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setFile(null);
      return setStatus({ kind: "error", text: "Only PDF (.pdf) and Excel (.xlsx) files can be uploaded." });
    }
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setFile(null);
      return setStatus({ kind: "error", text: `That file is ${formatSize(f.size)}; the limit is ${MAX_UPLOAD_MB} MB.` });
    }
    setFile(f);
  }

  async function submit(e) {
    e.preventDefault();
    if (!file || uploading) return;
    setUploading(true);
    setStatus(null);
    try {
      const doc = await uploadDocument(file, companyCode);
      setStatus({
        kind: "ok",
        text: `Uploaded ${doc.filename}: ${doc.section_count} ${doc.kind === "pdf" ? "page" : "sheet"}(s) of text extracted.`,
      });
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onUploaded();
    } catch (err) {
      setStatus({ kind: "error", text: err.message });
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="card">
      <h2>Upload a document</h2>
      <form className="upload-form" onSubmit={submit}>
        <label className="field">
          <span>File (PDF or Excel, max {MAX_UPLOAD_MB} MB)</span>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.xlsx,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(e) => pick(e.target.files?.[0])}
          />
        </label>
        <label className="field">
          <span>Company (optional)</span>
          <select value={companyCode} onChange={(e) => setCompanyCode(e.target.value)}>
            <option value="">Not linked to a company</option>
            {companies.map((c) => (
              <option key={c.company_code} value={c.company_code}>
                {c.name} ({c.company_code})
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn primary" disabled={!file || uploading}>
          {uploading ? "Uploading..." : "Upload"}
        </button>
      </form>
      {status && (
        <p className={`upload-status ${status.kind}`} role={status.kind === "error" ? "alert" : "status"}>
          {status.text}
        </p>
      )}
    </section>
  );
}

function ResultCard({ result, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  return (
    <section className="card result" ref={ref}>
      <div className="card-head">
        <h2>{result.title}</h2>
        <button type="button" className="btn secondary" onClick={onClose} aria-label="Close result">
          Close
        </button>
      </div>
      {result.pending && (
        <div className="loading" role="status">
          <span className="dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          Claude is reading...
        </div>
      )}
      {result.error && (
        <div className="bubble error" role="alert">
          {result.error}
        </div>
      )}
      {result.text && (
        <>
          <div className="markdown">
            <Markdown>{result.text}</Markdown>
          </div>
          <div className="meta">{result.meta}</div>
        </>
      )}
    </section>
  );
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
