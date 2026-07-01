import React, { useCallback, useEffect, useRef, useState } from "react";
import { Internals } from "remotion";

// "+ New project" launcher for the Studio Compositions panel (left sidebar).
// A button + a self-contained modal (Studio's ModalsContext is not public API, so
// we render our own position:fixed overlay) that stages videos into input/ three
// ways — drop (XHR upload), paste a local path (referenced in place, no copy), or
// pick from input/ — then kicks off run_all.py 1-6 as a background job via the
// sidecar.
//
// The pipeline run is NON-BLOCKING and its progress lives IN THE COMPOSITION LIST,
// not in the modal: clicking Create closes the modal immediately and the build is
// tracked in a module-level store (so it outlives the modal). The build shows up
// in the left sidebar as:
//   • a synthetic row (project name + progress bar) INSIDE the "Projects"
//     folder, at the same indent as real projects, while the real composition
//     snapshot doesn't exist yet (steps 1-3 of a brand-new project), then
//   • a progress bar rendered directly under the real composition row once step 4
//     writes the snapshot and HMR adds it.
// While building, the project is hard-blocked from selection (and deselected if it
// was the active composition) so you never view a half-written composition.
//
// Wiring: this file is injected via patch-package into TWO compiled Studio files —
//   CompositionSelector.js     → mounts <BuildSelectGuard/> + a fallback
//                                 <SyntheticBuildRow fallback/> (used only when no
//                                 Projects folder exists yet)
//   CompositionSelectorItem.js → blocks onClick, renders <CompositionBuildBar/> under
//                                 the real row, and <SyntheticBuildRow/> inside the
//                                 "Projects" folder body
// Those patched files import this module across the node_modules boundary, so all
// the logic stays here (project-owned, editable without re-patching). UI copy is
// English to match Studio's native chrome.

const SIDECAR = "http://127.0.0.1:9848";

// Mirror config.sanitize_project_name so the name field previews the real id.
const sanitizeName = (stem: string): string =>
  stem
    .replace(/[^a-zA-Z0-9-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "") || "default";

const stemOf = (filename: string): string => filename.replace(/\.[^.]+$/, "");

type InputVideo = { name: string; sizeMB: number };
type External = { name: string; path: string };

// A selectable row: an input/ video (send its bare name) or an external abs path.
type Item = {
  key: string;
  label: string;
  sub: string;
  send: string;
  external: boolean;
};

const PANEL_BG = "rgb(31,36,40)";
const ACCENT = "#0b84f3";
const MUTED = "#A6A7A9";

const triggerStyle: React.CSSProperties = {
  margin: "0 12px 8px",
  padding: "5px 10px",
  background: ACCENT,
  color: "#fff",
  border: "none",
  borderRadius: 5,
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  width: "calc(100% - 24px)",
  appearance: "none",
};

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
  width: 540,
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

const sectionLabel: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: MUTED,
  margin: "16px 0 6px",
};

const fieldStyle: React.CSSProperties = {
  background: "rgba(255,255,255,0.06)",
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: 5,
  color: "#fff",
  padding: "6px 8px",
  outline: "none",
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
  // Never throw: the sidecar may be down (e.g. `npm run dev:studio`), and this runs
  // at Studio load via initOnce() — an unhandled reject would pop the error overlay.
  try {
    const res = await fetch(SIDECAR + path, init);
    const body = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, body };
  } catch {
    return { ok: false, status: 0, body: {} as any };
  }
}

// XHR (not fetch) so we get a real upload progress %. Streams the File straight
// from disk — multi-GB files don't load into the tab's heap.
function uploadFile(file: File, onProgress: (pct: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${SIDECAR}/upload-video?filename=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText).name as string);
        } catch {
          reject(new Error("bad upload response"));
        }
      } else {
        let msg = `upload failed (${xhr.status})`;
        try {
          msg = JSON.parse(xhr.responseText).error || msg;
        } catch {
          /* keep default */
        }
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error("upload failed (network)"));
    xhr.send(file);
  });
}

/* ────────────────────────────────────────────────────────────────────────────
 * Build store — a module-level singleton tracking the one in-flight pipeline run.
 * NewProjectLauncher writes it (start) and polls the sidecar; the patched Studio
 * components subscribe to it. Lives outside React so it survives the modal closing
 * and component remounts.
 * ──────────────────────────────────────────────────────────────────────────── */

