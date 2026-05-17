import { Composition } from "remotion";
import { VideoComposition } from "./Composition";
import { compositionSchema } from "./schema";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="VideoEditor"
      component={VideoComposition}
      durationInFrames={2360}
      fps={30}
      width={608}
      height={1080}
      schema={compositionSchema}
      defaultProps={{"videoSrc": "edited.mp4", "captions": [{"startMs": 400, "endMs": 5733, "text": "Si sos dueño de negocio, esto es para vos. Todos sabemos que la guía se va a llevar puesta al mercado ¿Por qué?"}, {"startMs": 6033, "endMs": 6533, "text": "¿Por qué es como la internet? Había una época donde nadie sabía..."}, {"startMs": 13366, "endMs": 13366, "text": "Había una época donde nadie tenía una computadora en el bolsillo de su pantalón."}, {"startMs": 13533, "endMs": 13533, "text": "Ni hablar de..."}, {"startMs": 13533, "endMs": 19700, "text": "Y hoy es impensado no tener una cuenta de Instagram, una página web o tu ubicación marcada en Google Maps."}, {"startMs": 19866, "endMs": 22966, "text": "Y el mercado es como la selección natural en la jungla de concreto."}, {"startMs": 23333, "endMs": 26133, "text": "¿Dónde te adaptas si sobrevivís o salís del juego?"}, {"startMs": 26466, "endMs": 28833, "text": "¿Y por qué esto te importa a vos en este momento?"}, {"startMs": 29033, "endMs": 31466, "text": "¿Y por qué te estoy hablando de esto ahora?"}, {"startMs": 31833, "endMs": 34733, "text": "Porque yo soy mariano, licenciado en ciencias de la computación"}, {"startMs": 34733, "endMs": 35933, "text": "por la Universidad de Buenos Aires."}, {"startMs": 38433, "endMs": 38433, "text": "Y en este momento estoy seleccionando un puñado de negocios"}, {"startMs": 38433, "endMs": 40366, "text": "para hacerles automatizaciones"}, {"startMs": 40700, "endMs": 45600, "text": "para generar mi primer porfolio de productos de automatización para negocios."}, {"startMs": 45766, "endMs": 45766, "text": "Y con el objetivo..."}, {"startMs": 45766, "endMs": 47933, "text": "Y con el objetivo de enriquecer"}, {"startMs": 47933, "endMs": 51266, "text": "mi porfolio de automatizaciones con IAPA de negocios."}, {"startMs": 51633, "endMs": 52100, "text": "Y con el objetivo..."}, {"startMs": 52466, "endMs": 54466, "text": "Estoy seleccionando un puñado de estos"}, {"startMs": 54466, "endMs": 54966, "text": "a los cuales le voy a brindar"}, {"startMs": 56700, "endMs": 60633, "text": "una automatización con inteligencia artificial completamente gratis."}, {"startMs": 65400, "endMs": 65400, "text": "y querés que primero que nada te asesore"}, {"startMs": 65400, "endMs": 70400, "text": "y veamos cuáles de los procesos repetitivos y manuales se pueden automatizar"}, {"startMs": 70400, "endMs": 71400, "text": "entre un negocio y segundo,"}, {"startMs": 76766, "endMs": 76766, "text": "Te invito a que me sigas y que me mandes un mensaje."}, {"startMs": 77033, "endMs": 77266, "text": "Nos vemos."}], "imageOverlays": []}}
    />
  );
};
