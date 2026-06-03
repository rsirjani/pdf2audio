import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DocumentSummary } from "../types";

function fmtDuration(ms: number): string {
  const totalSec = Math.round(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

interface Job {
  job_id: string;
  status: string;
  doc_id: string | null;
  filename: string;
}

interface Props {
  project: string;
  onOpen: (docId: string) => void;
}

export default function ProjectDocs({ project, onOpen }: Props) {
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function reload() {
    try {
      setDocs(await api.listDocs(project));
    } catch (e) {
      console.error(e);
    }
  }

  useEffect(() => {
    reload();
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [project]);

  useEffect(() => {
    if (jobs.length === 0) return;
    const interval = setInterval(async () => {
      const updated = await Promise.all(
        jobs.map(async (j) => {
          if (j.status === "done" || j.status === "error") return j;
          try {
            const r = await api.getJob(j.job_id);
            return { ...j, status: r.status, doc_id: r.doc_id };
          } catch {
            return j;
          }
        })
      );
      setJobs(updated);
      if (updated.some((j) => j.status === "done")) reload();
    }, 1500);
    return () => clearInterval(interval);
  }, [jobs]);

  async function handleFiles(files: FileList | File[]) {
    const arr = Array.from(files);
    for (const f of arr) {
      if (!f.name.toLowerCase().endsWith(".pdf")) continue;
      try {
        const r = await api.ingest(project, f);
        setJobs((js) => [
          ...js,
          { job_id: r.job_id, status: r.status, doc_id: r.doc_id, filename: f.name },
        ]);
      } catch (e) {
        console.error(e);
      }
    }
  }

  return (
    <div className="library">
      <div
        className={`uploader ${dragging ? "dragging" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length > 0) handleFiles(e.dataTransfer.files);
        }}
      >
        <div>📄 drop PDFs here or click to upload</div>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          onChange={(e) => {
            if (e.target.files) handleFiles(e.target.files);
          }}
        />
      </div>

      {jobs.length > 0 && (
        <div className="job-list">
          {jobs.map((j) => (
            <div key={j.job_id} className={`job-card status-${j.status}`}>
              <div className="job-name">{j.filename}</div>
              <div className="job-status">
                {j.status === "queued" && "queued…"}
                {j.status === "running" && "parsing + synthesizing…"}
                {j.status === "done" && "✓ ready"}
                {j.status === "error" && "✗ failed"}
              </div>
              {j.status === "done" && j.doc_id && (
                <button onClick={() => onOpen(j.doc_id!)}>open</button>
              )}
            </div>
          ))}
        </div>
      )}

      <h2>papers in {project}</h2>
      {docs.length === 0 ? (
        <div className="empty">no papers yet</div>
      ) : (
        <div className="doc-list">
          {docs.map((d) => (
            <div key={d.id} className="doc-card" onClick={() => onOpen(d.id)}>
              <div className="doc-card-title">{d.title}</div>
              <div className="doc-card-meta">
                {fmtDuration(d.total_duration_ms)} · {d.block_count} blocks · {new Date(d.created_at).toLocaleDateString()}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
