import { useState } from "react";
import AboutPanel from "./components/AboutPanel";
import ProjectList from "./components/ProjectList";
import ProjectDocs from "./components/ProjectDocs";
import Reader from "./components/Reader";

type View =
  | { kind: "projects" }
  | { kind: "docs"; project: string }
  | { kind: "reader"; project: string; docId: string };

export default function App() {
  const [view, setView] = useState<View>({ kind: "projects" });
  const [showAbout, setShowAbout] = useState(false);

  let body;
  let crumb: string | null = null;
  let backTo: View | null = null;

  if (view.kind === "projects") {
    body = <ProjectList onOpen={(project) => setView({ kind: "docs", project })} />;
  } else if (view.kind === "docs") {
    crumb = view.project;
    backTo = { kind: "projects" };
    body = (
      <ProjectDocs
        project={view.project}
        onOpen={(docId) => setView({ kind: "reader", project: view.project, docId })}
      />
    );
  } else {
    crumb = view.project;
    backTo = { kind: "docs", project: view.project };
    body = <Reader project={view.project} docId={view.docId} />;
  }

  return (
    <div className="app">
      <div className="topbar">
        <div className="topbar-left" onClick={() => setView({ kind: "projects" })}>
          <h1>📖 pdf2audio</h1>
          {crumb && <span className="crumb">/ {crumb}</span>}
        </div>
        <div className="topbar-right">
          {backTo && (
            <button className="back-btn" onClick={() => setView(backTo!)}>
              ← back
            </button>
          )}
          <button className="about-btn" onClick={() => setShowAbout(true)} title="About + Donate">
            ♥
          </button>
        </div>
      </div>
      {body}
      {showAbout && <AboutPanel onClose={() => setShowAbout(false)} />}
    </div>
  );
}
