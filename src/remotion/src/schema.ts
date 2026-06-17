import { z } from "zod";

export const captionSchema = z.object({
  startMs: z.number().describe("Inicio (ms)"),
  endMs: z.number().describe("Fin (ms)"),
  text: z.string().describe("Texto del subtítulo"),
  cutIndex: z
    .number()
    .int()
    .optional()
    .describe("Índice del corte (cut) al que pertenece"),
  words: z
    .array(z.object({ startMs: z.number(), endMs: z.number() }))
    .optional()
    .describe("Tiempos por palabra (opcional, para highlight preciso)"),
});

export const imageOverlaySchema = z.object({
  file: z.string().describe("Archivo de imagen"),
  timestamp_ms: z.number().describe("Aparece en (ms)"),
  duration_ms: z.number().min(1).describe("Duración (ms)"),
  x: z.number().min(0).max(1).describe("Posición X (0–1, izquierda → derecha)"),
  y: z.number().min(0).max(1).describe("Posición Y (0–1, arriba → abajo)"),
});

export const titleCardSchema = z.object({
  title: z.string().describe("Título (línea principal)"),
  titleHighlight: z
    .string()
    .default("")
    .describe("Palabra o frase del título a resaltar en Gold (opcional)"),
  subtitle: z.string().default("").describe("Subtítulo (línea secundaria, opcional)"),
  startMs: z.number().describe("Aparece en (ms)"),
  durationMs: z.number().min(1).default(3000).describe("Duración en pantalla (ms)"),
});

export const compositionSchema = z.object({
  videoSrc: z.string(),
  project: z.string().optional().describe("Nombre del proyecto (para el sidecar de cortes)"),
  videoVersion: z.number().optional().describe("Token cache-bust del video re-cortado"),
  imageOverlays: z.array(imageOverlaySchema).default([]),
  captions: z.array(captionSchema).default([]),
  titleCards: z.array(titleCardSchema).default([]),
  captionsEnabled: z.boolean().default(false).describe("Mostrar subtítulos sobre el video"),
});

export type CaptionSegment = z.infer<typeof captionSchema>;
export type ImageOverlay = z.infer<typeof imageOverlaySchema>;
export type TitleCard = z.infer<typeof titleCardSchema>;
export type CompositionProps = z.infer<typeof compositionSchema>;
