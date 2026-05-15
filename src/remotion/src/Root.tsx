import { Composition } from "remotion";
import { VideoComposition } from "./Composition";
import { compositionSchema } from "./schema";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="VideoEditor"
      component={VideoComposition}
      durationInFrames={1545}
      fps={30}
      width={608}
      height={1080}
      schema={compositionSchema}
      defaultProps={{ videoSrc: "aislop.mp4", imageOverlays: [], captions: [] }}
    />
  );
};
