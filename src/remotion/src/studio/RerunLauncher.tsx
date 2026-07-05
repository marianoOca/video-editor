import React, { useCallback, useEffect, useState } from "react";

// "Re-run pipeline" launcher for the Studio Compositions panel. It has no visible
// trigger of its own: it listens for the `ve-rerun-pipeline` window event dispatched
// by the composition context-menu item (patched into composition-menu-items.js) and
// opens a self-contained modal that re-runs run_all.py from a chosen step for that
// project, via the sidecar's /rerun-pipeline endpoint.
//
// Progress is NOT shown here — after kicking off the run, the modal fires a
// `ve-job-started` window event and closes; NewProjectLauncher (always mounted)
// catches it and routes the run into the shared build store, which shows a progress
// bar directly under the project's row in the left sidebar (and blocks/deselects it
// while building). Mounted once alongside NewProjectLauncher (CompositionSelector.js
// patch). Kept self-contained (its own styles/helpers). UI copy is English.

const SIDECAR = "http://127.0.0.1:9848";
const ACCENT = "#0b84f3";
const MUTED = "#A6A7A9";
const PANEL_BG = "rgb(31,36,40)";

// Pipeline steps as run_all.py orders them (--from N runs N..6). Step 1 re-reads
// input/; steps 5-6 are reel-only (run_all skips them in youtube mode).
const STEPS: { n: number; label: string; note?: string }[] = [
  { n: 1, label: "Normalize video(s)", note: "re-reads input/" },
  { n: 2, label: "Transcribe" },
  { n: 3, label: "Analyze (cut plan)" },
  { n: 4, label: "Cut + Studio preview" },
  { n: 5, label: "Place images", note: "reel only" },
  { n: 6, label: "Motion graphics", note: "reel only" },
];

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.55)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 100000,
  fontFamily: "Arial, Helvetica, sans-serif",
};

const modalStyle: React.CSSProperties = {
  width: 460,
  maxWidth: "92vw",
  maxHeight: "86vh",
  overflowY: "auto",
  background: PANEL_BG,
  border: "1px solid #000",
  borderRadius: 8,
  padding: 20,
  color: "#fff",
  fontSize: 13,
  lineHeight: 1.45,
  boxShadow: "0 12px 48px rgba(0,0,0,0.5)",
};

const btn = (primary: boolean): React.CSSProperties => ({
  padding: "7px 14px",
  background: primary ? ACCENT : "rgba(255,255,255,0.08)",
  color: "#fff",
  border: "none",
  borderRadius: 5,
  cursor: "pointer",
  fontWeight: 600,
});

async function fetchJSON(path: string, init?: RequestInit) {
  const res = await fetch(SIDECAR + path, init);
  const body = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, body };
}

