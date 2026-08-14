"""
speech_handler.py - Reconnaissance vocale (STT) via Whisper API (Groq)
Utilise discord-ext-voice-recv pour capter l'audio de chaque membre.
"""
import asyncio
import io
import logging
import wave
from typing import Callable, Dict, Optional

import aiohttp
import discord
from discord.ext import voice_recv

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


async def transcribe_audio(pcm_data: bytes) -> Optional[str]:
    """Envoie l'audio WAV a l'API Whisper de Groq pour transcription."""
    if not config.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY manquante, transcription impossible.")
        return None

    # Filtre les enregistrements trop courts (< 0.2s = bruit)
    min_bytes = DISCORD_SAMPLE_RATE * DISCORD_CHANNELS * DISCORD_SAMPLE_WIDTH // 5
    if len(pcm_data) < min_bytes:
        logger.debug(f"Audio trop court ({len(pcm_data)} octets < {min_bytes}), ignore.")
        return None

    logger.info(f"Envoi de {len(pcm_data)} octets PCM a Whisper Groq...")
    wav_bytes = pcm_to_wav_bytes(pcm_data)
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


class VoiceSink(voice_recv.AudioSink):
    """
    Sink qui accumule l'audio PCM de chaque membre et declenche
    le callback on_speech apres un silence de silence_duration secondes.
    """

    def __init__(
        self,
        on_speech: Callable,
        silence_duration: float = 1.5,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        super().__init__()
        self.on_speech = on_speech
        self.silence_duration = silence_duration
        self.loop = loop
        self._buffers: Dict[Union[int, str], bytearray] = {}
        self._silence_tasks: Dict[Union[int, str], Any] = {}

    def wants_opus(self) -> bool:
        return False  # On veut du PCM decode

    def write(self, user, data: voice_recv.VoiceData):
        if user and user.bot:
            return

        if not data or not data.pcm:
            return

        # Si user n'est pas encore resolu par SSRC, utilise le SSRC du paquet comme cle
        key = user.id if user else f"ssrc_{data.packet.ssrc}"

        if key not in self._buffers:
            self._buffers[key] = bytearray()

        self._buffers[key].extend(data.pcm)

        # Annule le timer precedent
        old = self._silence_tasks.get(key)
        if old is not None and not old.done():
            old.cancel()

        # write() est appele depuis le thread audio -> run_coroutine_threadsafe
        target_loop = self.loop or (self.voice_client and self.voice_client.client.loop)
        if target_loop and target_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._silence_timer(key, user), target_loop
            )
            self._silence_tasks[key] = future

    async def _silence_timer(self, key: Union[int, str], user):
        """Attend la duree de silence puis declenche le traitement."""
        await asyncio.sleep(self.silence_duration)
        buf = self._buffers.get(key)
        if buf and len(buf) > 0:
            audio_data = bytes(buf)
            self._buffers[key] = bytearray()
            logger.info(f"Silence detecte ({len(audio_data)} octets audio capturés pour {user or key})")
            try:
                await self.on_speech(user, audio_data)
            except Exception as e:
                logger.error(f"Erreur dans on_speech pour {user or key}: {e}")

    def cleanup(self):
        for task in self._silence_tasks.values():
            if not task.done():
                task.cancel()
        self._buffers.clear()
        self._silence_tasks.clear()

