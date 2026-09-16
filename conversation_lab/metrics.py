import math
import unicodedata
import wave
from pathlib import Path


def percentile(values, p):
    """Linear interpolation, with no rounding of individual observations."""
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * p / 100
    low, high = math.floor(index), math.ceil(index)
    return values[low] + (values[high] - values[low]) * (index - low)


def normalize(text):
    """NFC, casefold, remove punctuation, normalize whitespace; retain numbers."""
    text = unicodedata.normalize("NFC", text).casefold()
    text = "".join(c for c in text if not unicodedata.category(c).startswith("P"))
    return " ".join(text.split())


def edit_distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        following = [i]
        for j, y in enumerate(b, 1):
            following.append(min(following[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = following
    return row[-1]


def recognition_errors(reference, hypothesis):
    ref, hyp = normalize(reference), normalize(hypothesis)
    chars, words = ref.replace(" ", ""), ref.split()
    if not chars:
        raise ValueError("STT reference must contain non-punctuation text")
    ce = edit_distance(chars, hyp.replace(" ", ""))
    we = edit_distance(words, hyp.split())
    return {"cer": ce / len(chars), "wer": we / len(words),
            "char_edits": ce, "reference_chars": len(chars),
            "word_edits": we, "reference_words": len(words)}


def wav_info(path):
    """Only PCM WAV is accepted so duration never depends on filename guesses."""
    with wave.open(str(Path(path)), "rb") as audio:
        if audio.getcomptype() != "NONE" or not audio.getnframes():
            raise ValueError("Expected a non-empty PCM WAV")
        frames = audio.getnframes()
        expected = frames * audio.getnchannels() * audio.getsampwidth()
        # Validate truncation without loading a long recording into memory.
        actual = 0
        while chunk := audio.readframes(65536):
            actual += len(chunk)
        if actual != expected:
            raise ValueError("Truncated PCM WAV")
        return {"audio_duration_s": frames / audio.getframerate(),
                "sample_rate": audio.getframerate(), "channels": audio.getnchannels()}
