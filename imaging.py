from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from datetime import datetime
import os


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).parent

OUT = ROOT / "assets" / "generated"
OUT.mkdir(parents=True, exist_ok=True)

CARD_DIR = ROOT / "assets" / "cards"


# ============================================================
# CARD DATA
# ============================================================

SUIT_NAMES = {
    "S": "spades",
    "H": "hearts",
    "D": "diamonds",
    "C": "clubs"
}

SUIT_SYMBOLS = {
    "S": "\u2660",
    "H": "\u2665",
    "D": "\u2666",
    "C": "\u2663"
}


# ============================================================
# FONT
# ============================================================

def font(size, bold=False):

    names = [
        (
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans-Bold.ttf"
            if bold
            else
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans.ttf"
        ),

        (
            "C:/Windows/Fonts/arialbd.ttf"
            if bold
            else
            "C:/Windows/Fonts/arial.ttf"
        )
    ]

    for name in names:

        if os.path.exists(name):

            return ImageFont.truetype(
                name,
                size
            )

    return ImageFont.load_default(
        size=size
    )


# ============================================================
# CENTER TEXT
# ============================================================

def center(
    draw,
    image,
    y,
    text,
    size,
    color,
    bold=False
):

    f = font(
        size,
        bold
    )

    box = draw.textbbox(
        (0, 0),
        text,
        font=f
    )

    width = box[2] - box[0]

    draw.text(
        (
            (image.width - width) / 2,
            y
        ),
        text,
        font=f,
        fill=color
    )


# ============================================================
# SAVE IMAGE
# ============================================================

def save(im, name):

    path = OUT / name

    im.save(
        path,
        "PNG"
    )

    return path


# ============================================================
# BALANCE CARD
# ============================================================

def balance_card(
    name,
    uid,
    points
):

    im = Image.new(
        "RGB",
        (800, 500),
        "#101c2d"
    )

    d = ImageDraw.Draw(im)

    d.ellipse(
        (57, 47, 193, 183),
        fill="#4285ff"
    )

    d.ellipse(
        (61, 51, 189, 179),
        fill="#1a2d47"
    )

    d.text(
        (229, 84),
        name[:20],
        font=font(45, True),
        fill="#e6eefb"
    )

    d.text(
        (229, 145),
        f"ID: {uid}",
        font=font(21),
        fill="#8ea4c3"
    )

    center(
        d,
        im,
        225,
        "POINTS BALANCE",
        25,
        "#8497b2",
        True
    )

    center(
        d,
        im,
        262,
        f"{points:,.2f}",
        92,
        "#4285ff",
        True
    )

    center(
        d,
        im,
        370,
        f"{float(points) * .0001:.4f} LTC",
        25,
        "#c0cee2"
    )

    d.text(
        (59, 456),
        "LiteBet Casino",
        font=font(21, True),
        fill="#8497b2"
    )

    d.text(
        (510, 456),
        datetime.now().strftime(
            "%b %d, %Y, %I:%M %p"
        ),
        font=font(17, True),
        fill="#8497b2"
    )

    return save(
        im,
        f"balance_{uid}.png"
    )


# ============================================================
# LIMBO CARD
# ============================================================

def limbo_card(crashed):

    im = Image.new(
        "RGB",
        (700, 300),
        "#111827"
    )

    d = ImageDraw.Draw(im)

    center(
        d,
        im,
        14,
        "CRASHED AT",
        22,
        "#93a4be",
        True
    )

    center(
        d,
        im,
        43,
        f"{crashed:.2f}x",
        58,
        "#ff5d6c",
        True
    )

    return save(
        im,
        "limbo.png"
    )


# ============================================================
# COINFLIP CARD
# ============================================================