type BuildState = "running" | "done" | "error";
type Build = {
  project: string;
  state: BuildState;
  step: number;
  total: number;
  label: string;
  error: string | null;
};

let _build: Build | null = null;
const _subs = new Set<() => void>();
let _poll: ReturnType<typeof setInterval> | null = null;
let _inited = false;

const _emit = () => _subs.forEach((f) => f());
const _set = (b: Build | null) => {
  _build = b;
  _emit();
};

const _stopPoll = () => {
  if (_poll) {
    clearInterval(_poll);
    _poll = null;
  }
};

const _fromJob = (j: any): Build => ({
  project: j.project,
  state: j.state,
  step: j.step,
  total: j.total,
  label: j.label || "Working",
  error: j.error ?? null,
});

const _beginPoll = () => {
  _stopPoll();
  _poll = setInterval(async () => {
    const { ok, body } = await fetchJSON("/pipeline-status");
    if (!ok || !body.job) return;
    const j = body.job;
    _set(_fromJob(j));
    if (j.state === "done") {
      _stopPoll();
      // Brief 100% beat, then hand off to the now-real (clickable) composition row.
      setTimeout(() => {
        if (_build && _build.project === j.project) _set(null);
      }, 800);
    } else if (j.state === "error") {
      _stopPoll();
    }
  }, 1000);
};

export const buildStore = {
  start(project: string) {
    _set({ project, state: "running", step: 0, total: 6, label: "Starting", error: null });
    _beginPoll();
  },
  dismiss() {
    _stopPoll();
    _set(null);
  },
  subscribe(cb: () => void) {
    _subs.add(cb);
    return () => {
      _subs.delete(cb);
    };
  },
  get(): Build | null {
    return _build;
  },
  // Re-attach to a run already in flight (e.g. after a page reload mid-build).
  initOnce() {
    if (_inited) return;
    _inited = true;
    (async () => {
      const { ok, body } = await fetchJSON("/pipeline-status");
      if (ok && body.job && body.job.state === "running") {
        _set(_fromJob(body.job));
        _beginPoll();
      }
    })();
  },
};

// A project is blocked from selection only while actively running (clickable again
// once done/error). Plain function so the patched onClick can call it at click time.
export const isBuildBlocked = (compositionId: string): boolean =>
  _build !== null && _build.state === "running" && _build.project === compositionId;

const useBuild = (): Build | null =>
  React.useSyncExternalStore(buildStore.subscribe, buildStore.get, buildStore.get);

/* ──────────────────────────── shared visuals ─────────────────────────────── */

const ProgressBar: React.FC<{ build: Build }> = ({ build }) => {
  const pct = build.state === "done" ? 100 : build.total ? Math.round((build.step / build.total) * 100) : 0;
  return (
    <div style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.12)", overflow: "hidden" }}>
      <div
        style={{
          width: `${pct}%`,
          height: "100%",
          borderRadius: 2,
          background: build.state === "error" ? "#ff6b6b" : ACCENT,
          transition: "width 300ms",
        }}
      />
    </div>
  );
};

const buildCaption = (build: Build): string =>
  build.state === "done" ? "Ready" : `${build.label} · ${build.step}/${build.total}`;

/* ───────────── pieces injected into the patched Studio components ──────────── */

