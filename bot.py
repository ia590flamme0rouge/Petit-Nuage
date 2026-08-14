"""
bot.py — Bot Discord principal (Disnake + discord-ext-voice-recv, DAVE-compatible)
"""
import asyncio
import logging
import os
from typing import Optional

import disnake
from disnake.ext import commands
from aiohttp import web
from discord.ext import voice_recv

import config
from image_generator import fetch_avatar_bytes, build_welcome_banner
from ai_handler import AIHandler, split_message
from voice_handler import VoiceHandler
from speech_handler import transcribe_audio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("welcome_bot")

intents = disnake.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)
ai_handler = AIHandler()
voice_handler = VoiceHandler()


# ---------------------------------------------------------------------------
# Evenements
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    logger.info(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: disnake.Message):
    if message.author.bot:
        return

    if config.AI_ENABLED_ON_MENTION and bot.user in message.mentions:
        cleaned_prompt = (
            message.content
            .replace(f"<@{bot.user.id}>", "")
            .replace(f"<@!{bot.user.id}>", "")
            .strip()
        )
        if cleaned_prompt:
            async with message.channel.typing():
                response = await ai_handler.generate_response(message.channel.id, cleaned_prompt)
                for chunk in split_message(response):
                    await message.channel.send(chunk)
        else:
            await message.channel.send("Bonjour ! Comment puis-je vous aider ?")

    await bot.process_commands(message)


@bot.event
async def on_member_join(member: disnake.Member):
    guild = member.guild
    if guild.id != config.GUILD_ID:
        return

    role_ids_to_add = [
        rid for rid in (config.WELCOME_ROLE_ID, config.WELCOME_ROLE_ID_2) if rid != 0
    ]
    roles_to_add = [r for rid in role_ids_to_add if (r := guild.get_role(rid))]
    if roles_to_add:
        try:
            await member.add_roles(*roles_to_add, reason="Rôles automatiques à l'arrivée")
        except (disnake.Forbidden, disnake.HTTPException) as e:
            logger.error(f"Erreur attribution rôles : {e}")

    channel = guild.get_channel(config.WELCOME_CHANNEL_ID)
    if channel is None:
        return

    try:
        avatar_bytes = await fetch_avatar_bytes(member)
        banner_buffer = build_welcome_banner(
            avatar_bytes=avatar_bytes,
            username=member.display_name,
            member_count=guild.member_count,
            background_path=config.BACKGROUND_PATH,
            font_bold_path=config.FONT_PATH_BOLD,
            font_regular_path=config.FONT_PATH_REGULAR,
            width=config.BANNER_WIDTH,
            height=config.BANNER_HEIGHT,
            avatar_size=config.AVATAR_SIZE,
        )
        file = disnake.File(fp=banner_buffer, filename="welcome.png")
        msg = config.WELCOME_MESSAGE_TEMPLATE.format(
            member=member.mention, guild=guild.name, count=guild.member_count
        )
        await channel.send(content=msg, file=file)
    except Exception as e:
        logger.error(f"Erreur bannière : {e}")
        await channel.send(
            f"Bienvenue {member.mention} sur **{guild.name}** ! Tu es le membre n°{guild.member_count}."
        )


# ---------------------------------------------------------------------------
# Commandes texte
# ---------------------------------------------------------------------------

@bot.command(name="ask")
async def ask_cmd(ctx: commands.Context, *, question: str):
    """Pose une question à l'IA."""
    if not config.AI_ENABLED_ON_COMMAND:
        await ctx.send("⚠️ La commande `!ask` est désactivée.")
        return
    async with ctx.typing():
        response = await ai_handler.generate_response(ctx.channel.id, question)
        for chunk in split_message(response):
            await ctx.send(chunk)


@bot.command(name="reset")
async def reset_cmd(ctx: commands.Context):
    """Réinitialise l'historique de conversation."""
    if ai_handler.reset_history(ctx.channel.id):
        await ctx.send("🧹 Historique réinitialisé.")
    else:
        await ctx.send("Aucun historique à réinitialiser.")


# ---------------------------------------------------------------------------
# Commandes vocales — Disnake + voice_recv (DAVE-compatible)
# ---------------------------------------------------------------------------

