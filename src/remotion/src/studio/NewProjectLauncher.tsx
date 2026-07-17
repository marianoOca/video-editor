import React, { useCallback, useEffect, useRef, useState } from "react";
import { Internals } from "remotion";

// "+ New project" launcher for the Studio Compositions panel (left sidebar).
// A button + a self-contained modal (Studio's ModalsContext is not public API, so
// we render our own position:fixed overlay) that stages videos into input/ three
// ways — drop (XHR upload), paste a local path (referenced in place, no copy), or
// pick from input/ — then kicks off run_all.py 1-6 as a background job via the
// sidecar. Two submit buttons: "Create project" (pipeline only) and "Create &
// render" (also renders output/<project>-edited.mp4 as a final step, so no manual Studio
// render is needed) — the only difference is the `render` flag in the POST body.
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
 * Build store — a module-level singleton mirroring the sidecar's job QUEUE. Keyed
 * by project (a project has at most one active job — dedupe is enforced server-
 * side), so every project row can look up its own build. The sidecar is the source
 * of truth: this store just polls /pipeline-status and reflects the `jobs` list.
 * Lives outside React so it survives the modal closing and component remounts.
 *
 * Jobs come from ALL heavy actions — new project, re-run, fix, and Subtitles-tab
 * re-cut — which the sidecar runs one at a time via a FIFO worker. So a project row
 * can show "Queued · Nth in line" (with an ✕ to cancel), a running progress bar, a
 * brief "Ready", or an error (with ✕ to dismiss).
 * ──────────────────────────────────────────────────────────────────────────── */

type BuildState = "queued" | "running" | "done" | "error";
type Build = {
  id: string;
  project: string;
  kind?: string; // new | rerun | fix | recut
  state: BuildState;
  step: number;
  total: number;
  label: string;
  error: string | null;
  queuePos: number | null; // 0-based position among QUEUED jobs
};

// Immutable map so useSyncExternalStore's getSnapshot stays referentially stable.
let _builds: Record<string, Build> = {};
const _subs = new Set<() => void>();
let _poll: ReturnType<typeof setInterval> | null = null;
let _inited = false;

const _emit = () => _subs.forEach((f) => f());
const _setBuilds = (next: Record<string, Build>) => {
  _builds = next;
  _emit();
};

const _stopPoll = () => {
  if (_poll) {
    clearInterval(_poll);
    _poll = null;
  }
};

const _fromJobs = (jobs: any[]): Record<string, Build> => {
  const next: Record<string, Build> = {};
  for (const j of jobs) {
    next[j.project] = {
      id: j.id,
      project: j.project,
      kind: j.kind,
      state: j.state,
      step: j.step ?? 0,
      total: j.total ?? 6,
      label: j.label || "Working",
      error: j.error ?? null,
      queuePos: j.queuePos ?? null,
    };
  }
  return next;
};

const _beginPoll = () => {
  _stopPoll();
  _poll = setInterval(async () => {
    const { ok, body } = await fetchJSON("/pipeline-status");
    if (!ok || !Array.isArray(body.jobs)) return;
    // A finished re-cut swapped edited.mp4 in place and rewrote the snapshot; a
    // hard reload picks up the re-mapped captions AND drops the in-memory
    // (shouldSave:false) caption override that would otherwise shadow them. Dequeue
    // it first so the reloaded page doesn't see it done again and reload-loop.
    const recutDone = body.jobs.find((j: any) => j.kind === "recut" && j.state === "done");
    if (recutDone) {
      _stopPoll();
      await fetchJSON("/dequeue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: recutDone.id }),
      });
      window.location.reload();
      return;
    }
    _setBuilds(_fromJobs(body.jobs));
    if (body.jobs.length === 0) _stopPoll();
  }, 1000);
};

