import React, { useEffect, useState } from "react";

// Cleanup options embedded in Studio's native "Delete project" modal (patched into
// @remotion/studio DeleteComposition.js, which requires this across the node_modules
// boundary). The base delete always removes the project's derived files (data dir +
// snapshot + public); these three radios optionally ALSO delete the project's SHARED
// files: its source video(s) in input/ and/or its rendered output(s). Which files
// exist comes from the sidecar's /project-files (backed by the project manifest).
//
// Controlled component: the parent owns `value` and builds the codemod flags from it.
// On mount we fetch existence, disable options whose files are gone, and set the
// default (both present → "all"; otherwise → "none"). Clicking the selected row again
// returns to "none" (delete project only) so keeping the files is always reachable.
// UI copy is English (matches Studio chrome).

const SIDECAR = "http://127.0.0.1:9848";
const ACCENT = "#0b84f3";
const MUTED = "#A6A7A9";
const WARN = "#ffb454";

export type DeleteChoice = "all" | "input" | "output" | "none";

type Slot = { files: string[]; exists: boolean };
type FilesInfo = { input: Slot; output: Slot };

const OPTIONS: { key: "input" | "output" | "all"; label: string }[] = [
  { key: "all", label: "All (input + output)" },
  { key: "input", label: "Delete input source video(s)" },
  { key: "output", label: "Delete output video" },
];

function isDisabled(key: string, info: FilesInfo | null): boolean {
  if (!info) return true;
  if (key === "input") return !info.input.exists;
  if (key === "output") return !info.output.exists;
  return !(info.input.exists && info.output.exists); // "all"
}

function filesFor(key: string, info: FilesInfo | null): string[] {
  if (!info) return [];
  if (key === "input") return info.input.files;
  if (key === "output") return info.output.files;
  return [...info.input.files, ...info.output.files]; // "all"
}

export const DeleteProjectOptions: React.FC<{
  project: string;
  value: DeleteChoice;
  onChange: (c: DeleteChoice) => void;
}> = ({ project, value, onChange }) => {
  const [info, setInfo] = useState<FilesInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `${SIDECAR}/project-files?project=${encodeURIComponent(project)}`
        );
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok || !body.ok) {
          setError(body.error || `could not read project files (${res.status})`);
          setLoading(false);
          return;
        }
        const fi: FilesInfo = { input: body.input, output: body.output };
        setInfo(fi);
        setLoading(false);
        onChange(fi.input.exists && fi.output.exists ? "all" : "none");
      } catch (e: any) {
        if (cancelled) return;
        setError(String(e?.message || e));
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // onChange (a stable setState) is intentionally excluded: run once per project.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ color: MUTED, fontSize: 12, marginBottom: 6 }}>
        Also delete this project's shared files (optional):
      </div>

      {loading ? (
        <div style={{ color: MUTED, fontSize: 12 }}>checking files…</div>
      ) : error ? (
        <div style={{ color: "#ff6b6b", fontSize: 12 }}>
          Couldn't read this project's files: {error}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {OPTIONS.map((o) => {
            const disabled = isDisabled(o.key, info);
            const selected = value === o.key;
            const files = filesFor(o.key, info);
            const reason =
              o.key === "input"
                ? "none in input/"
                : o.key === "output"
                ? "no output"
                : "needs both";
            return (
              <label
                key={o.key}
                onClick={(e) => {
                  if (disabled) return;
                  e.preventDefault();
                  onChange(selected ? "none" : o.key); // click selected → back to none
                }}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  padding: "6px 8px",
                  borderRadius: 5,
                  background: selected
                    ? "rgba(11,132,243,0.14)"
                    : "rgba(255,255,255,0.04)",
                  cursor: disabled ? "not-allowed" : "pointer",
                  opacity: disabled ? 0.45 : 1,
                }}
              >
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    type="radio"
                    name="ve-delete-cleanup"
                    checked={selected}
                    disabled={disabled}
                    readOnly
                  />
                  <span style={{ flex: 1, fontSize: 13 }}>{o.label}</span>
                  {disabled ? (
                    <span style={{ color: WARN, fontSize: 10 }}>{reason}</span>
                  ) : null}
                </span>
                {!disabled && files.length ? (
                  <span
                    style={{
                      color: MUTED,
                      fontSize: 11,
                      marginLeft: 24,
                      wordBreak: "break-all",
                    }}
                  >
                    {files.join(", ")}
                  </span>
                ) : null}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
};