def coinflip_card(landed):

    color = (
        "#264653"
        if landed == "heads"
        else "#5c2a42"
    )

    im = Image.new(
        "RGB",
        (700, 300),
        color
    )

    d = ImageDraw.Draw(im)

    d.ellipse(
        (230, 25, 470, 265),
        fill="#f4c95d",
        outline="#fff1b8",
        width=8
    )

    center(
        d,
        im,
        98,
        landed.upper(),
        38,
        "#29200c",
        True
    )

    return save(
        im,
        f"coinflip_{landed}.png"
    )


# ============================================================
# NORMAL CARD IMAGE
# ============================================================

def card_image(
    rank,
    suit,
    size=(255, 385)
):

    asset = (
        CARD_DIR
        / f"{rank}_of_{SUIT_NAMES[suit]}.png"
    )

    if asset.exists():

        return (
            Image.open(asset)
            .convert("RGB")
            .resize(size)
        )

    # Fallback card
    im = Image.new(
        "RGB",
        size,
        "#f8fafc"
    )

    d = ImageDraw.Draw(im)

    d.text(
        (25, 25),
        rank + SUIT_SYMBOLS[suit],
        font=font(48, True),
        fill=(
            "#bd2438"
            if suit in "HD"
            else "#152033"
        )
    )

    return im


# ============================================================
# HIDDEN BLACKJACK CARD
# ============================================================

def hidden_blackjack_card(
    size=(255, 385)
):

    width, height = size

    im = Image.new(
        "RGB",
        size,
        "#172033"
    )

    d = ImageDraw.Draw(im)

    # Outer border
    d.rounded_rectangle(
        (
            3,
            3,
            width - 3,
            height - 3
        ),
        radius=14,
        fill="#172033",
        outline="#9fb3ce",
        width=5
    )

    # Inner border
    d.rounded_rectangle(
        (
            18,
            18,
            width - 18,
            height - 18
        ),
        radius=10,
        outline="#405777",
        width=4
    )

    # Question mark
    f = font(
        max(
            60,
            int(width * 0.27)
        ),
        True
    )

    text = "?"

    box = d.textbbox(
        (0, 0),
        text,
        font=f
    )

    text_width = (
        box[2] - box[0]
    )

    text_height = (
        box[3] - box[1]
    )

    d.text(
        (
            (width - text_width) / 2,
            (height - text_height) / 2 - 10
        ),
        text,
        font=f,
        fill="#e6eefb"
    )

    return im


# ============================================================
# BLACKJACK CARD
# ============================================================

def blackjack_card(
    username,
    player,
    dealer
):

    im = Image.new(
        "RGB",
        (1200, 1284),
        "#003b08"
    )

    d = ImageDraw.Draw(im)

    # ========================================================
    # HEADER
    # ========================================================

    d.text(
        (60, 65),
        datetime.now().strftime(
            "%b %d, %Y, %I:%M %p"
        ),
        font=font(33),
        fill="#eef3ee"
    )

    name_font = font(
        33
    )

    box = d.textbbox(
        (0, 0),
        username,
        font=name_font
    )

    username_width = (
        box[2] - box[0]
    )

    d.text(
        (
            1125 - username_width,
            65
        ),
        username,
        font=name_font,
        fill="#eef3ee"
    )

    # ========================================================
    # HAND TOTAL
    # ========================================================

    def total(cards):

        values = []

        aces = 0

        for rank, suit in cards:

            # Hidden card doesn't count
            if rank == "?":
                continue

            if rank == "A":

                values.append(11)

                aces += 1

            elif rank in (
                "J",
                "Q",
                "K"
            ):

                values.append(10)

            else:

                try:

                    values.append(
                        int(rank)
                    )

                except (
                    ValueError,
                    TypeError
                ):

                    continue

        result = sum(values)

        while result > 21 and aces:

            result -= 10
            aces -= 1

        return result

    # ========================================================
    # DRAW SECTION
    # ========================================================

    def draw_section(
        cards,
        y,
        label
    ):

        hand_total = total(
            cards
        )

        center(
            d,
            im,
            y,
            f"{label}: {hand_total}",
            44,
            "#f3f6f3",
            True
        )

        count = len(cards)

        gap = 36

        # Card width dynamically changes
        # depending on number of cards
        width = min(
            255,
            max(
                145,
                (
                    1000
                    - gap * (count - 1)
                )
                // max(count, 1)
            )
        )

        height = int(
            width * 385 / 255
        )

        total_width = (
            count * width
            + (count - 1) * gap
        )

        start_x = (
            1200 - total_width
        ) // 2

        # ====================================================
        # DRAW EACH CARD
        # ====================================================

        for i, card in enumerate(cards):

            rank, suit = card

            x = (
                start_x
                + i * (width + gap)
            )

            # Hidden dealer card
            if rank == "?":

                card = hidden_blackjack_card(
                    (width, height)
                )

            else:

                card = card_image(
                    rank,
                    suit,
                    (width, height)
                )

            im.paste(
                card,
                (x, y + 78)
            )

    # ========================================================
    # PLAYER
    # ========================================================

    draw_section(
        player,
        145,
        "Your cards"
    )

    # ========================================================
    # CENTER TITLE
    # ========================================================

    center(
        d,
        im,
        677,
        "LiteBet Blackjack",
        60,
        "#003208",
        True
    )

    # ========================================================
    # DEALER
    # ========================================================

    draw_section(
        dealer,
        735,
        "Dealer cards"
    )

    # ========================================================
    # SAVE
    # ========================================================

    return save(
        im,
        "blackjack.png"
    )


