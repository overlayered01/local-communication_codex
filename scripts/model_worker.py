"""Optional engine worker. Run in the engine's own venv; stdout is JSONL only."""
import contextlib
import importlib.metadata
import json
import sys
import traceback


class Engine:
    def __init__(self, config):
        self.config = config
        self.engine = config["engine"]
        self.params = config.get("params", {})
        load = dict(config.get("load", {}))
        if self.engine == "supertonic":
            from supertonic import TTS
            self.model = TTS(**load)
            self.voice = self.model.get_voice_style(voice_name=config.get("voice", "F1"))
        elif self.engine == "qwen_tts":
            import torch
            from qwen_tts import Qwen3TTSModel
            dtype = load.pop("dtype", "bfloat16")
            self.model = Qwen3TTSModel.from_pretrained(config["model"], dtype=getattr(torch, dtype), **load)
        elif self.engine == "faster_whisper":
            from faster_whisper import WhisperModel
            self.model = WhisperModel(config["model"], **load)
        else:
            raise ValueError(f"Unknown engine: {self.engine}")

    def run(self, case, output):
        if self.engine == "supertonic":
            wav, _ = self.model.synthesize(text=case["text"], voice_style=self.voice, **self.params)
            self.model.save_audio(wav, output)
        elif self.engine == "qwen_tts":
            import soundfile as sf
            wavs, rate = self.model.generate_custom_voice(text=case["text"], **self.params)
            sf.write(output, wavs[0], rate, subtype="PCM_16")
        else:
            segments, info = self.model.transcribe(case["audio"], **self.params)
            # Transcription is lazy: fully consume inside the timed request.
            text = "".join(segment.text for segment in segments).strip()
            return {"text": text, "language": info.language, "metrics": {}}
        return {"audio_path": output, "metrics": {}}


def main():
    engine = None
    protocol = sys.stdout
    for line in sys.stdin:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                request = json.loads(line)
                if request["op"] == "load":
                    engine = Engine(request["config"])
                    packages = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
                    response = {"metadata": {"python": sys.version, "packages": packages,
                                              "engine": engine.engine}}
                elif request["op"] == "run" and engine:
                    response = engine.run(request["case"], request["output"])
                else:
                    raise ValueError("Load engine before inference")
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response = {"error": f"{type(exc).__name__}: {exc}"}
        protocol.write(json.dumps(response, ensure_ascii=False) + "\n")
        protocol.flush()


if __name__ == "__main__":
    main()
