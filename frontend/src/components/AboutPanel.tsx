import { useEffect, useState } from "react";

type Me = {
  email?: string;
  pdfs_today?: number;
  pdfs_per_day_limit?: number;
  storage_bytes?: number;
  max_storage_mb?: number;
};

export default function AboutPanel({ onClose }: { onClose: () => void }) {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    fetch("/api/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  const storageMB = me?.storage_bytes ? (me.storage_bytes / 1024 / 1024).toFixed(1) : "0";
  const storageLimit = me?.max_storage_mb ?? 0;
  const pdfPct = me?.pdfs_per_day_limit
    ? Math.min(100, ((me.pdfs_today ?? 0) / me.pdfs_per_day_limit) * 100)
    : 0;
  const storagePct = storageLimit
    ? Math.min(100, ((me?.storage_bytes ?? 0) / (storageLimit * 1024 * 1024)) * 100)
    : 0;

  const KOFI = (window as any).PDF2AUDIO_KOFI_URL || "https://ko-fi.com/rsirjani";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="close">
          ×
        </button>

        <div className="about-hero">
          <img
            src="/static/ramtin.jpg"
            alt="Ramtin Sirjani"
            className="about-photo"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
          <div>
            <h2 className="about-name">Ramtin Sirjani</h2>
            <div className="about-tag">PhD student, Western University</div>
          </div>
        </div>

        <p className="about-body">
          <strong>pdf2audio</strong> turns academic PDFs into structured reading
          with high-quality follow-along narration. Built and run on a personal GPU
          rig in my home office — every PDF you upload uses real electricity and GPU
          time. If it helps you, a tip keeps the lights on.
        </p>

        <a className="kofi-btn" href={KOFI} target="_blank" rel="noopener noreferrer">
          ☕ Leave a tip on Ko-fi
        </a>

        {me?.email && (
          <div className="usage">
            <div className="usage-row">
              <span>Signed in as</span>
              <span className="usage-value">{me.email}</span>
            </div>
            <div className="usage-row">
              <span>PDFs today</span>
              <span className="usage-value">
                {me.pdfs_today ?? 0} / {me.pdfs_per_day_limit}
              </span>
            </div>
            <div className="usage-bar">
              <div className="usage-bar-fill" style={{ width: `${pdfPct}%` }} />
            </div>
            <div className="usage-row">
              <span>Storage</span>
              <span className="usage-value">
                {storageMB} MB / {storageLimit} MB
              </span>
            </div>
            <div className="usage-bar">
              <div className="usage-bar-fill" style={{ width: `${storagePct}%` }} />
            </div>
            <a className="signout" href="/cdn-cgi/access/logout">
              Sign out
            </a>
          </div>
        )}

        <div className="legal">
          <a href="/static/terms.html" target="_blank" rel="noopener noreferrer">
            Terms
          </a>{" "}
          ·{" "}
          <a href="/static/privacy.html" target="_blank" rel="noopener noreferrer">
            Privacy
          </a>{" "}
          ·{" "}
          <a
            href="https://github.com/RamtinSirjani/pdf2audio"
            target="_blank"
            rel="noopener noreferrer"
          >
            Source on GitHub
          </a>
        </div>
      </div>
    </div>
  );
}