# ============================================================
# HI-LO CARD
# ============================================================

def hilo_card(
    rank,
    suit
):

    im = Image.new(
        "RGB",
        (700, 430),
        "#14243a"
    )

    card = card_image(
        rank,
        suit,
        (230, 347)
    )

    im.paste(
        card,
        (235, 58)
    )

    d = ImageDraw.Draw(im)

    center(
        d,
        im,
        15,
        "HI-LO",
        28,
        "#e6eefb",
        True
    )

    return save(
        im,
        "hilo.png"
    )

def tower_card(rows, current_row, mode, username):
    W, H = 900, 1050
    im = Image.new("RGB", (W, H), "#101827")
    d = ImageDraw.Draw(im)

    d.text(
        (45, 30),
        "LiteBet Tower",
        font=font(38, True),
        fill="#f3f6f3"
    )

    d.text(
        (45, 78),
        f"{username} • {mode.title()}",
        font=font(22),
        fill="#93a4be"
    )

    cols = len(rows[0])
    tile = 105
    gap = 15
    board_w = cols * tile + (cols - 1) * gap
    start_x = (W - board_w) // 2
    start_y = 145

    for row_index, row in enumerate(rows):
        y = start_y + (7 - row_index) * 105

        for col_index, value in enumerate(row):
            x = start_x + col_index * (tile + gap)

            if row_index < current_row:
                if value == "diamond":
                    fill = "#218c74"
                    symbol = "◆"
                else:
                    fill = "#8f4850"
                    symbol = "●"

                d.rounded_rectangle(
                    (x, y, x + tile, y + tile),
                    radius=12,
                    fill=fill
                )

                f = font(45, True)
                box = d.textbbox((0, 0), symbol, font=f)
                d.text(
                    (
                        x + (tile - (box[2] - box[0])) / 2,
                        y + 25
                    ),
                    symbol,
                    font=f,
                    fill="#ffffff"
                )

            else:
                d.rounded_rectangle(
                    (x, y, x + tile, y + tile),
                    radius=12,
                    fill="#34475e"
                )

                d.text(
                    (x + 40, y + 28),
                    "?",
                    font=font(42, True),
                    fill="#aebdcd"
                )

    d.text(
        (45, 955),
        "💎 = Safe     💣 = Bomb",
        font=font(25, True),
        fill="#dbe5ef"
    )

    return save(im, "tower.png")
