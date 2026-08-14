import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", 0))
WELCOME_ROLE_ID = int(os.getenv("WELCOME_ROLE_ID_1", 0))
WELCOME_ROLE_ID_2 = int(os.getenv("WELCOME_ROLE_ID_2", 0))
# Personnalisation de la bannière
BANNER_WIDTH = 900
BANNER_HEIGHT = 300
AVATAR_SIZE = 180
BACKGROUND_PATH = "assets/background.png"
FONT_PATH_BOLD = "assets/font_bold.ttf"   # à fournir, voir notes en bas
FONT_PATH_REGULAR = "assets/font_regular.ttf"

WELCOME_MESSAGE_TEMPLATE = "Bienvenue {member} sur **{guild}** ! Tu es le membre n°{count}."

# --- Configuration IA (Groq - Gratuit, 14 400 req/jour) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
AI_ENABLED_ON_MENTION = os.getenv("AI_ENABLED_ON_MENTION", "true").lower() in ("true", "1", "yes")
AI_ENABLED_ON_COMMAND = os.getenv("AI_ENABLED_ON_COMMAND", "true").lower() in ("true", "1", "yes")
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", 800))
AI_HISTORY_LENGTH = int(os.getenv("AI_HISTORY_LENGTH", 5))
AI_SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT",
    "Tu es un assistant utile et concis sur ce serveur Discord."
)
AI_MODEL = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")

# --- Configuration Voix ---
VOICE_ENABLED = os.getenv("VOICE_ENABLED", "true").lower() in ("true", "1", "yes")
VOICE_TTS_VOICE = os.getenv("VOICE_TTS_VOICE", "fr-FR-HenriNeural")  # Voix fr masculine grave et naturelle
VOICE_TEXT_CHANNEL_ID = int(os.getenv("VOICE_TEXT_CHANNEL_ID", 0))  # Salon où envoyer les transcriptions (0 = même salon que la commande !join)
VOICE_SILENCE_DURATION = float(os.getenv("VOICE_SILENCE_DURATION", "1.5"))  # Secondes de silence avant traitement