export const buildStore = {
  // Seed one build optimistically (so the row shows without a 1s poll lag) and
  // ensure polling is on. The poll reconciles to the server's truth immediately.
  startOrQueue(detail: { project: string; id?: string; state?: string }) {
    const state = (detail.state as BuildState) || "queued";
    const b: Build = {
      id: detail.id || `pending-${detail.project}`,
      project: detail.project,
      state,
      step: 0,
      total: 6,
      label: state === "running" ? "Starting" : "Queued",
      error: null,
      queuePos: null,
    };
    _setBuilds({ ..._builds, [detail.project]: b });
    _beginPoll();
  },
  // Cancel a queued job / dismiss a finished one (server drops it; optimistic here).
  dequeue(id: string) {
    fetchJSON("/dequeue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }).catch(() => {});
    const next: Record<string, Build> = {};
    for (const [k, v] of Object.entries(_builds)) if (v.id !== id) next[k] = v;
    _setBuilds(next);
  },
  subscribe(cb: () => void) {
    _subs.add(cb);
    return () => {
      _subs.delete(cb);
    };
  },
  get(): Record<string, Build> {
    return _builds;
  },
  // Re-attach to jobs already in flight (e.g. after a page reload mid-build).
  initOnce() {
    if (_inited) return;
    _inited = true;
    (async () => {
      const { ok, body } = await fetchJSON("/pipeline-status");
      if (ok && Array.isArray(body.jobs) && body.jobs.length) {
        _setBuilds(_fromJobs(body.jobs));
        _beginPoll();
      }
    })();
  },
};

// A project is blocked from selection only while its job is actively RUNNING
// (queued/done/error stay clickable — a queued project isn't being written yet, so
// its old cut still plays). Plain function so the patched onClick can call it.
export const isBuildBlocked = (compositionId: string): boolean => {
  const b = _builds[compositionId];
  return !!b && b.state === "running";
};

const useBuilds = (): Record<string, Build> =>
  React.useSyncExternalStore(buildStore.subscribe, buildStore.get, buildStore.get);

// Per-project build (for a composition row or the Subtitles tab's edit-lock).
export const useProjectBuild = (project: string | null): Build | null => {
  const builds = useBuilds();
  return project ? builds[project] ?? null : null;
};

/* ──────────────────────────── shared visuals ─────────────────────────────── */

// A re-cut runs 4_render.py directly (no run_all step markers), so it has no
// determinate progress — show a sliding indeterminate bar. Keyframes injected once.
const ensureIndetStyle = () => {
  const ID = "ve-indet-style";
  if (typeof document === "undefined" || document.getElementById(ID)) return;
  const s = document.createElement("style");
  s.id = ID;
  s.textContent = "@keyframes ve-indet{0%{left:-40%}100%{left:100%}}";
  document.head.appendChild(s);
};

const isIndeterminate = (build: Build): boolean =>
  build.state === "running" && (build.kind === "recut" || build.total <= 1);

const ProgressBar: React.FC<{ build: Build }> = ({ build }) => {
  ensureIndetStyle();
  const indet = isIndeterminate(build);
  const pct = build.state === "done" ? 100 : build.total ? Math.round((build.step / build.total) * 100) : 0;
  return (
    <div style={{ position: "relative", height: 4, borderRadius: 2, background: "rgba(255,255,255,0.12)", overflow: "hidden" }}>
      {indet ? (
        <div
          style={{
            position: "absolute",
            top: 0,
            width: "40%",
            height: "100%",
            borderRadius: 2,
            background: ACCENT,
            animation: "ve-indet 1.1s linear infinite",
          }}
        />
      ) : (
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            borderRadius: 2,
            background: build.state === "error" ? "#ff6b6b" : ACCENT,
            transition: "width 300ms",
          }}
        />
      )}
    </div>
  );
};

const ordinal = (n: number): string => {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
};

const buildCaption = (build: Build): string => {
  if (build.state === "done") return "Ready";
  if (build.state === "queued") {
    return build.queuePos != null ? `Queued · ${ordinal(build.queuePos + 1)} in line` : "Queued";
  }
  if (isIndeterminate(build)) return build.label; // e.g. "Re-cutting…"
  return `${build.label} · ${build.step}/${build.total}`;
};

