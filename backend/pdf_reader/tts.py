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
    def __init__(self, voice: str = "tara", quantize_4bit: bool = True):
        if voice not in VOICES:
            raise ValueError(f"voice must be one of {VOICES}")
        self.voice = voice
        self.device = "cuda"
        self._load(quantize_4bit)

    def _load(self, quantize_4bit: bool):
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from snac import SNAC

        log.info("Loading Orpheus tokenizer")
        self.tokenizer = AutoTokenizer.from_pretrained(ORPHEUS_MODEL)

        load_kwargs: dict = {"torch_dtype": torch.bfloat16}
        if quantize_4bit:
            load_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
            load_kwargs["device_map"] = "auto"

        log.info("Loading Orpheus model (4bit=%s)", quantize_4bit)
        self.model = AutoModelForCausalLM.from_pretrained(ORPHEUS_MODEL, **load_kwargs)
        if not quantize_4bit:
            self.model = self.model.to(self.device)
        self.model.eval()

        log.info("Loading SNAC decoder")
        self.snac = SNAC.from_pretrained(SNAC_MODEL).to(self.device).eval()

    @torch.inference_mode()
    def synthesize(self, text: str, out_path: Path) -> float:
        """Synthesize speech and write to WAV. Returns duration in seconds."""
        prompt = f"{self.voice}: {text}"
        input_ids = self.tokenizer(prompt, return_tensors="pt").input_ids

        start = torch.tensor([[START_OF_HUMAN]], dtype=input_ids.dtype)
        end = torch.tensor([[END_OF_TEXT, END_OF_HUMAN]], dtype=input_ids.dtype)
        input_ids = torch.cat([start, input_ids, end], dim=1).to(self.device)
        attention_mask = torch.ones_like(input_ids)

        generated = self.model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=4096,
            do_sample=True,
            temperature=0.6,
            top_p=0.9,
            repetition_penalty=1.1,
            eos_token_id=END_OF_SPEECH,
            pad_token_id=self.tokenizer.eos_token_id,
        )

        new_tokens = generated[0][input_ids.shape[1]:].tolist()
        audio = self._decode(new_tokens)
        sf.write(out_path, audio, SAMPLE_RATE)
        return len(audio) / SAMPLE_RATE

    def _decode(self, tokens: list[int]) -> np.ndarray:
        codes = [t - AUDIO_CODE_OFFSET for t in tokens if AUDIO_CODE_OFFSET <= t <= AUDIO_CODE_MAX]
        n_frames = len(codes) // 7
        if n_frames == 0:
            return np.zeros(0, dtype=np.float32)
        codes = codes[: n_frames * 7]

        layer_1: list[int] = []
        layer_2: list[int] = []
        layer_3: list[int] = []
        for i in range(n_frames):
            base = i * 7
            layer_1.append(codes[base])
            layer_2.append(codes[base + 1] - 4096)
            layer_3.append(codes[base + 2] - 2 * 4096)
            layer_3.append(codes[base + 3] - 3 * 4096)
            layer_2.append(codes[base + 4] - 4 * 4096)
            layer_3.append(codes[base + 5] - 5 * 4096)
            layer_3.append(codes[base + 6] - 6 * 4096)

        codes_t = [
            torch.tensor(layer_1, device=self.device).unsqueeze(0),
            torch.tensor(layer_2, device=self.device).unsqueeze(0),
            torch.tensor(layer_3, device=self.device).unsqueeze(0),
        ]
        with torch.inference_mode():
            audio = self.snac.decode(codes_t).squeeze().cpu().float().numpy()
        return audio
