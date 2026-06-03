"""Smoke test: load Orpheus, synthesize a short clip, save to /tmp."""
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_reader.tts import OrpheusTTS

def main():
    print("Loading Orpheus (4-bit)...")
    t0 = time.time()
    tts = OrpheusTTS(voice="tara", quantize_4bit=True)
    print(f"Loaded in {time.time() - t0:.1f}s")

    import torch
    free, total = torch.cuda.mem_get_info()
    print(f"VRAM after load: {(total-free)/1e9:.2f} GB used / {total/1e9:.2f} GB total")

    text = "Hello, this is a smoke test of the Orpheus three B model running locally on the laptop."
    out = Path("/tmp/orpheus_smoke.wav")
    print(f"Synthesizing: {text!r}")
    t0 = time.time()
    duration = tts.synthesize(text, out)
    elapsed = time.time() - t0
    rtf = elapsed / duration if duration > 0 else float("inf")
    print(f"Generated {duration:.2f}s of audio in {elapsed:.2f}s (RTF={rtf:.2f}x)")
    print(f"Saved to {out}")

if __name__ == "__main__":
    main()
