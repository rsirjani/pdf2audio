import { useEffect, useState } from "react";
import { api } from "../api";
import type { Project } from "../types";

function fmtDuration(ms: number): string {
  const totalMin = Math.round(ms / 60000);
  if (totalMin >= 60) {
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    return `${h}h ${m}m`;
  }
  return `${totalMin}m`;
}

interface Props {
  onOpen: (project: string) => void;
}

export default function ProjectList({ onOpen }: Props) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    try {
      setProjects(await api.listProjects());
    } catch (e: any) {
      setError(String(e));
    }
  }

  useEffect(() => {
    reload();
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, []);

  function createProject() {
    const n = newName.trim();
    if (!n) return;
    setNewName("");
    onOpen(n);
  }

  return (
    <div className="library">
      <div className="new-project">
        <input
          type="text"
          placeholder="new project name (e.g. SQL-ambiguity)"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && createProject()}
        />
        <button onClick={createProject} disabled={!newName.trim()}>
          create
        </button>
      </div>

      <h2>projects</h2>
      {error && <div className="empty">⚠ {error}</div>}
      {projects === null ? (
        <div className="empty">loading…</div>
      ) : projects.length === 0 ? (
        <div className="empty">
          no projects yet — create one above, then upload PDFs into it
        </div>
      ) : (
        <div className="doc-list">
          {projects.map((p) => (
            <div key={p.name} className="doc-card" onClick={() => onOpen(p.name)}>
              <div className="doc-card-title">{p.name}</div>
              <div className="doc-card-meta">
                {p.doc_count} {p.doc_count === 1 ? "paper" : "papers"} · {fmtDuration(p.total_duration_ms)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
