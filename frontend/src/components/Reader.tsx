import { useEffect, useMemo, useRef, useState } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";
import { api } from "../api";
import type { Block, Document, Sentence } from "../types";

function fixDoubleSuperscripts(s: string): string {
  // KaTeX rejects "a^X^Y"; Marker's math OCR emits this often. Wrap as "a^{X^Y}".
  let prev: string;
  do {
    prev = s;
    s = s.replace(
      /\^(\\[A-Za-z]+|\{[^{}]*\}|[^\s{}^_])\^(\\[A-Za-z]+|\{[^{}]*\}|[^\s{}^_])/g,
      "^{$1^$2}"
    );
  } while (s !== prev);
  // Same problem with subscripts: a_X_Y → a_{X_Y}
  do {
    prev = s;
    s = s.replace(
      /_(\\[A-Za-z]+|\{[^{}]*\}|[^\s{}^_])_(\\[A-Za-z]+|\{[^{}]*\}|[^\s{}^_])/g,
      "_{$1_$2}"
    );
  } while (s !== prev);
  return s;
}

function InlineMathText({ text }: { text: string }) {
  // Split sentence on $...$ regions; render math via KaTeX, prose as plain text
  const parts = useMemo(() => {
    const out: { kind: "text" | "math"; value: string }[] = [];
    const re = /\$([^$\n]{1,200})\$/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) out.push({ kind: "text", value: text.slice(last, m.index) });
      out.push({ kind: "math", value: m[1] });
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ kind: "text", value: text.slice(last) });
    return out;
  }, [text]);
  return (
    <>
      {parts.map((p, i) =>
        p.kind === "math" ? <InlineMath key={i} latex={p.value} /> : <span key={i}>{p.value}</span>
      )}
    </>
  );
}

function InlineMath({ latex }: { latex: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!ref.current) return;
    setFailed(false);
    try {
      katex.render(fixDoubleSuperscripts(latex), ref.current, {
        displayMode: false,
        throwOnError: true,
        output: "html",
        strict: false,
      });
    } catch {
      setFailed(true);
      if (ref.current) ref.current.innerHTML = "";
    }
  }, [latex]);
  if (failed) {
    return <span className="inline-math-failed" title={latex}>[…]</span>;
  }
  return <span className="inline-math" ref={ref} />;
}

function EquationBlock({ latex }: { latex: string }) {
  const mathRef = useRef<HTMLDivElement>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!mathRef.current) return;
    // Pull out \tag{N} so we can render it as a separate, non-scrolling sibling
    let tagText: string | null = null;
    const stripped = latex.replace(/\\tag\{([^}]*)\}/g, (_m, t) => {
      tagText = `(${t})`;
      return "";
    });
    setTag(tagText);
    setFailed(false);
    try {
      // throwOnError: true so we can catch garbage LaTeX (e.g. Marker OCR errors)
      // and fall back to a placeholder instead of dumping raw \jeft\unbrace etc.
      katex.render(fixDoubleSuperscripts(stripped), mathRef.current, {
        displayMode: true,
        throwOnError: true,
        output: "html",
        strict: false,
      });
    } catch {
      setFailed(true);
      if (mathRef.current) mathRef.current.innerHTML = "";
    }
  }, [latex]);
  return (
    <div className={`equation-block ${failed ? "failed" : ""}`}>
      {failed ? (
        <div className="equation-fallback">⚠ equation couldn't render (Marker OCR artifact)</div>
      ) : (
        <div className="equation-math" ref={mathRef} />
      )}
      {tag && !failed && <div className="equation-tag">{tag}</div>}
    </div>
  );
}

