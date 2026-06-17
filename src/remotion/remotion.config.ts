import { Config } from "@remotion/cli/config";
import path from "path";

// Render output → video-editor/output/<project>-edited.mp4 (not the default out/).
// Point at the output DIRECTORY (no filename), so the per-composition
// `defaultOutName` (= "<project>-edited", set in the generated Root.tsx) supplies
// the filename. Remotion's getDefaultOutLocation only honors defaultOutName when
// the configured outputLocation has NO file extension — a full "*.mp4" path is
// returned verbatim and shadows defaultOutName (that's why every comp showed "final").
// Use ABSOLUTE path resolved from process.cwd():
//  - Studio rejects paths starting with "." ("output name must not start with a dot"),
//    so relative "../../output" won't work.
//  - __dirname is unreliable here: Remotion bundles the config and __dirname
//    resolves into node_modules at render time.
//  - process.cwd() = the dir Studio/CLI was launched from, which is src/remotion/.
Config.setOutputLocation(path.resolve(process.cwd(), "../../output"));

// Public dir = src/remotion/public/ (Remotion default).
// VideoEditor/Caro/Agendia all reference assets via staticFile() from public/
// (e.g. 4_render.py copies edited.mp4 → public/). Overriding to ../../input
// broke staticFile() resolution → 404 + MediaPlaybackError. Left as default.
