import { Config } from "@remotion/cli/config";
import path from "path";

// Render output → video-editor/output/final.mp4 (not the default out/).
// Use ABSOLUTE path resolved from process.cwd():
//  - Studio rejects paths starting with "." ("output name must not start with a dot"),
//    so relative "../../output/..." won't work.
//  - __dirname is unreliable here: Remotion bundles the config and __dirname
//    resolves into node_modules at render time (output landed in
//    src/remotion/node_modules/@remotion/output/...).
//  - process.cwd() = the dir Studio/CLI was launched from, which is src/remotion/.
//  - Must include the .mp4 filename: h264+aac requires mp4/mkv/mov extension.
Config.setOutputLocation(path.resolve(process.cwd(), "../../output/final.mp4"));

// Public dir = src/remotion/public/ (Remotion default).
// VideoEditor/Caro/Agendia all reference assets via staticFile() from public/
// (e.g. 4_render.py copies edited.mp4 → public/). Overriding to ../../input
// broke staticFile() resolution → 404 + MediaPlaybackError. Left as default.