class SpeechSink(voice_recv.AudioSink):
    """
    Sink audio qui accumule le PCM de chaque utilisateur et
    déclenche la transcription + réponse IA après un silence.
    """

    def __init__(self, text_channel: disnake.TextChannel, vh: VoiceHandler):
        super().__init__()
        self.text_channel = text_channel
        self.vh = vh
        self._buffers: dict[int, bytearray] = {}
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._loop = asyncio.get_event_loop()

    def wants_opus(self) -> bool:
        return False  # on veut du PCM décodé

    def write(self, user, data: voice_recv.VoiceData):
        if user is None or user.bot:
            return
        uid = user.id
        if uid not in self._buffers:
            self._buffers[uid] = bytearray()
        self._buffers[uid].extend(data.pcm)

        # Reset le timer de silence
        if uid in self._timers:
            self._timers[uid].cancel()
        self._timers[uid] = self._loop.call_later(
            config.VOICE_SILENCE_DURATION,
            lambda u=user, i=uid: asyncio.ensure_future(self._on_silence(u, i))
        )

    async def _on_silence(self, user: disnake.Member, uid: int):
        buf = self._buffers.pop(uid, bytearray())
        self._timers.pop(uid, None)
        if len(buf) < 8000:  # trop court, on ignore
            return

        logger.info(f"Audio reçu de {user.display_name} : {len(buf)} octets PCM")
        transcript = await transcribe_audio(bytes(buf))
        if not transcript:
            return

        await self.text_channel.send(f"🎙️ **{user.display_name}** : {transcript}")

        async with self.text_channel.typing():
            response = await ai_handler.generate_response(self.text_channel.id, transcript)

        for chunk in split_message(response):
            await self.text_channel.send(chunk)

        vc = self.vh.voice_client
        if vc:
            await self.vh.speak(response, vc)

    def cleanup(self):
        for t in self._timers.values():
            t.cancel()
        self._buffers.clear()
        self._timers.clear()


@bot.command(name="join")
async def join_cmd(ctx: commands.Context):
    """Le bot rejoint le salon vocal et écoute en continu."""
    if not config.VOICE_ENABLED:
        await ctx.send("La fonctionnalité vocale est désactivée.")
        return

    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send(f"Tu dois être dans un salon vocal, {ctx.author.mention} !")
        return

    channel = ctx.author.voice.channel

    # Se déplacer si déjà connecté ailleurs
    if ctx.guild.voice_client:
        if ctx.guild.voice_client.channel == channel:
            await ctx.send(f"Je suis déjà dans **{channel.name}** !")
            return
        await ctx.guild.voice_client.disconnect()

    try:
        vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        voice_handler.voice_client = vc
        voice_handler.text_channel = ctx.channel
        voice_handler._ensure_tts_worker()

        sink = SpeechSink(ctx.channel, voice_handler)
        vc.listen(sink)

        logger.info(f"Connecté à {channel.name} avec voice_recv")
        await ctx.send(
            f"🎙️ **J'écoute {channel.name} en continu !**\n"
            f"Parlez directement — je vous répondrai automatiquement après votre phrase.\n"
            f"Tapez `!leave` pour que je parte."
        )
    except Exception as e:
        logger.error(f"Erreur connexion vocale : {e}", exc_info=True)
        await ctx.send(f"⚠️ Impossible de rejoindre le salon vocal : `{e}`")


@bot.command(name="leave")
async def leave_cmd(ctx: commands.Context):
    """Le bot quitte le salon vocal."""
    vc = ctx.guild.voice_client
    if vc:
        if hasattr(vc, 'stop_listening'):
            vc.stop_listening()
        await vc.disconnect()
        voice_handler.voice_client = None
        voice_handler.text_channel = None
        await ctx.send("👋 Bonne conversation ! J'ai quitté le salon vocal.")
    else:
        await ctx.send("Je ne suis dans aucun salon vocal.")


# ---------------------------------------------------------------------------
# Serveur HTTP pour Render health check
# ---------------------------------------------------------------------------

async def health_check(request):
    return web.Response(text="OK")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Serveur HTTP démarré sur le port {port}")


async def main():
    await asyncio.gather(
        run_web_server(),
        bot.start(config.BOT_TOKEN),
    )


if __name__ == "__main__":
    asyncio.run(main())