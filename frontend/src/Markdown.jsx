import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Claude's answers as Markdown, with GFM tables wrapped so they scroll sideways on small screens.
export default function Markdown({ children }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        table: ({ node, ...props }) => (
          <div className="table-wrap">
            <table {...props} />
          </div>
        ),
        a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
