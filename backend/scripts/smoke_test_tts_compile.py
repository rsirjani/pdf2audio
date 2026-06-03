"""Smoke test with torch.compile + static KV cache."""
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from snac import SNAC
import soundfile as sf

from pdf_reader.tts import (
    ORPHEUS_MODEL,
    SNAC_MODEL,
    SAMPLE_RATE,
    START_OF_HUMAN,
    END_OF_TEXT,
    END_OF_HUMAN,
    END_OF_SPEECH,
    AUDIO_CODE_OFFSET,
    AUDIO_CODE_MAX,
)


def decode_snac(tokens, snac_model, device):
    import numpy as np
    codes = [t - AUDIO_CODE_OFFSET for t in tokens if AUDIO_CODE_OFFSET <= t <= AUDIO_CODE_MAX]
    n_frames = len(codes) // 7
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32)
    codes = codes[: n_frames * 7]
    layer_1, layer_2, layer_3 = [], [], []
    for i in range(n_frames):
        b = i * 7
        layer_1.append(codes[b])
        layer_2.append(codes[b + 1] - 4096)
        layer_3.append(codes[b + 2] - 2 * 4096)
        layer_3.append(codes[b + 3] - 3 * 4096)
        layer_2.append(codes[b + 4] - 4 * 4096)
        layer_3.append(codes[b + 5] - 5 * 4096)
        layer_3.append(codes[b + 6] - 6 * 4096)
    codes_t = [
        torch.tensor(layer_1, device=device).unsqueeze(0),
        torch.tensor(layer_2, device=device).unsqueeze(0),
        torch.tensor(layer_3, device=device).unsqueeze(0),
    ]
    with torch.inference_mode():
        audio = snac_model.decode(codes_t).squeeze().cpu().float().numpy()
    return audio


def main():
    device = "cuda"
    print("Loading tokenizer + model + SNAC...")
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(ORPHEUS_MODEL)
    model = AutoModelForCausalLM.from_pretrained(ORPHEUS_MODEL, dtype=torch.bfloat16).to(device).eval()
    snac_model = SNAC.from_pretrained(SNAC_MODEL).to(device).eval()
    print(f"Loaded in {time.time() - t0:.1f}s")

    model.generation_config.cache_implementation = "static"
    model.generation_config.max_new_tokens = 2048

    print("Compiling model (cold)...")
    t0 = time.time()
    model.forward = torch.compile(model.forward, mode="reduce-overhead", dynamic=False)

    text_warmup = "Warmup."
    prompt = f"tara: {text_warmup}"
    input_ids = tok(prompt, return_tensors="pt").input_ids
    start = torch.tensor([[START_OF_HUMAN]], dtype=input_ids.dtype)
    end = torch.tensor([[END_OF_TEXT, END_OF_HUMAN]], dtype=input_ids.dtype)
    input_ids = torch.cat([start, input_ids, end], dim=1).to(device)

    with torch.inference_mode():
        _ = model.generate(
            input_ids=input_ids,
            attention_mask=torch.ones_like(input_ids),
            max_new_tokens=256,
            do_sample=True,
            temperature=0.6,
            top_p=0.9,
            repetition_penalty=1.1,
            eos_token_id=END_OF_SPEECH,
            pad_token_id=tok.eos_token_id,
        )
    print(f"Warmup + compile: {time.time() - t0:.1f}s")

    text = "Hello, this is a smoke test of the Orpheus three B model running locally."
    prompt = f"tara: {text}"
    input_ids = tok(prompt, return_tensors="pt").input_ids
    input_ids = torch.cat([start, input_ids, end], dim=1).to(device)

    print(f"Synthesizing: {text!r}")
    t0 = time.time()
    with torch.inference_mode():
        gen = model.generate(
            input_ids=input_ids,
            attention_mask=torch.ones_like(input_ids),
            max_new_tokens=2048,
            do_sample=True,
            temperature=0.6,
            top_p=0.9,
            repetition_penalty=1.1,
            eos_token_id=END_OF_SPEECH,
            pad_token_id=tok.eos_token_id,
        )
    new_tokens = gen[0][input_ids.shape[1]:].tolist()
    audio = decode_snac(new_tokens, snac_model, device)
    elapsed = time.time() - t0
    duration = len(audio) / SAMPLE_RATE
    rtf = elapsed / duration if duration > 0 else float("inf")
    print(f"Generated {duration:.2f}s of audio in {elapsed:.2f}s (RTF={rtf:.2f}x)")

    out = Path.home() / "orpheus_smoke_compile.wav"
    sf.write(out, audio, SAMPLE_RATE)
    print(f"Saved to {out}")

    # Second pass to confirm warm cache speed
    print("Second pass (warm)...")
    t0 = time.time()
    with torch.inference_mode():
        gen2 = model.generate(
            input_ids=input_ids,
            attention_mask=torch.ones_like(input_ids),
            max_new_tokens=2048,
            do_sample=True,
            temperature=0.6,
            top_p=0.9,
            repetition_penalty=1.1,
            eos_token_id=END_OF_SPEECH,
            pad_token_id=tok.eos_token_id,
        )
    new2 = gen2[0][input_ids.shape[1]:].tolist()
    audio2 = decode_snac(new2, snac_model, device)
    elapsed2 = time.time() - t0
    dur2 = len(audio2) / SAMPLE_RATE
    rtf2 = elapsed2 / dur2 if dur2 > 0 else float("inf")
    print(f"  pass 2: {dur2:.2f}s in {elapsed2:.2f}s (RTF={rtf2:.2f}x)")


if __name__ == "__main__":
    main()
