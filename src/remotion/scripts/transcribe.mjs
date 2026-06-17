/**
 * Transcribe audio using Whisper.cpp with token-level timestamps.
 * Called by src/pipeline/2_transcribe.py — not meant to be run standalone.
 *
 * Usage: node scripts/transcribe.mjs <audio.wav> <output.json> <model> <lang> <whisper-dir>
 * Output: JSON array of word captions [{text, startMs, endMs, timestampMs, confidence}]
 */
import {writeFileSync, existsSync} from "fs";
import {
  installWhisperCpp,
  downloadWhisperModel,
  transcribe,
  toCaptions,
} from "@remotion/install-whisper-cpp";

const WHISPER_VERSION = "1.5.5";

const [, , inputPath, outputPath, model, language, whisperPath] = process.argv;

if (!inputPath || !outputPath || !model || !language || !whisperPath) {
  console.error(
    "Usage: node transcribe.mjs <audio.wav> <output.json> <model> <lang> <whisper-dir>",
  );
  process.exit(1);
}

if (!existsSync(inputPath)) {
  console.error(`Audio file not found: ${inputPath}`);
  process.exit(1);
}

async function main() {
  console.log("Ensuring Whisper.cpp is installed...");
  await installWhisperCpp({to: whisperPath, version: WHISPER_VERSION});

  console.log(`Ensuring model is downloaded (${model})...`);
  await downloadWhisperModel({model, folder: whisperPath});

  console.log(`Transcribing (${language}): ${inputPath}`);
  const whisperOutput = await transcribe({
    model,
    whisperPath,
    whisperCppVersion: WHISPER_VERSION,
    inputPath,
    language,
    translateToEnglish: false,
    tokenLevelTimestamps: true,
  });

  const {captions} = toCaptions({whisperCppOutput: whisperOutput});

  writeFileSync(outputPath, JSON.stringify(captions, null, 2));
  console.log(`Captions saved to ${outputPath} (${captions.length} words)`);
}

main().catch((err) => {
  console.error("Transcription failed:", err.message);
  process.exit(1);
});
