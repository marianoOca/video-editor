#!/usr/bin/env node
/**
 * Render script: lee el videoSrc de Root.tsx y guarda en /output/<videoSrc>
 * Uso: node render.mjs  (o npm run render)
 */

import { execSync } from "child_process";
import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Red videoSrc from Root.tsx
const rootTsx = readFileSync(resolve(__dirname, "src/Root.tsx"), "utf8");
const match = rootTsx.match(/videoSrc["'\s:]+["']([^"']+)["']/);
const videoSrc = match ? match[1] : "output.mp4";

// Output: ../../output/<videoSrc>  (relative  a src/remotion/)
const outputPath = resolve(__dirname, "../../output", videoSrc);

console.log(`🎬 Renderizando VideoEditor → ${outputPath}`);

execSync(
  `npx remotion render VideoEditor "${outputPath}"`,
  { stdio: "inherit", cwd: __dirname }
);
