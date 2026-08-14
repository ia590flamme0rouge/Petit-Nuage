"""
speech_handler.py - Reconnaissance vocale (STT) via Whisper API (Groq)
Compatible Py-cord (format WAV natif)
"""
import io
import logging
import wave
from typing import Optional

import aiohttp

import config

logger = logging.getLogger("welcome_bot")

DISCORD_SAMPLE_RATE = 48000
DISCORD_CHANNELS = 2
DISCORD_SAMPLE_WIDTH = 2  # 16-bit PCM


def pcm_to_wav_bytes(pcm_data: bytes) -> bytes:
    """Convertit des donnees PCM brutes en fichier WAV en memoire."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(DISCORD_CHANNELS)
        wf.setsampwidth(DISCORD_SAMPLE_WIDTH)
        wf.setframerate(DISCORD_SAMPLE_RATE)
        wf.writeframes(pcm_data)
    buf.seek(0)
    return buf.read()


async def transcribe_audio(audio_bytes: bytes) -> Optional[str]:
    """Envoie l'audio WAV/PCM a l'API Whisper de Groq pour transcription."""
    if not config.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY manquante, transcription impossible.")
        return None

    if len(audio_bytes) < 2000:
        logger.debug(f"Audio trop court ({len(audio_bytes)} octets), ignore.")
        return None

    # Si ce n'est pas deja du WAV (entête RIFF), convertit du PCM en WAV
    if not audio_bytes.startswith(b"RIFF"):
        wav_bytes = pcm_to_wav_bytes(audio_bytes)
    else:
        wav_bytes = audio_bytes

    logger.info(f"Envoi de {len(wav_bytes)} octets WAV a Whisper Groq...")
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}

    form = aiohttp.FormData()
    form.add_field("file", wav_bytes, filename="audio.wav", content_type="audio/wav")
    form.add_field("model", "whisper-large-v3-turbo")
    form.add_field("language", "fr")
    form.add_field("response_format", "text")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, data=form,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200:
                    text = (await resp.text()).strip()
                    logger.info(f"Transcription Whisper reussie: {text!r}")
                    return text if text else None
                else:
                    err = await resp.text()
                    logger.error(f"Erreur Whisper HTTP {resp.status}: {err}")
                    return None
    except Exception as e:
        logger.error(f"Erreur lors de la transcription audio: {e}")
        return None