function TableBlock({ markdown }: { markdown: string }) {
  const rows = useMemo(() => parseMarkdownTable(markdown), [markdown]);
  if (rows.length === 0) {
    return <pre className="table-fallback">{markdown}</pre>;
  }
  const [header, ...body] = rows;
  return (
    <div className="table-wrap">
      <table className="md-table">
        <thead>
          <tr>{header.map((c, i) => <th key={i}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri}>{r.map((c, ci) => <td key={ci}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function parseMarkdownTable(md: string): string[][] {
  const lines = md.split("\n").map((l) => l.trim()).filter((l) => l.startsWith("|"));
  const rows: string[][] = [];
  for (const line of lines) {
    // skip separator row like |---|---|
    if (/^\|[\s\-:|]+\|?$/.test(line)) continue;
    const cells = line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    rows.push(cells);
  }
  return rows;
}

interface Props {
  project: string;
  docId: string;
}

interface SentenceRef {
  sentence: Sentence;
  blockIdx: number;
}

async function precacheDoc(
  doc: Document,
  project: string,
  docId: string,
  onProgress: (done: number, total: number) => void
): Promise<{ done: number; failed: number; total: number }> {
  // Cache directly via Cache API — works even if SW isn't controlling the page yet.
  const urls: string[] = [
    `/api/projects/${encodeURIComponent(project)}/docs/${docId}`,
    api.markdownUrl(project, docId),
    api.audiobookUrl(project, docId),
  ];
  for (const b of doc.blocks) {
    if (b.type === "image" && b.image_path) {
      urls.push(api.imageUrl(project, docId, b.image_path));
    }
  }

  const cache = await caches.open("docs-v5");
  let done = 0;
  let failed = 0;
  for (const u of urls) {
    try {
      const res = await fetch(u);
      if (res.ok) {
        await cache.put(u, res.clone());
        done++;
      } else {
        failed++;
      }
    } catch {
      failed++;
    }
    onProgress(done + failed, urls.length);
  }
  return { done, failed, total: urls.length };
}

function fmtTime(sec: number): string {
  const total = Math.max(0, Math.round(sec));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function Reader({ project, docId }: Props) {
  const [doc, setDoc] = useState<Document | null>(null);
  const [activeIdx, setActiveIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [offlineStatus, setOfflineStatus] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const activeElRef = useRef<HTMLSpanElement | null>(null);

  async function saveOffline() {
    if (!doc) return;
    setOfflineStatus("starting…");
    const result = await precacheDoc(doc, project, docId, (done, total) => {
      setOfflineStatus(`${done}/${total}`);
    });
    if (result.failed === 0) {
      setOfflineStatus(`✓ saved (${result.done} files)`);
    } else {
      setOfflineStatus(`saved ${result.done}, failed ${result.failed}`);
    }
    setTimeout(() => setOfflineStatus(null), 5000);
  }

  useEffect(() => {
    api.getDoc(project, docId).then(setDoc).catch(console.error);
  }, [project, docId]);

  const sentences: SentenceRef[] = useMemo(() => {
    if (!doc) return [];
    const out: SentenceRef[] = [];
    doc.blocks.forEach((b, bi) => {
      b.sentences.forEach((s) => {
        if (s.audio) out.push({ sentence: s, blockIdx: bi });
      });
    });
    return out;
  }, [doc]);

  const totalDuration = doc ? doc.total_duration_ms / 1000 : 0;
  const activeSent = sentences[activeIdx]?.sentence;

  // Equation/table pause points, sorted by time, with a label for display
  const pausePoints = useMemo(() => {
    if (!doc) return [] as { atSec: number; label: string; blockId: string }[];
    return doc.blocks
      .filter((b) => (b.type === "equation" || b.type === "table") && b.pause_at_ms != null)
      .map((b) => ({
        atSec: (b.pause_at_ms as number) / 1000,
        label: b.type === "equation" ? "equation" : "table",
        blockId: b.id,
      }))
      .sort((a, b) => a.atSec - b.atSec);
  }, [doc]);

  const lastTimeRef = useRef(0);
  const skippedRef = useRef<Set<string>>(new Set());
  const [pauseInfo, setPauseInfo] = useState<{ label: string; blockId: string } | null>(null);

  // Set the MP3 source once per doc — single stream, no per-sentence fetch.
  useEffect(() => {
    if (!doc || !audioRef.current) return;
    audioRef.current.src = api.audiobookUrl(project, docId);
  }, [doc, project, docId]);

  useEffect(() => {
    if (!audioRef.current) return;
    if (playing) audioRef.current.play().catch(() => setPlaying(false));
    else audioRef.current.pause();
  }, [playing]);

  useEffect(() => {
    if (activeElRef.current) {
      activeElRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeIdx]);

  function findActiveIdx(timeSec: number): number {
    const timeMs = timeSec * 1000;
    if (sentences.length === 0) return 0;
    let lo = 0;
    let hi = sentences.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (sentences[mid].sentence.start_offset_ms <= timeMs) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  function seekTo(timeSec: number, play: boolean = true) {
    if (!audioRef.current) return;
    audioRef.current.currentTime = Math.max(0, Math.min(timeSec, totalDuration));
    setActiveIdx(findActiveIdx(timeSec));
    if (play) setPlaying(true);
  }

  function jumpToSentenceIdx(idx: number, play: boolean = true) {
    if (idx < 0 || idx >= sentences.length) return;
    seekTo(sentences[idx].sentence.start_offset_ms / 1000, play);
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement) return;
      if (e.code === "Space") {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.code === "KeyJ" || e.code === "ArrowRight") {
        jumpToSentenceIdx(activeIdx + 1, playing);
      } else if (e.code === "KeyK" || e.code === "ArrowLeft") {
        jumpToSentenceIdx(activeIdx - 1, playing);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeIdx, sentences.length, playing]);

  function handleEnded() {
    setPlaying(false);
  }

  function handleTimeUpdate() {
    if (!audioRef.current) return;
    const t = audioRef.current.currentTime;
    const last = lastTimeRef.current;
    lastTimeRef.current = t;
    setCurrentTime(t);
    const newIdx = findActiveIdx(t);
    if (newIdx !== activeIdx) setActiveIdx(newIdx);

    // Auto-pause when crossing an equation/table that we haven't already skipped
    for (const pp of pausePoints) {
      if (skippedRef.current.has(pp.blockId)) continue;
      if (last < pp.atSec && t >= pp.atSec) {
        audioRef.current.pause();
        setPlaying(false);
        setPauseInfo({ label: pp.label, blockId: pp.blockId });
        break;
      }
    }
  }

  function resumeFromPause() {
    if (pauseInfo) {
      skippedRef.current.add(pauseInfo.blockId);
      setPauseInfo(null);
    }
    setPlaying(true);
  }

  function jumpToSentence(sentenceId: string) {
    const idx = sentences.findIndex((s) => s.sentence.id === sentenceId);
    if (idx >= 0) jumpToSentenceIdx(idx, true);
  }

  function seekByFraction(frac: number) {
    if (!doc) return;
    seekTo(frac * totalDuration, playing);
  }

  if (!doc) return <div className="empty">loading…</div>;

  return (
    <>
      <div className="reader">
        <h1 className="doc-title">{doc.title}</h1>

        <div className="download-bar">
          <button className="dl-btn save-btn" onClick={saveOffline}>
            💾 Save offline
          </button>
          {offlineStatus && <span className="offline-status">{offlineStatus}</span>}
        </div>

        {doc.abstract && <div className="abstract">{doc.abstract}</div>}

        {doc.blocks.map((block) => (
          <BlockView
            key={block.id}
            block={block}
            project={project}
            docId={docId}
            activeSentenceId={activeSent?.id ?? null}
            onSentenceClick={jumpToSentence}
            activeElRef={activeElRef}
          />
        ))}
      </div>

      <div className="player">
        <div className="controls">
          <button onClick={() => jumpToSentenceIdx(activeIdx - 1, playing)} title="prev (k / ←)">⏮</button>
          <button onClick={() => setPlaying((p) => !p)} title="play/pause (space)">
            {playing ? "⏸" : "▶"}
          </button>
          <button onClick={() => jumpToSentenceIdx(activeIdx + 1, playing)} title="next (j / →)">⏭</button>
        </div>
        <div className="progress">
          <div
            className="progress-bar"
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              seekByFraction((e.clientX - rect.left) / rect.width);
            }}
          >
            <div className="progress-fill" style={{ width: `${(currentTime / totalDuration) * 100}%` }} />
          </div>
          <div className="time">
            {fmtTime(currentTime)} / {fmtTime(totalDuration)}
          </div>
        </div>
      </div>

      <audio ref={audioRef} onEnded={handleEnded} onTimeUpdate={handleTimeUpdate} preload="auto" />

      {pauseInfo && (
        <div className="pause-overlay">
          <div className="pause-card">
            <span className="pause-label">⏸ {pauseInfo.label}</span>
            <button className="dl-btn" onClick={resumeFromPause}>▶ resume</button>
          </div>
        </div>
      )}
    </>
  );
}

interface BlockViewProps {
  block: Block;
  project: string;
  docId: string;
  activeSentenceId: string | null;
  onSentenceClick: (id: string) => void;
  activeElRef: React.MutableRefObject<HTMLSpanElement | null>;
}

function BlockView({ block, project, docId, activeSentenceId, onSentenceClick, activeElRef }: BlockViewProps) {
  if (block.type === "image" && block.image_path) {
    return (
      <figure>
        <img src={api.imageUrl(project, docId, block.image_path)} alt={block.caption ?? ""} />
        {block.caption && <figcaption>{block.caption}</figcaption>}
      </figure>
    );
  }

  if (block.type === "equation" && block.latex) {
    return <EquationBlock latex={block.latex} />;
  }

  if (block.type === "table" && block.table_md) {
    return <TableBlock markdown={block.table_md} />;
  }

  if (block.type === "list_item") {
    return (
      <div className="list-item">
        <span className="list-marker">{block.list_marker ?? "•"}</span>
        <span className="list-content">
          {block.sentences.map((s, i) => {
            const isActive = s.id === activeSentenceId;
            return (
              <span key={s.id}>
                {i > 0 && " "}
                <span
                  ref={isActive ? activeElRef : null}
                  className={`sentence ${isActive ? "active" : ""}`}
                  onClick={() => onSentenceClick(s.id)}
                >
                  <InlineMathText text={s.text} />
                </span>
              </span>
            );
          })}
        </span>
      </div>
    );
  }

  if (block.type === "heading") {
    const level = Math.min(block.level, 4);
    const text = block.sentences[0]?.text ?? block.raw ?? "";
    const isActive = block.sentences[0]?.id === activeSentenceId;
    const inner = (
      <span
        ref={isActive ? activeElRef : null}
        className={`sentence ${isActive ? "active" : ""}`}
        onClick={() => block.sentences[0] && onSentenceClick(block.sentences[0].id)}
      >
        {text}
      </span>
    );
    if (level === 1) return <h2 className="heading-1">{inner}</h2>;
    if (level === 2) return <h3 className="heading-2">{inner}</h3>;
    if (level === 3) return <h4 className="heading-3">{inner}</h4>;
    return <h5 className="heading-4">{inner}</h5>;
  }

  return (
    <p>
      {block.sentences.map((s, i) => {
        const isActive = s.id === activeSentenceId;
        return (
          <span key={s.id}>
            {i > 0 && " "}
            <span
              ref={isActive ? activeElRef : null}
              className={`sentence ${isActive ? "active" : ""}`}
              onClick={() => onSentenceClick(s.id)}
            >
              <InlineMathText text={s.text} />
            </span>
          </span>
        );
      })}
    </p>
  );
}
