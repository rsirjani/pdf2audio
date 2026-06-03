export type BlockType = "heading" | "paragraph" | "image" | "list_item" | "code" | "equation" | "table" | "caption";

export interface Sentence {
  id: string;
  text: string;
  tts_text: string | null;
  audio: string | null;
  duration_ms: number;
  start_offset_ms: number;
}

export interface Block {
  id: string;
  type: BlockType;
  level: number;
  sentences: Sentence[];
  image_path: string | null;
  caption: string | null;
  raw: string | null;
  latex: string | null;
  table_md: string | null;
  list_marker: string | null;
  pause_at_ms: number | null;
}

export interface Document {
  id: string;
  title: string;
  project: string;
  abstract: string | null;
  authors: string[];
  blocks: Block[];
  total_duration_ms: number;
  voice: string;
  created_at: string;
  source_pdf: string | null;
  markdown_file: string | null;
}

export interface DocumentSummary {
  id: string;
  title: string;
  project: string;
  created_at: string;
  total_duration_ms: number;
  block_count: number;
}

export interface Project {
  name: string;
  doc_count: number;
  total_duration_ms: number;
  created_at: string;
}

export interface IngestResponse {
  job_id: string;
  status: string;
  doc_id: string | null;
  project: string | null;
  message: string | null;
}
