"""
voice_handler.py — Synthèse vocale TTS et gestion des connexions vocales
Utilise edge-tts (Microsoft, gratuit) pour des voix naturelles en français
Compatible disnake
"""
import asyncio
import logging
import os
import tempfile
from typing import Optional

import disnake
import edge_tts
import re

import config

logger = logging.getLogger("welcome_bot")


class VoiceHandler:
    """
    Gère la connexion vocale du bot et la file d'attente TTS.
    Une seule instance partagée sur tout le bot.
    """

    def __init__(self):
        self.text_channel: Optional[disnake.TextChannel] = None
        self.voice_client: Optional[disnake.VoiceClient] = None
        self._tts_queue: asyncio.Queue = asyncio.Queue()
        self._tts_worker_task: Optional[asyncio.Task] = None

    def _ensure_tts_worker(self):
        """Démarre le worker TTS s'il n'est pas actif."""
        if self._tts_worker_task is None or self._tts_worker_task.done():
            self._tts_worker_task = asyncio.create_task(self._tts_worker())

    async def speak(self, text: str, voice_client: disnake.VoiceClient):
        """Ajoute un texte à la file TTS."""
        clean = self._clean_text_for_tts(text)
        if not clean.strip():
            return
        await self._tts_queue.put((clean, voice_client))
        self._ensure_tts_worker()

    async def _tts_worker(self):
        """Worker qui vide la file TTS une entrée à la fois."""
        while True:
            try:
                text, voice_client = await asyncio.wait_for(
                    self._tts_queue.get(), timeout=300.0
                )
                await self._play_tts(text, voice_client)
                self._tts_queue.task_done()
            except asyncio.TimeoutError:
                logger.debug("Worker TTS inactif, arrêt.")
                break
            except Exception as e:
                logger.error(f"Erreur dans le worker TTS : {e}")

    async def _play_tts(self, text: str, voice_client: disnake.VoiceClient):
        """Génère le MP3 via edge-tts et le joue dans le salon vocal."""
        if not voice_client or not voice_client.is_connected():
            logger.warning("VoiceClient non connecté, TTS annulé.")
            return

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name

            communicate = edge_tts.Communicate(text=text, voice=config.VOICE_TTS_VOICE)
            await communicate.save(tmp_path)

            # Attendre si le bot parle déjà
            while voice_client.is_playing():
                await asyncio.sleep(0.1)

            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            audio_source = disnake.FFmpegPCMAudio(tmp_path, executable=ffmpeg_exe)
            done_event = asyncio.Event()

            def after_play(error):
                if error:
                    logger.error(f"Erreur lecture audio : {error}")
                done_event.set()

            logger.info(f"🔊 Lecture TTS : {text[:60]}...")
            voice_client.play(audio_source, after=after_play)
            await done_event.wait()
            logger.info("Fin de lecture TTS.")

        except Exception as e:
            logger.error(f"Erreur TTS : {e}")
            if self.text_channel:
                await self.text_channel.send(f"⚠️ Erreur lecture vocale : `{e}`")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _clean_text_for_tts(text: str) -> str:
        """Nettoie le texte pour la synthèse vocale."""
        text = re.sub(r"<@!?\d+>", "", text)
        text = re.sub(r"<#\d+>", "", text)
        text = re.sub(r"\*{1,3}|_{1,3}|`{1,3}", "", text)
        text = re.sub(r"https?://\S+", "lien URL", text)
        return text.strip()
