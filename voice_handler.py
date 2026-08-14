"""
voice_handler.py — Synthese vocale (TTS) et gestion des connexions vocales Discord
Utilise edge-tts (Microsoft, gratuit) pour generer des voix naturelles en francais
"""
import asyncio
import io
import logging
import os
import tempfile
from typing import Optional

import discord
import edge_tts
from discord.ext import voice_recv

import config

logger = logging.getLogger("welcome_bot")


class VoiceHandler:
    """
    Gere la connexion vocale du bot et la file d'attente TTS.
    Une seule instance partagee sur tout le bot.
    """

    def __init__(self):
        # Salon textuel lie au salon vocal (pour renvoyer les transcriptions)
        self.text_channel: Optional[discord.TextChannel] = None
        # File TTS pour eviter les chevauchements
        self._tts_queue: asyncio.Queue = asyncio.Queue()
        self._tts_worker_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Connexion / deconnexion
    # ------------------------------------------------------------------

    async def join(self, member: discord.Member, text_channel: discord.TextChannel) -> bool:
        """Fait rejoindre le bot au salon vocal du membre."""
        if not config.VOICE_ENABLED:
            await text_channel.send("La fonctionnalite vocale est desactivee (`VOICE_ENABLED=false`).")
            return False

        if not member.voice or not member.voice.channel:
            await text_channel.send(f"Tu dois etre dans un salon vocal pour que je te rejoigne, {member.mention} !")
            return False

        voice_channel = member.voice.channel

        # Si deja connecte a un autre salon, se deplacer
        if member.guild.voice_client:
            if member.guild.voice_client.channel == voice_channel:
                await text_channel.send(f"Je suis deja dans **{voice_channel.name}** !")
                return True
            await member.guild.voice_client.disconnect()

        try:
            await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
            self.text_channel = text_channel
            logger.info(f"Bot connecte au salon vocal: {voice_channel.name}")
            await text_channel.send(f"Je rejoins **{voice_channel.name}** ! Parlez, je vous ecoute.")
            # Demarrer le worker TTS si besoin
            self._ensure_tts_worker()
            return True
        except discord.ClientException as e:
            logger.error(f"Erreur de connexion vocale: {e}")
            await text_channel.send(f"Impossible de rejoindre le salon vocal : {e}")
            return False

    async def leave(self, guild: discord.Guild, text_channel: discord.TextChannel):
        """Deconnecte le bot du salon vocal."""
        vc = guild.voice_client
        if vc:
            await vc.disconnect()
            self.text_channel = None
            await text_channel.send("Bonne conversation ! Je quitte le salon vocal.")
            logger.info("Bot deconnecte du salon vocal.")
        else:
            await text_channel.send("Je ne suis dans aucun salon vocal.")

    # ------------------------------------------------------------------
    # TTS — Synthese vocale
    # ------------------------------------------------------------------

    def _ensure_tts_worker(self):
        """Demarre le worker de file TTS s'il n'est pas deja actif."""
        if self._tts_worker_task is None or self._tts_worker_task.done():
            self._tts_worker_task = asyncio.create_task(self._tts_worker())

    async def speak(self, text: str, voice_client: discord.VoiceClient):
        """Ajoute un texte a la file TTS pour lecture sequentielle."""
        # Nettoie les mentions Discord et les emojis pour la voix
        clean = self._clean_text_for_tts(text)
        if not clean.strip():
            return
        await self._tts_queue.put((clean, voice_client))
        self._ensure_tts_worker()

    async def _tts_worker(self):
        """Worker qui vide la file TTS une entree a la fois."""
        while True:
            try:
                text, voice_client = await asyncio.wait_for(
                    self._tts_queue.get(), timeout=300.0
                )
                await self._play_tts(text, voice_client)
                self._tts_queue.task_done()
            except asyncio.TimeoutError:
                # Inactivite longue, on arrete le worker
                logger.debug("Worker TTS inactif, arret.")
                break
            except Exception as e:
                logger.error(f"Erreur dans le worker TTS: {e}")

    async def _play_tts(self, text: str, voice_client: discord.VoiceClient):
        """Genere le MP3 via edge-tts et le joue dans le salon vocal."""
        if not voice_client or not voice_client.is_connected():
            logger.warning("VoiceClient non connecte, TTS annule.")
            return

        tmp_path = None
        try:
            # Creer un fichier temporaire pour l'audio
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name

            communicate = edge_tts.Communicate(text=text, voice=config.VOICE_TTS_VOICE)
            await communicate.save(tmp_path)

            # Attendre si le bot parle deja
            while voice_client.is_playing():
                await asyncio.sleep(0.1)

            # Jouer le MP3
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            audio_source = discord.FFmpegPCMAudio(tmp_path, executable=ffmpeg_exe)
            done_event = asyncio.Event()

            def after_play(error):
                if error:
                    logger.error(f"Erreur lecture audio: {error}")
                done_event.set()

            voice_client.play(audio_source, after=after_play)
            await done_event.wait()

        except Exception as e:
            logger.error(f"Erreur TTS: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _clean_text_for_tts(text: str) -> str:
        """Nettoie le texte pour la synthese vocale (supprime mentions, emojis markdown)."""
        import re
        # Supprime les mentions Discord <@123456>
        text = re.sub(r"<@!?\d+>", "", text)
        # Supprime les canaux <#123456>
        text = re.sub(r"<#\d+>", "", text)
        # Supprime le formatage markdown de base
        text = re.sub(r"\*{1,3}|_{1,3}|`{1,3}", "", text)
        # Supprime les URLs
        text = re.sub(r"https?://\S+", "lien URL", text)
        # Nettoie les espaces multiples
        text = re.sub(r"\s+", " ", text).strip()
        # Limite a 500 caracteres pour la voix
        if len(text) > 500:
            text = text[:497] + "..."
        return text
