import logging
import discord
from discord.ext import commands
from discord.ext import voice_recv

import config
from image_generator import fetch_avatar_bytes, build_welcome_banner
from ai_handler import AIHandler, split_message
from voice_handler import VoiceHandler
from speech_handler import VoiceSink, transcribe_audio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("welcome_bot")

intents = discord.Intents.default()
intents.members = True  # OBLIGATOIRE pour détecter les arrivées, à activer aussi dans le portail dev
intents.message_content = True  # OBLIGATOIRE pour lire le contenu des messages et mentions

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    voice_cls=voice_recv.VoiceRecvClient,  # Permet la reception audio
)
ai_handler = AIHandler()
voice_handler = VoiceHandler()


@bot.event
async def on_ready():
    logger.info(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    # Toujours ignorer les messages provenant de bots (pour éviter les boucles infinies)
    if message.author.bot:
        return

    # Gestion de la réponse IA lors d'une mention directe du bot
    if config.AI_ENABLED_ON_MENTION and bot.user in message.mentions:
        # Extraire la question en supprimant la mention du bot du texte du message
        cleaned_prompt = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()

        if cleaned_prompt:
            async with message.channel.typing():
                response = await ai_handler.generate_response(message.channel.id, cleaned_prompt)
                for chunk in split_message(response):
                    await message.channel.send(chunk)
        else:
            await message.channel.send("Bonjour ! Comment puis-je vous aider aujourd'hui ?")

    # Important : exécuter les commandes préfixées (ex: !ask, !reset)
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

 # --- Attribution des rôles ---
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
                f"Permissions insuffisantes pour attribuer un ou plusieurs rôles à {member}. "
                "Vérifie que le rôle du bot est AU-DESSUS de tous les rôles à attribuer "
                "dans la hiérarchie des rôles du serveur."
            )
        except discord.HTTPException as e:
            logger.error(f"Erreur HTTP lors de l'attribution des rôles : {e}")

    # --- Génération et envoi de la bannière ---
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
        # Fallback texte si l'image plante, pour ne pas silencieusement rater l'accueil
        await channel.send(
            f"Bienvenue {member.mention} sur **{guild.name}** ! "
            f"Tu es le membre n°{guild.member_count}."
        )


# ---------------------------------------------------------------------------
# Commandes vocales
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
        if vc and isinstance(vc, voice_recv.VoiceRecvClient):
            sink = VoiceSink(
                on_speech=lambda user, audio: _handle_speech(user, audio, ctx.channel, vc),
                silence_duration=config.VOICE_SILENCE_DURATION,
            )
            vc.listen(sink)
            logger.info("Ecoute vocale demarree.")


async def _handle_speech(
    user: discord.Member,
    audio_data: bytes,
    text_channel: discord.TextChannel,
    voice_client: voice_recv.VoiceRecvClient,
):
    """
    Pipeline: audio PCM -> Whisper STT -> Groq IA -> edge-tts TTS
    """
    if user.bot:
        return

    # 1. Transcription vocale -> texte
    transcript = await transcribe_audio(audio_data)
    if not transcript:
        return

    logger.info(f"{user.display_name} a dit: {transcript!r}")

    # 2. Affiche la transcription dans le salon texte
    await text_channel.send(f"**{user.display_name}** : {transcript}")

    # 3. Genere la reponse IA
    async with text_channel.typing():
        response = await ai_handler.generate_response(text_channel.id, transcript)

    # 4. Envoie la reponse en texte
    for chunk in split_message(response):
        await text_channel.send(chunk)

    # 5. Lit la reponse a voix haute (TTS)
    await voice_handler.speak(response, voice_client)


@bot.command(name="leave")
async def leave_cmd(ctx: commands.Context):
    """Le bot quitte le salon vocal."""
    vc = ctx.guild.voice_client
    if vc and isinstance(vc, voice_recv.VoiceRecvClient):
        vc.stop_listening()
    await voice_handler.leave(ctx.guild, ctx.channel)


if __name__ == "__main__":
    bot.run(config.BOT_TOKEN)