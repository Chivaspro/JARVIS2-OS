from kokoro import KPipeline
import soundfile as sf
import numpy as np

# Configure pipeline
lang_code = "b"  # British English
p = KPipeline(lang_code=lang_code)

# Jarvis-style text
text = (
    "Welcome back, sir. "
    "All core systems are currently operating at peak efficiency. "
    "I have initiated a diagnostic check on the neural pathways, "
    "and the environmental controls are fully calibrated to your preferences. "
    "How shall I assist you with your next project today?"
)

# Voice blend: tweak this to taste
# Try: "bm_george,bm_daniel" or "bm_george,bm_fable,bm_daniel"
voice_blend = "bm_george,bm_daniel"

speed = 0.9      # Slightly slower for calm, controlled delivery
volume = 1.0     # Overall gain; we’ll normalize after
sample_rate = 24000

# Generate audio chunks from Kokoro
chunks = []
for _, _, a in p(text, voice=voice_blend, speed=speed):
    if a is not None:
        chunks.append(np.asarray(a).reshape(-1))

if not chunks:
    raise RuntimeError("No audio generated. Check Kokoro installation and voice names.")

audio = np.concatenate(chunks)

# Apply global volume
audio = audio * volume

# Simple peak normalization to avoid clipping while keeping dynamics
peak = np.max(np.abs(audio))
if peak > 0:
    audio = audio * (0.95 / peak)

output_file = "jarvis_kokoro_test.wav"
sf.write(output_file, audio, sample_rate)

print(f"Created: {output_file}")
print(f"Voices: {voice_blend}, speed: {speed}, sample_rate: {sample_rate}")