// Rendered under the matching REAL composition row (patched into CompositionSelectorItem.js).
export const CompositionBuildBar: React.FC<{ compositionId: string; level: number }> = ({ compositionId, level }) => {
  const build = useBuild();
  if (!build || build.project !== compositionId) return null;
  // Align under the row's name (paddingLeft 12 + level*8 + icon 18 + spacing 8).
  const padLeft = 38 + level * 8;
  return (
    <div style={{ padding: `2px 12px 7px ${padLeft}px`, background: PANEL_BG, fontFamily: "Arial, Helvetica, sans-serif" }}>
      {build.state === "error" ? (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: "#ff6b6b", fontSize: 10, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {build.error || "failed"}
          </span>
          <button onClick={() => buildStore.dismiss()} style={{ ...btn(false), padding: "0 6px", fontSize: 11 }} aria-label="Dismiss">
            ✕
          </button>
        </div>
      ) : (
        <>
          <ProgressBar build={build} />
          <div style={{ fontSize: 9, color: MUTED, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {buildCaption(build)}
          </div>
        </>
      )}
    </div>
  );
};

const PROJECTS_FOLDER = "Projects";

// Film icon matching Studio's native composition-row icon (icons/video.js),
// inlined so the synthetic row reads as a real project row without importing
// Studio internals across the node_modules boundary.
const FilmIcon: React.FC<{ color: string }> = ({ color }) => (
  <svg width={18} height={18} viewBox="0 0 512 512" style={{ flexShrink: 0 }}>
    <path
      fill={color}
      d="M448 32H64C28.65 32 0 60.65 0 96v320c0 35.35 28.65 64 64 64h384c35.35 0 64-28.65 64-64V96C512 60.65 483.3 32 448 32zM384 64v176H128V64H384zM32 96c0-17.64 14.36-32 32-32h32v80H32V96zM32 176h64v64H32V176zM32 272h64v64H32V272zM64 448c-17.64 0-32-14.36-32-32v-48h64V448H64zM128 448V272h256V448H128zM480 416c0 17.64-14.36 32-32 32h-32v-80h64V416zM480 336h-64v-64h64V336zM480 240h-64v-64h64V240zM480 144h-64V64h32c17.64 0 32 14.36 32 32V144z"
    />
  </svg>
);

// Stand-in row shown in the list while the real composition snapshot doesn't exist
// yet (steps 1-3 of a brand-new project). Hidden the moment the real row appears.
// It mirrors a native CompositionSelectorItem row — same 32px height, indent, film
// icon, and 13px font — with the progress bar rendered directly underneath, exactly
// like CompositionBuildBar does for an existing project's re-run. So a building new
// project looks like a real project row (just not yet clickable), instead of an
// oversized off-style card. The patch mounts it twice:
//   • inside the Projects folder body (CompositionSelectorItem.js, level = folder+1)
//     — the normal home, used whenever a Projects folder exists.
//   • at the top of the list as a `fallback` (CompositionSelector.js) — only used
//     when there is NO Projects folder yet (the very first project ever), so the row
//     never vanishes. The two never both render: fallback bails if the folder exists.
export const SyntheticBuildRow: React.FC<{ level?: number; fallback?: boolean }> = ({
  level = 1,
  fallback = false,
}) => {
  const build = useBuild();
  const cm = React.useContext((Internals as any).CompositionManager) as any;
  if (!build) return null;
  const exists = (cm?.compositions || []).some((c: any) => c.id === build.project);
  if (exists) return null; // the real row carries the bar from here on
  // The Projects-folder copy owns this row; the top-level fallback yields to it.
  if (fallback && (cm?.folders || []).some((f: any) => f.name === PROJECTS_FOLDER)) return null;
  const error = build.state === "error";
  // Row indent matches a native row (itemStyle paddingLeft = 12 + level*8); the bar
  // sits under the name (+ icon 18 + gap 8), same formula as CompositionBuildBar.
  const rowPadLeft = 12 + level * 8;
  const barPadLeft = 38 + level * 8;
  return (
    <div style={{ fontFamily: "Arial, Helvetica, sans-serif", cursor: "default" }}>
      {/* fake composition row — mirrors itemStyle in CompositionSelectorItem.js */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          height: 32,
          boxSizing: "border-box",
          paddingLeft: rowPadLeft,
          paddingRight: 10,
          paddingTop: 6,
          paddingBottom: 6,
          marginBottom: 1,
          background: PANEL_BG,
        }}
      >
        <FilmIcon color={MUTED} />
        <span
          style={{
            marginLeft: 8,
            flex: 1,
            fontSize: 13,
            color: MUTED,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {build.project}
        </span>
        {error ? (
          <button onClick={() => buildStore.dismiss()} style={{ ...btn(false), padding: "0 6px", fontSize: 11 }} aria-label="Dismiss">
            ✕
          </button>
        ) : null}
      </div>
      {/* progress bar under the name — identical layout to CompositionBuildBar */}
      <div style={{ padding: `2px 12px 7px ${barPadLeft}px`, background: PANEL_BG }}>
        {error ? (
          <div style={{ color: "#ff6b6b", fontSize: 10, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {build.error || "failed"}
          </div>
        ) : (
          <>
            <ProgressBar build={build} />
            <div style={{ fontSize: 9, color: MUTED, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {buildCaption(build)}
            </div>
          </>
        )}
      </div>
    </div>
  );
};

// No-op (kept mounted because the CompositionSelector.js patch references it).
//
// This used to deselect the building project (setCanvasContent(null)) so you
// couldn't view a composition mid-rewrite. But that FOUGHT Studio's
// InitialCompositionLoader, which re-selects the composition named in the URL the
// instant canvasContent goes null (the deselect never cleared the URL). The two
// effects re-triggered each other every render: an infinite deselect↔reselect loop
// (the "tilts like crazy" flicker), which also opened a window where the comp was
// selected while transiently absent from the require.context list →
// "Composition <id> not found" + the error overlay's source-map fetch failing.
//
// A re-run doesn't need the deselect at all: videoVersion cache-busts the <Video>,
// so the old cut stays on screen until the new snapshot lands and HMR swaps it in.
// (A brand-new project isn't a registered composition yet, so there was never
// anything to deselect there either — this branch only ever fired on re-runs.)
// Selection is still gated by isBuildBlocked() in the patched onClick.
export const BuildSelectGuard: React.FC = () => null;

/* ──────────────────────────────── the modal ──────────────────────────────── */

// Uploads still block the modal (they stage files before Create); the pipeline run
// does not. So the modal only tracks the staging phase.
type Phase = "idle" | "uploading";

const Modal: React.FC<{
  onClose: () => void;
  onStarted: (project: string) => void;
  jobRunning: boolean;
}> = ({ onClose, onStarted, jobRunning }) => {
  const [videos, setVideos] = useState<InputVideo[]>([]);
  const [projects, setProjects] = useState<string[]>([]);
  const [externals, setExternals] = useState<External[]>([]);
  const [hyperframesUp, setHyperframesUp] = useState(true);
  const [checked, setChecked] = useState<Set<string>>(new Set());

  const [projectName, setProjectName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [overwrite, setOverwrite] = useState(false);

  const [pathInput, setPathInput] = useState("");
  const [pathError, setPathError] = useState<string | null>(null);

  const [phase, setPhase] = useState<Phase>("idle");
  const [uploadMsg, setUploadMsg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const items: Item[] = [
    ...videos.map((v) => ({
      key: `in:${v.name}`,
      label: v.name,
      sub: `${v.sizeMB} MB`,
      send: v.name,
      external: false,
    })),
    ...externals.map((e) => ({
      key: `ext:${e.path}`,
      label: e.name,
      sub: e.path,
      send: e.path,
      external: true,
    })),
  ];
  const checkedItems = items.filter((i) => checked.has(i.key));
  const collision = projectName !== "" && projects.includes(projectName);

  const loadList = useCallback(async () => {
    const { ok, body } = await fetchJSON("/input-videos");
    if (ok) {
      setVideos(body.videos || []);
      setProjects(body.projects || []);
      setHyperframesUp(Boolean(body.hyperframesUp));
    }
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  // Prevent an errant drop anywhere in the window from navigating away.
  useEffect(() => {
    const prevent = (e: DragEvent) => e.preventDefault();
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);

  // Auto-derive the project name from the first checked item until the user types.
  useEffect(() => {
    if (nameEdited) return;
    const first = checkedItems[0];
    setProjectName(first ? sanitizeName(stemOf(first.label)) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checked, videos, externals, nameEdited]);

  const toggle = (key: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const busy = phase === "uploading";

  const runUploads = useCallback(
    async (raw: File[]) => {
      if (busy) return;
      const files = raw.filter((f) => /\.(mp4|mov)$/i.test(f.name));
      if (!files.length) return;
      setPhase("uploading");
      setError(null);
      try {
        const uploaded: string[] = [];
        for (let i = 0; i < files.length; i++) {
          const f = files[i];
          const label = (extra: string) =>
            setUploadMsg(`Uploading ${f.name} (${i + 1}/${files.length})${extra}`);
          label("…");
          uploaded.push(await uploadFile(f, (pct) => label(` — ${pct}%`)));
        }
        await loadList();
        setChecked((prev) => {
          const next = new Set(prev);
          uploaded.forEach((n) => next.add(`in:${n}`));
          return next;
        });
        setPhase("idle");
        setUploadMsg("");
      } catch (err) {
        setPhase("idle");
        setUploadMsg("");
        setError((err as Error).message);
      }
    },
    [busy, loadList]
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      runUploads(Array.from(e.dataTransfer.files));
    },
    [runUploads]
  );

  const addPath = useCallback(async () => {
    const path = pathInput.trim();
    if (!path) return;
    setPathError(null);
    const { ok, body } = await fetchJSON("/import-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!ok) {
      setPathError(body.error || "could not add path");
      return;
    }
    setExternals((prev) =>
      prev.some((e) => e.path === body.path) ? prev : [...prev, { name: body.name, path: body.path }]
    );
    setChecked((prev) => new Set(prev).add(`ext:${body.path}`));
    setPathInput("");
  }, [pathInput]);

  const create = useCallback(async () => {
    if (!checkedItems.length || !projectName || (collision && !overwrite) || jobRunning) return;
    setError(null);
    const inputs = checkedItems.map((i) => i.send);
    const { ok, status, body } = await fetchJSON("/run-pipeline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs, project: projectName, overwrite }),
    });
    if (!ok) {
      if (status === 409 && body.error === "project exists") {
        await loadList();
        setError("Project already exists — rename or tick Overwrite.");
      } else if (status === 409) {
        setError("A project is already being created — wait for it to finish.");
      } else {
        setError(body.error || `request failed (${status})`);
      }
      return;
    }
    // Hand the run off to the build store (it shows in the list) and get out of the way.
    onStarted(body.project);
  }, [checkedItems, projectName, collision, overwrite, jobRunning, loadList, onStarted]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Keep keystrokes inside the modal — Studio binds global shortcuts.
    e.stopPropagation();
    if (e.key === "Escape" && !busy) onClose();
  };

  const createDisabled =
    busy || jobRunning || !checkedItems.length || !projectName || (collision && !overwrite);

  return (
    <div style={overlayStyle} onClick={() => !busy && onClose()}>
      <div style={modalStyle} onClick={(e) => e.stopPropagation()} onKeyDown={onKeyDown}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>New project</div>
          <button
            onClick={() => !busy && onClose()}
            style={{ ...btn(false), padding: "2px 9px", fontSize: 16 }}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Drop zone */}
        <div style={sectionLabel}>Add a video</div>
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => !busy && fileInputRef.current?.click()}
          style={{
            border: `2px dashed ${dragOver ? ACCENT : "rgba(255,255,255,0.2)"}`,
            borderRadius: 8,
            padding: "22px 14px",
            textAlign: "center",
            color: MUTED,
            background: dragOver ? "rgba(11,132,243,0.08)" : "transparent",
            cursor: busy ? "default" : "pointer",
          }}
        >
          Drop video(s) here or click to choose
          <div style={{ fontSize: 11, marginTop: 4, opacity: 0.7 }}>
            Copies into input/ (.mp4 / .mov). Big files: use the path field below.
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".mp4,.mov"
            multiple
            style={{ display: "none" }}
            onChange={(e) => runUploads(Array.from(e.target.files || []))}
          />
        </div>

        {/* Path field (zero-copy reference) */}
        <div style={sectionLabel}>…or reference a local path (no copy)</div>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            type="text"
            value={pathInput}
            placeholder="/Users/you/Movies/clip.mp4"
            onChange={(e) => setPathInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") addPath();
            }}
            style={{ ...fieldStyle, flex: 1 }}
          />
          <button onClick={addPath} style={btn(false)} disabled={busy}>
            Add
          </button>
        </div>
        {pathError ? (
          <div style={{ color: "#ff6b6b", fontSize: 12, marginTop: 4 }}>{pathError}</div>
        ) : null}

        {/* Select inputs */}
        <div style={sectionLabel}>Select video(s) to merge ({checkedItems.length})</div>
        {items.length === 0 ? (
          <div style={{ color: MUTED, fontSize: 12 }}>No videos in input/ yet — drop or add one.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {items.map((it) => (
              <label
                key={it.key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  borderRadius: 5,
                  background: checked.has(it.key) ? "rgba(11,132,243,0.12)" : "rgba(255,255,255,0.04)",
                  cursor: "pointer",
                }}
              >
                <input type="checkbox" checked={checked.has(it.key)} onChange={() => toggle(it.key)} />
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {it.label}
                  {it.external ? (
                    <span style={{ color: ACCENT, fontSize: 10, marginLeft: 6 }}>EXTERNAL</span>
                  ) : null}
                </span>
                <span style={{ color: MUTED, fontSize: 11, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {it.sub}
                </span>
              </label>
            ))}
          </div>
        )}

        {/* Project name */}
        <div style={sectionLabel}>Project name</div>
        <input
          type="text"
          value={projectName}
          onChange={(e) => {
            setNameEdited(true);
            setProjectName(sanitizeName(e.target.value));
          }}
          style={{ ...fieldStyle, width: "100%", boxSizing: "border-box" }}
        />
        {collision ? (
          <div style={{ color: "#ffb454", fontSize: 12, marginTop: 6 }}>
            ⚠ A project named “{projectName}” already exists.{" "}
            <label style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={overwrite}
                onChange={(e) => setOverwrite(e.target.checked)}
                style={{ marginLeft: 4, verticalAlign: "middle" }}
              />{" "}
              Overwrite
            </label>
          </div>
        ) : null}

        {/* Hyperframes banner */}
        {!hyperframesUp ? (
          <div
            style={{
              marginTop: 14,
              padding: "8px 10px",
              borderRadius: 5,
              background: "rgba(255,180,84,0.12)",
              border: "1px solid rgba(255,180,84,0.4)",
              color: "#ffb454",
              fontSize: 12,
            }}
          >
            Motion graphics (reel step 6) need the Hyperframes producer on :9847 —
            run <code>npx hyperframes-producer</code>, or steps 1–4 still save and you
            can re-run step 6 later. (youtube mode skips it.)
          </div>
        ) : null}

        {/* Status */}
        {phase === "uploading" ? (
          <div style={{ marginTop: 16, color: MUTED }}>{uploadMsg}</div>
        ) : null}
        {jobRunning ? (
          <div style={{ marginTop: 16, color: MUTED, fontSize: 12 }}>
            A project is being created — wait for it to finish before starting another.
          </div>
        ) : null}
        {error ? (
          <div style={{ marginTop: 12, color: "#ff6b6b", fontSize: 12 }}>{error}</div>
        ) : null}

        {/* Actions */}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
          <button onClick={() => !busy && onClose()} style={btn(false)} disabled={busy}>
            Cancel
          </button>
          <button onClick={create} style={{ ...btn(true), opacity: createDisabled ? 0.5 : 1 }} disabled={createDisabled}>
            {collision && overwrite ? "Overwrite & Create" : "Create project"}
          </button>
        </div>
      </div>
    </div>
  );
};

export const NewProjectLauncher: React.FC = () => {
  const [open, setOpen] = useState(false);
  const build = useBuild();

  // Re-attach to a run already in flight (survives reloads / first mount), and
  // adopt runs kicked off elsewhere — RerunLauncher dispatches `ve-job-started`
  // after POSTing /rerun-pipeline, so reruns also light up the in-list progress
  // bar + selection block + deselect.
  useEffect(() => {
    buildStore.initOnce();
    const onJobStarted = (e: Event) => {
      const d = (e as CustomEvent).detail;
      if (d && typeof d.project === "string") buildStore.start(d.project);
    };
    window.addEventListener("ve-job-started", onJobStarted);
    return () => window.removeEventListener("ve-job-started", onJobStarted);
  }, []);

  const onStarted = useCallback((project: string) => {
    buildStore.start(project);
    setOpen(false);
  }, []);

  return (
    <>
      <button style={triggerStyle} onClick={() => setOpen(true)}>
        + New project
      </button>
      {open ? (
        <Modal onClose={() => setOpen(false)} onStarted={onStarted} jobRunning={build?.state === "running"} />
      ) : null}
    </>
  );
};