// Bar/caption body shared by CompositionBuildBar (existing project rows) and
// SyntheticBuildRow (new-project rows with no composition yet).
const BuildRowBody: React.FC<{ build: Build }> = ({ build }) => {
  if (build.state === "error") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: "#ff6b6b", fontSize: 10, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {build.error || "failed"}
        </span>
        <button onClick={() => buildStore.dequeue(build.id)} style={{ ...btn(false), padding: "0 6px", fontSize: 11 }} aria-label="Dismiss">
          ✕
        </button>
      </div>
    );
  }
  if (build.state === "queued") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: MUTED, fontSize: 10, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {buildCaption(build)}
        </span>
        <button onClick={() => buildStore.dequeue(build.id)} style={{ ...btn(false), padding: "0 6px", fontSize: 11 }} aria-label="Cancel">
          ✕
        </button>
      </div>
    );
  }
  return (
    <>
      <ProgressBar build={build} />
      <div style={{ fontSize: 9, color: MUTED, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {buildCaption(build)}
      </div>
    </>
  );
};

/* ───────────── pieces injected into the patched Studio components ──────────── */

// Rendered under the matching REAL composition row (patched into CompositionSelectorItem.js).
export const CompositionBuildBar: React.FC<{ compositionId: string; level: number }> = ({ compositionId, level }) => {
  const build = useProjectBuild(compositionId);
  if (!build) return null;
  // Align under the row's name (paddingLeft 12 + level*8 + icon 18 + spacing 8).
  const padLeft = 38 + level * 8;
  return (
    <div style={{ padding: `2px 12px 7px ${padLeft}px`, background: PANEL_BG, fontFamily: "Arial, Helvetica, sans-serif" }}>
      <BuildRowBody build={build} />
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
// One stand-in row for a single build whose composition doesn't exist yet.
const SyntheticRow: React.FC<{ build: Build; level: number }> = ({ build, level }) => {
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
      </div>
      {/* progress/queued/error body under the name — same layout as CompositionBuildBar */}
      <div style={{ padding: `2px 12px 7px ${barPadLeft}px`, background: PANEL_BG }}>
        <BuildRowBody build={build} />
      </div>
    </div>
  );
};

// Stand-in rows shown while a build's composition snapshot doesn't exist yet
// (a brand-new project during steps 1-3, or one still queued). Each mirrors a
// native CompositionSelectorItem row (32px, indent, film icon, 13px) with the
// build body underneath. Re-runs/fixes/re-cuts target EXISTING projects (real
// rows → CompositionBuildBar), so only new-project builds ever appear here — but
// several may be queued at once, so we render one row per orphan build. Mounted twice:
//   • inside the Projects folder body (CompositionSelectorItem.js, level = folder+1)
//   • at the top of the list as a `fallback` (CompositionSelector.js) — used only
//     when there is NO Projects folder yet. The two never both render: the fallback
//     yields if the folder exists.
export const SyntheticBuildRow: React.FC<{ level?: number; fallback?: boolean }> = ({
  level = 1,
  fallback = false,
}) => {
  const builds = useBuilds();
  const cm = React.useContext((Internals as any).CompositionManager) as any;
  const compIds = new Set((cm?.compositions || []).map((c: any) => c.id));
  const orphans = Object.values(builds).filter((b) => !compIds.has(b.project));
  if (orphans.length === 0) return null;
  // The Projects-folder copy owns these rows; the top-level fallback yields to it.
  if (fallback && (cm?.folders || []).some((f: any) => f.name === PROJECTS_FOLDER)) return null;
  return (
    <>
      {orphans.map((b) => (
        <SyntheticRow key={b.project} build={b} level={level} />
      ))}
    </>
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
  onStarted: (detail: { project: string; id?: string; state?: string }) => void;
}> = ({ onClose, onStarted }) => {
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

  // render=true also renders output/<project>-edited.mp4 as a final step, right after the
  // pipeline finishes — so there's no manual Studio render afterwards.
  const create = useCallback(async (render: boolean) => {
    if (!checkedItems.length || !projectName || (collision && !overwrite)) return;
    setError(null);
    const inputs = checkedItems.map((i) => i.send);
    const { ok, status, body } = await fetchJSON("/run-pipeline", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs, project: projectName, overwrite, render }),
    });
    if (!ok) {
      if (status === 409 && body.error === "project exists") {
        await loadList();
        setError("Project already exists — rename or tick Overwrite.");
      } else if (status === 409) {
        // A same-named project already has a job queued/running (dedupe).
        setError(body.error || "This project already has a job queued or running.");
      } else {
        setError(body.error || `request failed (${status})`);
      }
      return;
    }
    // Hand the run off to the build store — it queues behind any running jobs and
    // shows its progress/queued state in the list. Get out of the way.
    onStarted({ project: body.project, id: body.jobId, state: body.state });
  }, [checkedItems, projectName, collision, overwrite, loadList, onStarted]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Keep keystrokes inside the modal — Studio binds global shortcuts.
    e.stopPropagation();
    if (e.key === "Escape" && !busy) onClose();
  };

  const createDisabled =
    busy || !checkedItems.length || !projectName || (collision && !overwrite);

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
        {error ? (
          <div style={{ marginTop: 12, color: "#ff6b6b", fontSize: 12 }}>{error}</div>
        ) : null}

        {/* Actions */}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20 }}>
          <button onClick={() => !busy && onClose()} style={btn(false)} disabled={busy}>
            Cancel
          </button>
          <button
            onClick={() => create(false)}
            style={{ ...btn(false), opacity: createDisabled ? 0.5 : 1 }}
            disabled={createDisabled}
          >
            {collision && overwrite ? "Overwrite & Create" : "Create project"}
          </button>
          <button
            onClick={() => create(true)}
            style={{ ...btn(true), opacity: createDisabled ? 0.5 : 1 }}
            disabled={createDisabled}
            title="Run the full pipeline, then render output/<project>-edited.mp4 automatically"
          >
            {collision && overwrite ? "Overwrite & Render" : "Create & render"}
          </button>
        </div>
      </div>
    </div>
  );
};

export const NewProjectLauncher: React.FC = () => {
  const [open, setOpen] = useState(false);

  // Re-attach to jobs already in flight (survives reloads / first mount), and
  // adopt jobs kicked off elsewhere — RerunLauncher (re-run) and SubtitlesTab
  // (fix, re-cut) dispatch `ve-job-started` after their POST, so those also light
  // up the in-list progress/queued bar + selection block.
  useEffect(() => {
    buildStore.initOnce();
    const onJobStarted = (e: Event) => {
      const d = (e as CustomEvent).detail;
      if (d && typeof d.project === "string") buildStore.startOrQueue(d);
    };
    window.addEventListener("ve-job-started", onJobStarted);
    return () => window.removeEventListener("ve-job-started", onJobStarted);
  }, []);

  const onStarted = useCallback((detail: { project: string; id?: string; state?: string }) => {
    buildStore.startOrQueue(detail);
    setOpen(false);
  }, []);

  return (
    <>
      <button style={triggerStyle} onClick={() => setOpen(true)}>
        + New project
      </button>
      {open ? <Modal onClose={() => setOpen(false)} onStarted={onStarted} /> : null}
    </>
  );
};