const Modal: React.FC<{ project: string; onClose: () => void }> = ({ project, onClose }) => {
  const [fromStep, setFromStep] = useState(2); // default: --from 2
  const [hasInput, setHasInput] = useState(true);
  const [hyperframesUp, setHyperframesUp] = useState(true);
  const [queueCount, setQueueCount] = useState(0); // jobs running/queued right now
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // On open: step-1 availability + Hyperframes status, and how many jobs are
  // already running/queued (this re-run joins the FIFO queue after them).
  useEffect(() => {
    (async () => {
      const { ok, body } = await fetchJSON("/input-videos");
      if (ok) {
        setHasInput((body.videos || []).length > 0);
        setHyperframesUp(Boolean(body.hyperframesUp));
      }
      const st = await fetchJSON("/pipeline-status");
      if (st.ok && Array.isArray(st.body.jobs)) {
        setQueueCount(
          st.body.jobs.filter((j: any) => j.state === "running" || j.state === "queued").length
        );
      }
    })();
  }, []);

  // Step 1 needs a video in input/; if it's gone, never leave it selected.
  useEffect(() => {
    if (!hasInput && fromStep === 1) setFromStep(2);
  }, [hasInput, fromStep]);

  const rerun = useCallback(async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    const { ok, status, body } = await fetchJSON("/rerun-pipeline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, fromStep }),
    });
    if (!ok) {
      setSubmitting(false);
      setError(
        status === 409
          ? "This project already has a job queued or running — cancel it first (✕ on its row) to re-queue."
          : body.error || `request failed (${status})`
      );
      return;
    }
    // Hand the job off to the sidebar row (NewProjectLauncher listens for this): it
    // queues behind any running/queued jobs and runs in turn. Get out of the way.
    window.dispatchEvent(
      new CustomEvent("ve-job-started", {
        detail: { project: body.project || project, id: body.jobId, state: body.state },
      })
    );
    onClose();
  }, [submitting, project, fromStep, onClose]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    e.stopPropagation(); // Studio binds global shortcuts; keep keystrokes local.
    if (e.key === "Escape" && !submitting) onClose();
  };

  // Steps 5-6 need the Hyperframes producer (reel mode); warn if the run reaches them.
  const willHitMotion = fromStep <= 6 && !hyperframesUp;
  const disabled = submitting;

  return (
    <div style={overlayStyle} onClick={() => !submitting && onClose()}>
      <div style={modalStyle} onClick={(e) => e.stopPropagation()} onKeyDown={onKeyDown}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>
            Re-run pipeline — <span style={{ color: ACCENT }}>{project}</span>
          </div>
          <button
            onClick={() => !submitting && onClose()}
            style={{ ...btn(false), padding: "2px 9px", fontSize: 16 }}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div style={{ color: MUTED, fontSize: 12, margin: "10px 0 4px" }}>
          Start from this step (runs through to the end):
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {STEPS.map((s) => {
            const stepDisabled = s.n === 1 && !hasInput;
            const selected = fromStep === s.n;
            return (
              <label
                key={s.n}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  borderRadius: 5,
                  background: selected ? "rgba(11,132,243,0.14)" : "rgba(255,255,255,0.04)",
                  cursor: stepDisabled ? "not-allowed" : "pointer",
                  opacity: stepDisabled ? 0.45 : 1,
                }}
              >
                <input
                  type="radio"
                  name="rerun-step"
                  checked={selected}
                  disabled={stepDisabled || disabled}
                  onChange={() => setFromStep(s.n)}
                />
                <span style={{ flex: 1 }}>
                  <span style={{ color: MUTED, marginRight: 6 }}>{s.n}.</span>
                  {s.label}
                </span>
                {s.note ? <span style={{ color: MUTED, fontSize: 10 }}>{s.note}</span> : null}
                {stepDisabled ? <span style={{ color: "#ffb454", fontSize: 10 }}>no input/</span> : null}
              </label>
            );
          })}
        </div>

        {willHitMotion ? (
          <div
            style={{
              marginTop: 12,
              padding: "8px 10px",
              borderRadius: 5,
              background: "rgba(255,180,84,0.12)",
              border: "1px solid rgba(255,180,84,0.4)",
              color: "#ffb454",
              fontSize: 12,
            }}
          >
            Motion graphics (reel step 6) need the Hyperframes producer on :9847 — run{" "}
            <code>npx hyperframes-producer</code>, or it'll fail at step 6 (steps before
            it still save). youtube mode skips steps 5-6.
          </div>
        ) : null}

        {queueCount > 0 ? (
          <div style={{ marginTop: 14, color: MUTED, fontSize: 12 }}>
            {queueCount} job{queueCount > 1 ? "s" : ""} already running/queued — this re-run
            joins the queue and starts when they finish (progress shows under each
            project's row).
          </div>
        ) : null}
        {error ? (
          <div style={{ marginTop: 12, color: "#ff6b6b", fontSize: 12 }}>{error}</div>
        ) : null}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
          <button onClick={() => !submitting && onClose()} style={btn(false)} disabled={submitting}>
            Cancel
          </button>
          <button onClick={rerun} style={{ ...btn(true), opacity: disabled ? 0.5 : 1 }} disabled={disabled}>
            {submitting ? "Queuing…" : queueCount > 0 ? `Add to queue (step ${fromStep})` : `Re-run from step ${fromStep}`}
          </button>
        </div>
      </div>
    </div>
  );
};

export const RerunLauncher: React.FC = () => {
  const [project, setProject] = useState<string | null>(null);
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail && typeof detail.project === "string") setProject(detail.project);
    };
    window.addEventListener("ve-rerun-pipeline", handler);
    return () => window.removeEventListener("ve-rerun-pipeline", handler);
  }, []);
  if (!project) return null;
  return <Modal project={project} onClose={() => setProject(null)} />;
};
