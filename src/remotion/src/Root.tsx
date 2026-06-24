import { Composition, Folder } from "remotion";
import { VideoComposition } from "./Composition";
import { compositionSchema, type CompositionProps } from "./schema";

type ProjectSnapshot = CompositionProps & {
  name: string;
  durationInFrames: number;
  width: number;
  height: number;
  fps: number;
};

const ctx = (
  require as unknown as {
    context(
      dir: string,
      recursive: boolean,
      regexp: RegExp,
    ): { keys(): string[]; (id: string): unknown };
  }
).context("./projects", false, /\.json$/);

const PROJECTS: ProjectSnapshot[] = ctx
  .keys()
  .sort()
  .map((key) => ctx(key) as ProjectSnapshot);

export const RemotionRoot: React.FC = () => {
  return (
    <Folder name="Projects">
      {PROJECTS.map((p) => (
        <Composition
          key={p.name}
          id={p.name}
          calculateMetadata={() => ({ defaultOutName: `${p.name}-edited` })}
          component={VideoComposition}
          durationInFrames={p.durationInFrames}
          fps={p.fps}
          width={p.width}
          height={p.height}
          schema={compositionSchema}
          defaultProps={{
            project: p.name,
            videoSrc: p.videoSrc,
            videoVersion: p.videoVersion ?? 0,
            imageOverlays: p.imageOverlays,
            captions: p.captions,
            captionsEnabled: p.captionsEnabled ?? false,
            titleCards: p.titleCards,
          }}
        />
      ))}
    </Folder>
  );
};
