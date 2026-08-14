from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from datetime import datetime
import os

ROOT = Path(__file__).parent
OUT = ROOT / "assets" / "generated"
OUT.mkdir(parents=True, exist_ok=True)
CARD_DIR = ROOT / "assets" / "cards"
SUIT_NAMES = {"S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}
SUIT_SYMBOLS = {"S": "\u2660", "H": "\u2665", "D": "\u2666", "C": "\u2663"}

def font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for name in names:
        if os.path.exists(name): return ImageFont.truetype(name, size)
    # Railway's Linux image does not guarantee system fonts. Pillow's scalable
    # built-in fallback preserves the requested size instead of tiny text.
    return ImageFont.load_default(size=size)

def center(draw, image, y, text, size, color, bold=False):
    f = font(size, bold); box = draw.textbbox((0, 0), text, font=f)
    draw.text(((image.width - (box[2] - box[0])) / 2, y), text, font=f, fill=color)

def save(im, name):
    path = OUT / name; im.save(path, "PNG"); return path

def balance_card(name, uid, points):
    im = Image.new("RGB", (800, 500), "#101c2d"); d = ImageDraw.Draw(im)
    d.ellipse((57, 47, 193, 183), fill="#4285ff"); d.ellipse((61, 51, 189, 179), fill="#1a2d47")
    d.text((229, 84), name[:20], font=font(45, True), fill="#e6eefb")
    d.text((229, 145), f"ID: {uid}", font=font(21), fill="#8ea4c3")
    center(d, im, 225, "POINTS BALANCE", 25, "#8497b2", True)
    center(d, im, 262, f"{points:,.2f}", 92, "#4285ff", True)
    center(d, im, 370, f"{float(points) * .0001:.4f} LTC", 25, "#c0cee2")
    d.text((59, 456), "LiteBet Casino", font=font(21, True), fill="#8497b2")
    d.text((510, 456), datetime.now().strftime("%b %d, %Y, %I:%M %p"), font=font(17, True), fill="#8497b2")
    return save(im, f"balance_{uid}.png")

def limbo_card(crashed):
    im = Image.new("RGB", (700, 300), "#111827"); d = ImageDraw.Draw(im)
    center(d, im, 14, "CRASHED AT", 22, "#93a4be", True)
    center(d, im, 43, f"{crashed:.2f}x", 58, "#ff5d6c", True)
    return save(im, "limbo.png")

def coinflip_card(landed):
    color = "#264653" if landed == "heads" else "#5c2a42"
    im = Image.new("RGB", (700, 300), color); d = ImageDraw.Draw(im)
    d.ellipse((230, 25, 470, 265), fill="#f4c95d", outline="#fff1b8", width=8)
    center(d, im, 98, landed.upper(), 38, "#29200c", True)
    return save(im, f"coinflip_{landed}.png")

def card_image(rank, suit, size=(255, 385)):
    asset = CARD_DIR / f"{rank}_of_{SUIT_NAMES[suit]}.png"
    if asset.exists(): return Image.open(asset).convert("RGB").resize(size)
    im = Image.new("RGB", size, "#f8fafc"); d = ImageDraw.Draw(im)
    d.text((25, 25), rank + SUIT_SYMBOLS[suit], font=font(48, True), fill="#bd2438" if suit in "HD" else "#152033")
    return im

def blackjack_card(username, player, dealer):
    im = Image.new("RGB", (1200, 1284), "#003b08"); d = ImageDraw.Draw(im)
    d.text((60, 65), datetime.now().strftime("%b %d, %Y, %I:%M %p"), font=font(33), fill="#eef3ee")
    box = d.textbbox((0, 0), username, font=font(33)); d.text((1125 - box[2], 65), username, font=font(33), fill="#eef3ee")
    def total(cards):
        vals = [11 if r == "A" else 10 if r in "JQK" else int(r) for r, _ in cards]; result = sum(vals); aces = sum(r == "A" for r, _ in cards)
        while result > 21 and aces: result -= 10; aces -= 1
        return result
    def section(cards, y, label):
        center(d, im, y, f"{label}: {total(cards)}", 44, "#f3f6f3", True)
        width = min(255, max(145, (1000 - 36 * (len(cards) - 1)) // len(cards)))
        height = int(width * 385 / 255); start = (1200 - (len(cards) * width + (len(cards) - 1) * 36)) // 2
        for i, (rank, suit) in enumerate(cards): im.paste(card_image(rank, suit, (width, height)), (start + i * (width + 36), y + 78))
    section(player, 145, "Your cards")
    center(d, im, 677, "LiteBet Blackjack", 60, "#003208", True)
    section(dealer, 735, "Dealer cards")
    return save(im, "blackjack.png")

def hilo_card(rank, suit):
    im = Image.new("RGB", (700, 430), "#14243a"); card = card_image(rank, suit, (230, 347)); im.paste(card, (235, 58))
    d = ImageDraw.Draw(im); center(d, im, 15, "HI-LO", 28, "#e6eefb", True)
    return save(im, "hilo.png")
