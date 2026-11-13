import { useEffect, useRef, useState } from "react";
import { askQuestion, checkHealth } from "./api.js";
import DocumentsPanel from "./DocumentsPanel.jsx";
import Markdown from "./Markdown.jsx";

const EXAMPLE_QUESTIONS = [
  "Which energy companies missed their revenue target in the most recent quarter?",
  "Which sector has the highest average EBITDA in 2025?",
  "Which companies have the most net debt compared to EBITDA?",
  "List all pending deals and their value.",
];

let nextId = 1;

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [backendUp, setBackendUp] = useState(null); // null = not checked yet
  const [tab, setTab] = useState("chat"); // "chat" | "documents"
  const listRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    checkHealth().then(setBackendUp);
  }, []);

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages, loading]);

  async function send(text) {
    const question = text.trim();
    if (!question || loading) return;

    setMessages((m) => [...m, { id: nextId++, role: "user", text: question }]);
    setInput("");
    setLoading(true);
    try {
      const { answer, tools_used, duration_ms } = await askQuestion(question);
      setBackendUp(true);
      setMessages((m) => [
        ...m,
        { id: nextId++, role: "assistant", text: answer, tools: tools_used, durationMs: duration_ms },
      ]);
    } catch (err) {
      setMessages((m) => [...m, { id: nextId++, role: "error", text: err.message }]);
      checkHealth().then(setBackendUp);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function onKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send(input);
    }
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <div>
            <h1>Portfolio Analyst Assistant</h1>
            <p className="subtitle">Ask questions about the infrastructure portfolio</p>
          </div>
          <StatusBadge up={backendUp} />
        </div>
        <nav className="tabs" role="tablist" aria-label="Views">
          {[
            ["chat", "Chat"],
            ["documents", "Documents"],
          ].map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={`tab ${tab === key ? "active" : ""}`}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      {/* Both views stay mounted (hidden, not removed) so switching tabs keeps their state. */}
      <main className="docs" hidden={tab !== "documents"}>
        {backendUp === false && (
          <div className="banner docs-banner">
            The backend isn't responding. Start the FastAPI server on port 8000, then refresh.
          </div>
        )}
        <DocumentsPanel />
      </main>

      <main className="messages" ref={listRef} hidden={tab !== "chat"}>
        <div className="messages-inner">
          {backendUp === false && (
            <div className="banner">
              The backend isn't responding. Start the FastAPI server on port 8000, then ask your question.
            </div>
          )}

          {messages.length === 0 && !loading && (
            <section className="empty">
              <h2>What would you like to know?</h2>
              <p>Try one of these questions, or type your own below.</p>
              <div className="examples">
                {EXAMPLE_QUESTIONS.map((q) => (
                  <button key={q} type="button" className="example" onClick={() => send(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </section>
          )}

          {messages.map((msg) => (
            <Message key={msg.id} message={msg} />
          ))}

          {loading && (
            <div className="message assistant">
              <div className="bubble loading" role="status">
                <span className="dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                Analyzing the portfolio...
              </div>
            </div>
          )}
        </div>
      </main>

      <footer className="composer" hidden={tab !== "chat"}>
        <form
          className="composer-inner"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about companies, sectors, financials or deals..."
            rows={2}
            maxLength={4000}
            aria-label="Your question"
            autoFocus
          />
          <button type="submit" className="send" disabled={loading || !input.trim()}>
            Send
          </button>
        </form>
        <p className="hint">Enter to send · Shift+Enter for a new line</p>
      </footer>
    </div>
  );
}

function Message({ message }) {
  if (message.role === "user") {
    return (
      <div className="message user">
        <div className="bubble">{message.text}</div>
      </div>
    );
  }
  if (message.role === "error") {
    return (
      <div className="message assistant">
        <div className="bubble error" role="alert">
          {message.text}
        </div>
      </div>
    );
  }
  return (
    <div className="message assistant">
      <div className="bubble markdown">
        <Markdown>{message.text}</Markdown>
      </div>
      <div className="meta">
        Tools used: {message.tools.length ? message.tools.join(", ") : "none"} ·{" "}
        {(message.durationMs / 1000).toFixed(1)} s
      </div>
    </div>
  );
}

function StatusBadge({ up }) {
  if (up === null) return null;
  return (
    <span className={`status ${up ? "ok" : "down"}`}>
      <span className="status-dot" aria-hidden="true" />
      {up ? "Connected" : "Backend offline"}
    </span>
  );
}
