import asyncio
import os
import logging
from typing import Optional
from aiohttp import web
import discord
import discord.sinks
from discord.ext import commands

# Patch pour les bugs connus de Pycord 2.8.1 (SinkEventRouter & PacketDecoder)
if not hasattr(discord.sinks.Sink, "__sink_listeners__"):
    discord.sinks.Sink.__sink_listeners__ = []
if not hasattr(discord.sinks.Sink, "walk_children"):
    discord.sinks.Sink.walk_children = lambda self: []
if not hasattr(discord.sinks.Sink, "is_opus"):
    discord.sinks.Sink.is_opus = lambda self: False
if not hasattr(discord.sinks.Sink, "wants_opus"):
    discord.sinks.Sink.wants_opus = lambda self: False

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
            try:
                try:
                    vc.stop_recording()
                except Exception:
                    pass

                vc.start_recording(
                    discord.sinks.WaveSink(),
                    on_recording_finished,
                )
                logger.info("Enregistrement vocal Py-cord DAVE demarre.")
                await ctx.send("🎙️ **J'écoute le salon vocal !** Parlez, puis faites `!stop` quand vous avez fini pour que je réponde.")
            except Exception as e:
                logger.error(f"Erreur lors de start_recording: {e}", exc_info=True)
                await ctx.send(f"⚠️ Erreur démarrage écoute : `{e}`")


@bot.command(name="stop")
async def stop_cmd(ctx: commands.Context):
    """Arrete l'enregistrement vocal et traite ce qui a ete dit."""
    vc = ctx.guild.voice_client
    if vc:
        try:
            vc.stop_recording()
            await ctx.send("⏹️ Traitement de votre message vocal en cours...")
        except Exception as e:
            logger.warning(f"Stop recording exception: {e}")
            await ctx.send("⚠️ Aucun enregistrement en cours ou déjà arrêté.")
    else:
        await ctx.send("Le bot n'est pas dans un salon vocal.")


async def on_recording_finished(sink: discord.sinks.Sink, *args):
    """Callback execute quand l'enregistrement vocal se termine."""
    voice_client = getattr(sink, "vc", None) or (args[0] if args else None)
    text_channel = voice_handler.text_channel

    if not text_channel:
        logger.error("Salon textuel introuvable pour la réponse.")
        return

    if not config.GROQ_API_KEY:
        await text_channel.send("⚠️ `GROQ_API_KEY` est manquante dans les variables d'environnement sur Render !")
        return

    processed = False
    for user_id, audio in sink.audio_data.items():
        guild = text_channel.guild if text_channel else None
        member = guild.get_member(user_id) if guild else None
        if member and member.bot:
            continue

        wav_bytes = audio.file.read()
        if len(wav_bytes) < 4000:
            continue

        processed = True
        logger.info(f"Traitement de {len(wav_bytes)} octets audio pour user_id={user_id}...")

        transcript = await transcribe_audio(wav_bytes)
        if not transcript:
            await text_channel.send("🔇 Audio capturé mais aucun mot n'a pu être transcrit.")
            continue

        display_name = member.display_name if member else f"Membre {user_id}"
        await text_channel.send(f"🎙️ **{display_name}** : {transcript}")

        async with text_channel.typing():
            response = await ai_handler.generate_response(text_channel.id, transcript)

        for chunk in split_message(response):
            await text_channel.send(chunk)

        if voice_client:
            await voice_handler.speak(response, voice_client)

    if not processed:
        await text_channel.send("⚠️ Aucun audio capturé. Assurez-vous de parler dans votre micro avant de faire `!stop` !")

    # Relance l'écoute automatiquement si le bot est toujours dans le vocal
    if voice_client and voice_client.is_connected():
        try:
            voice_client.start_recording(
                discord.sinks.WaveSink(),
                on_recording_finished,
            )
            await text_channel.send("🎙️ *Écoute relancée !* Parlez puis faites `!stop` quand vous souhaitez une réponse.")
        except Exception as e:
            logger.error(f"Erreur relance enregistrement: {e}")


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
