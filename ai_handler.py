import logging
import asyncio
from typing import List, Dict, Optional
import aiohttp

import config

logger = logging.getLogger("welcome_bot")

# Tente d'importer le SDK officiel Groq
try:
    from groq import AsyncGroq
    HAS_GROQ_SDK = True
except ImportError:
    HAS_GROQ_SDK = False


class AIHandler:
    """
    Gère les appels à l'API GRATUITE Groq (ex: llama-3.3-70b-versatile ou llama3-8b-8192)
    et conserve l'historique de conversation en mémoire pour chaque salon Discord.
    """

    def __init__(self):
        self.api_key = config.GROQ_API_KEY
        if not self.api_key:
            logger.warning(
                "⚠️ GROQ_API_KEY n'est pas définie. "
                "Obtenez une clé GRATUITE sur https://console.groq.com/keys et ajoutez-la dans votre .env"
            )
            self.client = None
        elif HAS_GROQ_SDK:
            try:
                self.client = AsyncGroq(api_key=self.api_key)
            except Exception as e:
                logger.error(f"Erreur lors de l'initialisation du SDK Groq : {e}")
                self.client = None
        else:
            self.client = None

        # Historique par salon: {channel_id: [{"role": "user"/"assistant", "content": "..."}]}
        self.conversations: Dict[int, List[Dict[str, str]]] = {}

    def reset_history(self, channel_id: int) -> bool:
        """
        Efface l'historique des échanges pour un salon donné.
        """
        if channel_id in self.conversations:
            del self.conversations[channel_id]
            logger.info(f"Historique IA réinitialisé pour le salon {channel_id}.")
            return True
        return False

    def _trim_history(self, channel_id: int):
        """
        Conserve uniquement les (AI_HISTORY_LENGTH * 2) derniers messages dans l'historique.
        """
        max_messages = max(1, config.AI_HISTORY_LENGTH) * 2
        if channel_id in self.conversations:
            if len(self.conversations[channel_id]) > max_messages:
                self.conversations[channel_id] = self.conversations[channel_id][-max_messages:]

    async def generate_response(self, channel_id: int, user_prompt: str) -> str:
        """
        Génère une réponse avec l'API Groq gratuite.
        """
        if not self.api_key:
            return (
                "❌ L'API Groq n'est pas configurée.\n"
                "💡 **Solution 100% gratuite** : Obtenez votre clé sur https://console.groq.com/keys "
                "et ajoutez `GROQ_API_KEY=gsk_...` dans votre fichier `.env`."
            )

        if not user_prompt.strip():
            return "Veuillez poser une question ou fournir un texte."

        # Initialisation de l'historique
        if channel_id not in self.conversations:
            self.conversations[channel_id] = []

        self._trim_history(channel_id)

        # Construction des messages pour Groq (format compatible OpenAI)
        history = list(self.conversations[channel_id])
        messages_payload = [{"role": "system", "content": config.AI_SYSTEM_PROMPT}]
        messages_payload.extend(history)
        current_user_msg = {"role": "user", "content": user_prompt}
        messages_payload.append(current_user_msg)

        try:
            assistant_reply = ""

            # Utilisation du SDK officiel s'il est disponible
            if self.client and HAS_GROQ_SDK:
                completion = await self.client.chat.completions.create(
                    model=config.AI_MODEL,
                    messages=messages_payload,
                    max_tokens=config.AI_MAX_TOKENS,
                    timeout=30.0,
                )
                assistant_reply = completion.choices[0].message.content or ""
            else:
                # Fallback HTTP direct REST API de Groq
                assistant_reply = await self._call_groq_rest_api(messages_payload)

            if not assistant_reply:
                assistant_reply = "Désolé, je n'ai pas pu obtenir de réponse."

            # Mise à jour de l'historique
            self.conversations[channel_id].append(current_user_msg)
            self.conversations[channel_id].append({"role": "assistant", "content": assistant_reply})
            self._trim_history(channel_id)

            return assistant_reply

        except Exception as e:
            logger.error(f"Erreur lors de l'appel à l'API Groq : {e}", exc_info=True)
            return f"⚠️ Une erreur est survenue lors du traitement par l'IA : {e}"

    async def _call_groq_rest_api(self, messages: list) -> str:
        """
        Appel de secours via l'API REST de Groq en aiohttp.
        """
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.AI_MODEL,
            "messages": messages,
            "max_tokens": config.AI_MAX_TOKENS,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    try:
                        return data["choices"][0]["message"]["content"]
                    except (KeyError, IndexError):
                        return "Erreur lors de la lecture de la réponse de l'API."
                else:
                    error_text = await resp.text()
                    logger.error(f"Erreur REST Groq HTTP {resp.status}: {error_text}")
                    return f"⚠️ Erreur API Groq (HTTP {resp.status}). Vérifiez votre clé API sur https://console.groq.com/keys."


def split_message(text: str, limit: int = 1950) -> List[str]:
    """
    Découpe un texte long en blocs de longueur maximale `limit` (défaut 1950 pour garder de la marge sous les 2000 car. Discord).
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    current_text = text

    while len(current_text) > limit:
        cut_index = current_text.rfind("\n", 0, limit)
        if cut_index == -1 or cut_index < limit // 2:
            cut_index = current_text.rfind(" ", 0, limit)

        if cut_index == -1 or cut_index == 0:
            cut_index = limit

        chunks.append(current_text[:cut_index].strip())
        current_text = current_text[cut_index:].strip()

    if current_text:
        chunks.append(current_text)

    return chunks
