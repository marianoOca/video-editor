import { z } from "zod";

export const captionSchema = z.object({
  startMs: z.number().describe("Inicio (ms)"),
  endMs: z.number().describe("Fin (ms)"),
  text: z.string().describe("Texto del subtítulo"),
});

export const imageOverlaySchema = z.object({
  file: z.string().describe("Archivo de imagen"),
  timestamp_ms: z.number().describe("Aparece en (ms)"),
  duration_ms: z.number().describe("Duración (ms)"),
  x: z.number().min(0).max(1).describe("Posición X (0–1, izquierda → derecha)"),
  y: z.number().min(0).max(1).describe("Posición Y (0–1, arriba → abajo)"),
});

export const compositionSchema = z.object({
  videoSrc: z.string(),
  imageOverlays: z.array(imageOverlaySchema).default([]),
  captions: z.array(captionSchema).default([]),
});

export type CaptionSegment = z.infer<typeof captionSchema>;
export type ImageOverlay = z.infer<typeof imageOverlaySchema>;
export type CompositionProps = z.infer<typeof compositionSchema>;
