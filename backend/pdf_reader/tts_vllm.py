"""Orpheus TTS via vLLM — batch synthesis for fast ingest."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

log = logging.getLogger(__name__)

ORPHEUS_MODEL = "canopylabs/orpheus-3b-0.1-ft"
SNAC_MODEL = "hubertsiuzdak/snac_24khz"
SAMPLE_RATE = 24000

START_OF_HUMAN = 128259
END_OF_TEXT = 128009
END_OF_HUMAN = 128260
END_OF_SPEECH = 128258
AUDIO_CODE_OFFSET = 128266
AUDIO_CODE_MAX = 156938

VOICES = {"tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"}


class OrpheusTTS:
    """vLLM-backed Orpheus TTS with batch synthesis."""

    def __init__(self, voice: str = "tara", gpu_memory_utilization: float = 0.55):
        if voice not in VOICES:
            raise ValueError(f"voice must be one of {VOICES}")
        self.voice = voice
        self._load(gpu_memory_utilization)

    def _load(self, gpu_memory_utilization: float):
        from vllm import LLM, SamplingParams
        from snac import SNAC
        from transformers import AutoTokenizer

        log.info("Loading Orpheus tokenizer")
        self.tokenizer = AutoTokenizer.from_pretrained(ORPHEUS_MODEL)

        import os
        max_model_len = int(os.environ.get("PDF_READER_MAX_MODEL_LEN", "2304"))
        enforce_eager = os.environ.get("PDF_READER_ENFORCE_EAGER", "0") == "1"
        log.info("Initializing vLLM (gpu_util=%s, max_len=%d, eager=%s)",
                 gpu_memory_utilization, max_model_len, enforce_eager)
        self.llm = LLM(
            model=ORPHEUS_MODEL,
            dtype="bfloat16",
            gpu_memory_utilization=gpu_memory_utilization,
            max_model_len=max_model_len,
            enforce_eager=enforce_eager,
        )

        log.info("Loading SNAC decoder")
        self.snac = SNAC.from_pretrained(SNAC_MODEL).to("cuda").eval()

        self.params = SamplingParams(
            temperature=0.6,
            top_p=0.9,
            max_tokens=2048,
            stop_token_ids=[END_OF_SPEECH],
            repetition_penalty=1.1,
        )

    def _build_prompt(self, text: str) -> list[int]:
        ids = self.tokenizer(f"{self.voice}: {text}").input_ids
        return [START_OF_HUMAN] + list(ids) + [END_OF_TEXT, END_OF_HUMAN]

    def synthesize_batch(self, texts: list[str], out_paths: list[Path]) -> list[float]:
        """Synthesize a batch of texts. Returns durations in seconds (parallel to inputs)."""
        from vllm.inputs import TokensPrompt

        if len(texts) != len(out_paths):
            raise ValueError("texts and out_paths must be same length")
        if not texts:
            return []

        prompts = [TokensPrompt(prompt_token_ids=self._build_prompt(t)) for t in texts]
        outputs = self.llm.generate(prompts=prompts, sampling_params=self.params)

        durations: list[float] = []
        for out, path in zip(outputs, out_paths):
            tokens = list(out.outputs[0].token_ids)
            audio = self._decode(tokens)
            sf.write(path, audio, SAMPLE_RATE)
            durations.append(len(audio) / SAMPLE_RATE)
        return durations

    def synthesize(self, text: str, out_path: Path) -> float:
        return self.synthesize_batch([text], [out_path])[0]

    @torch.inference_mode()
    def _decode(self, tokens: list[int]) -> np.ndarray:
        codes = [t - AUDIO_CODE_OFFSET for t in tokens if AUDIO_CODE_OFFSET <= t <= AUDIO_CODE_MAX]
        n_frames = len(codes) // 7
        if n_frames == 0:
            return np.zeros(0, dtype=np.float32)
        codes = codes[: n_frames * 7]

        l1, l2, l3 = [], [], []
        for i in range(n_frames):
            b = i * 7
            l1.append(codes[b])
            l2.append(codes[b + 1] - 4096)
            l3.append(codes[b + 2] - 2 * 4096)
            l3.append(codes[b + 3] - 3 * 4096)
            l2.append(codes[b + 4] - 4 * 4096)
            l3.append(codes[b + 5] - 5 * 4096)
            l3.append(codes[b + 6] - 6 * 4096)

        codes_t = [
            torch.tensor(l1, device="cuda").unsqueeze(0),
            torch.tensor(l2, device="cuda").unsqueeze(0),
            torch.tensor(l3, device="cuda").unsqueeze(0),
        ]
        audio = self.snac.decode(codes_t).squeeze().cpu().float().numpy()
        return audio
