import type { Document, DocumentSummary, IngestResponse, Project } from "./types";

const BASE = import.meta.env.DEV ? "http://127.0.0.1:8000" : "";

export const api = {
  async health() {
    const r = await fetch(`${BASE}/api/health`);
    return r.json();
  },
  async listProjects(): Promise<Project[]> {
    const r = await fetch(`${BASE}/api/projects`);
    if (!r.ok) throw new Error(`listProjects: ${r.status}`);
    return r.json();
  },
  async listDocs(project: string): Promise<DocumentSummary[]> {
    const r = await fetch(`${BASE}/api/projects/${encodeURIComponent(project)}/docs`);
    if (!r.ok) throw new Error(`listDocs: ${r.status}`);
    return r.json();
  },
  async getDoc(project: string, id: string): Promise<Document> {
    const r = await fetch(`${BASE}/api/projects/${encodeURIComponent(project)}/docs/${id}`);
    if (!r.ok) throw new Error(`getDoc: ${r.status}`);
    return r.json();
  },
  async ingest(project: string, file: File): Promise<IngestResponse> {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`${BASE}/api/projects/${encodeURIComponent(project)}/ingest`, {
      method: "POST",
      body: form,
    });
    if (!r.ok) throw new Error(`ingest: ${r.status}`);
    return r.json();
  },
  async getJob(jobId: string): Promise<IngestResponse> {
    const r = await fetch(`${BASE}/api/jobs/${encodeURIComponent(jobId)}`);
    if (!r.ok) throw new Error(`getJob: ${r.status}`);
    return r.json();
  },
  audioUrl(project: string, docId: string, sentenceId: string) {
    return `${BASE}/api/projects/${encodeURIComponent(project)}/docs/${docId}/audio/${sentenceId}`;
  },
  imageUrl(project: string, docId: string, name: string) {
    return `${BASE}/api/projects/${encodeURIComponent(project)}/docs/${docId}/image/${name}`;
  },
  pdfUrl(project: string, docId: string) {
    return `${BASE}/api/projects/${encodeURIComponent(project)}/docs/${docId}/source.pdf`;
  },
  markdownUrl(project: string, docId: string) {
    return `${BASE}/api/projects/${encodeURIComponent(project)}/docs/${docId}/markdown`;
  },
  audiobookUrl(project: string, docId: string) {
    return `${BASE}/api/projects/${encodeURIComponent(project)}/docs/${docId}/audiobook.mp3`;
  },
};
