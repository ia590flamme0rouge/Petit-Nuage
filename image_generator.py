import io
from PIL import Image, ImageDraw, ImageFont, ImageOps
import aiohttp


async def fetch_avatar_bytes(member) -> bytes:
    """Télécharge l'avatar du membre en bytes PNG."""
    asset = member.display_avatar.with_size(256).with_format("png")
    async with aiohttp.ClientSession() as session:
        async with session.get(str(asset.url)) as resp:
            resp.raise_for_status()
            return await resp.read()


def make_circle_avatar(avatar_bytes: bytes, size: int) -> Image.Image:
    """Découpe l'avatar en cercle avec anti-aliasing propre."""
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar = avatar.resize((size, size), Image.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)

    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.paste(avatar, (0, 0), mask)
    return output


def build_welcome_banner(
    avatar_bytes: bytes,
    username: str,
    member_count: int,
    background_path: str,
    font_bold_path: str,
    font_regular_path: str,
    width: int = 900,
    height: int = 300,
    avatar_size: int = 180,
) -> io.BytesIO:
    """
    Génère la bannière de bienvenue complète.
    Retourne un buffer BytesIO prêt à être envoyé en pièce jointe Discord.
    """
    try:
        bg = Image.open(background_path).convert("RGBA")
        bg = ImageOps.fit(bg, (width, height))
    except FileNotFoundError:
        # Fallback si pas de background custom : dégradé simple
        bg = Image.new("RGBA", (width, height), (30, 30, 40, 255))

    # Voile sombre pour que le texte reste lisible peu importe le fond
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 90))
    bg = Image.alpha_composite(bg, overlay)

    draw = ImageDraw.Draw(bg)

    avatar = make_circle_avatar(avatar_bytes, avatar_size)
    avatar_x = 60
    avatar_y = (height - avatar_size) // 2

    # Anneau autour de l'avatar
    ring_padding = 6
    ring_box = (
        avatar_x - ring_padding,
        avatar_y - ring_padding,
        avatar_x + avatar_size + ring_padding,
        avatar_y + avatar_size + ring_padding,
    )
    draw.ellipse(ring_box, outline=(255, 255, 255, 255), width=4)
    bg.paste(avatar, (avatar_x, avatar_y), avatar)

    try:
        font_title = ImageFont.truetype(font_bold_path, 48)
        font_sub = ImageFont.truetype(font_regular_path, 26)
    except OSError:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    text_x = avatar_x + avatar_size + 50
    draw.text((text_x, 90), "BIENVENUE", font=font_title, fill=(255, 255, 255, 255))
    draw.text((text_x, 150), username, font=font_sub, fill=(220, 220, 220, 255))
    draw.text(
        (text_x, 190),
        f"Membre n°{member_count}",
        font=font_sub,
        fill=(180, 180, 180, 255),
    )

    buffer = io.BytesIO()
    bg.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer