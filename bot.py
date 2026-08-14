import asyncio
import os
import logging
from typing import Optional
from aiohttp import web
import discord
from discord.ext import commands

import config
from image_generator import fetch_avatar_bytes, build_welcome_banner
from ai_handler import AIHandler, split_message
from voice_handler import VoiceHandler
from speech_handler import transcribe_audio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("welcome_bot")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)
ai_handler = AIHandler()
voice_handler = VoiceHandler()


@bot.event
async def on_ready():
    logger.info(f"Connecté en tant que {bot.user} (ID: {bot.user.id}) - Version Discord: {getattr(discord, '__version__', 'inconnue')}")
    if not hasattr(discord.VoiceClient, "start_recording"):
        logger.warning("ATTENTION: discord.VoiceClient n'a pas start_recording ! L'ancienne librairie discord.py est encore dans le cache de Render.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if config.AI_ENABLED_ON_MENTION and bot.user in message.mentions:
        cleaned_prompt = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if cleaned_prompt:
            async with message.channel.typing():
                response = await ai_handler.generate_response(message.channel.id, cleaned_prompt)
                for chunk in split_message(response):
                    await message.channel.send(chunk)
        else:
            await message.channel.send("Bonjour ! Comment puis-je vous aider aujourd'hui ?")

    await bot.process_commands(message)


@bot.command(name="ask")
async def ask_cmd(ctx: commands.Context, *, question: str):
    """Pose une question à l'assistant IA Claude."""
    if not config.AI_ENABLED_ON_COMMAND:
        await ctx.send("⚠️ La commande `!ask` est actuellement désactivée.")
        return

    async with ctx.typing():
        response = await ai_handler.generate_response(ctx.channel.id, question)
        for chunk in split_message(response):
            await ctx.send(chunk)


@bot.command(name="reset")
async def reset_cmd(ctx: commands.Context):
    """Réinitialise l'historique de conversation du salon courant."""
    if ai_handler.reset_history(ctx.channel.id):
        await ctx.send("🧹 L'historique de conversation de ce salon a été réinitialisé.")
    else:
        await ctx.send("Aucun historique de conversation à réinitialiser dans ce salon.")


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild

    if guild.id != config.GUILD_ID:
        return

    role_ids_to_add = [
        rid for rid in (config.WELCOME_ROLE_ID, config.WELCOME_ROLE_ID_2) if rid != 0
    ]
    roles_to_add = []

    for rid in role_ids_to_add:
        role = guild.get_role(rid)
        if role is not None:
            roles_to_add.append(role)
        else:
            logger.warning(f"Rôle ID={rid} introuvable sur le serveur.")

    if roles_to_add:
        try:
            await member.add_roles(*roles_to_add, reason="Rôles automatiques à l'arrivée")
        except discord.Forbidden:
            logger.error(
                f"Permissions insuffisantes pour attribuer un ou plusieurs rôles à {member}."
            )
        except discord.HTTPException as e:
            logger.error(f"Erreur HTTP lors de l'attribution des rôles : {e}")

    channel = guild.get_channel(config.WELCOME_CHANNEL_ID)
    if channel is None:
        logger.warning(f"WELCOME_CHANNEL_ID={config.WELCOME_CHANNEL_ID} introuvable.")
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
        file = discord.File(fp=banner_buffer, filename="welcome.png")

        message = config.WELCOME_MESSAGE_TEMPLATE.format(
            member=member.mention,
            guild=guild.name,
            count=guild.member_count,
        )

        await channel.send(content=message, file=file)

    except Exception as e:
        logger.error(f"Erreur lors de la génération/envoi de la bannière : {e}")
        await channel.send(
            f"Bienvenue {member.mention} sur **{guild.name}** ! "
            f"Tu es le membre n°{guild.member_count}."
        )


# ---------------------------------------------------------------------------
# Commandes vocales (Py-cord DAVE compatible)
# ---------------------------------------------------------------------------

@bot.command(name="join")
async def join_cmd(ctx: commands.Context):
    """Le bot rejoint ton salon vocal et commence a ecouter."""
    if not config.VOICE_ENABLED:
        await ctx.send("La fonctionnalite vocale est desactivee.")
        return

    joined = await voice_handler.join(ctx.author, ctx.channel)
    if joined:
        vc = ctx.guild.voice_client
        if vc:
            if not hasattr(vc, "start_recording"):
                await ctx.send("⚠️ **Render utilise encore l'ancien cache.** Va sur Render -> bouton **Manual Deploy** -> clique sur **Clear build cache & deploy** !")
                logger.error("start_recording introuvable sur VoiceClient (cache Render périmé)")
                return

            try:
                if getattr(vc, "recording", False):
                    vc.stop_recording()

                vc.start_recording(
                    discord.sinks.WaveSink(),
                    on_recording_finished,
                    ctx.channel,
                    vc,
                )
                logger.info("Enregistrement vocal Py-cord DAVE demarre.")
                await ctx.send("🎙️ **J'écoute le salon vocal !** Parlez, puis faites `!stop` (ou `!leave`) quand vous avez fini pour que je réponde.")
            except Exception as e:
                logger.error(f"Erreur lors de start_recording: {e}", exc_info=True)
                await ctx.send(f"⚠️ Erreur démarrage écoute : `{e}`")


@bot.command(name="stop")
async def stop_cmd(ctx: commands.Context):
    """Arrete l'enregistrement vocal et traite ce qui a ete dit."""
    vc = ctx.guild.voice_client
    if vc and getattr(vc, "recording", False):
        vc.stop_recording()
        await ctx.send("⏹️ Traitement de votre message vocal en cours...")
    else:
        await ctx.send("Aucun enregistrement vocal en cours.")


async def on_recording_finished(sink: discord.sinks.Sink, text_channel: discord.TextChannel, voice_client: discord.VoiceClient, *args):
    """Callback execute quand l'enregistrement vocal se termine."""
    if not config.GROQ_API_KEY:
        await text_channel.send("⚠️ `GROQ_API_KEY` est manquante dans les variables d'environnement sur Render !")
        return

    for user_id, audio in sink.audio_data.items():
        member = text_channel.guild.get_member(user_id)
        if member and member.bot:
            continue

        wav_bytes = audio.file.read()
        if len(wav_bytes) < 4000:
            continue

        logger.info(f"Traitement de {len(wav_bytes)} octets audio pour user_id={user_id}...")

        transcript = await transcribe_audio(wav_bytes)
        if not transcript:
            continue

        display_name = member.display_name if member else f"Membre {user_id}"
        await text_channel.send(f"🎙️ **{display_name}** : {transcript}")

        async with text_channel.typing():
            response = await ai_handler.generate_response(text_channel.id, transcript)

        for chunk in split_message(response):
            await text_channel.send(chunk)

        await voice_handler.speak(response, voice_client)


@bot.command(name="leave")
async def leave_cmd(ctx: commands.Context):
    """Le bot quitte le salon vocal."""
    vc = ctx.guild.voice_client
    if vc:
        if getattr(vc, "recording", False):
            vc.stop_recording()
    await voice_handler.leave(ctx.guild, ctx.channel)


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
    logger.info(f"Serveur HTTP de health check démarré sur le port {port}")


async def main():
    async with bot:
        await asyncio.gather(
            run_web_server(),
            bot.start(config.BOT_TOKEN),
        )


if __name__ == "__main__":
    asyncio.run(main())
