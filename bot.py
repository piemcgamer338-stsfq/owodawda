import os, random, hashlib, secrets, asyncio, json, urllib.request
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from pathlib import Path
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from db import Database
from imaging import (
    balance_card,
    limbo_card,
    blackjack_card,
    coinflip_card,
    hilo_card,
    tower_card
)
from english_words import get_english_words_set

from bip_utils import Bip44, Bip44Coins, Bip44Changes 

ENGLISH_WORDS = get_english_words_set(['web2'], lower=True)

load_dotenv()
TOKEN=os.getenv('DISCORD_TOKEN'); DB_URL=os.getenv('DATABASE_URL'); LOG_CHANNEL_ID=int(os.getenv('LOG_CHANNEL_ID','0'))
NAVY=discord.Colour(0x34495E); GREEN=discord.Colour.green(); RED=discord.Colour.red(); RATE=Decimal('0.0001'); USD_PER_POINT=Decimal('0.0045')
intents=discord.Intents.default(); intents.message_content=True; intents.members=True
bot=commands.Bot(command_prefix='.', intents=intents, help_command=None)
db=Database(DB_URL) if DB_URL else None

def emb(title=None, description=None, colour=NAVY):
    e=discord.Embed(title=title, description=description, colour=colour, timestamp=datetime.now(timezone.utc)); e.set_footer(text='LiteBet'); return e
def money(v): return f'{Decimal(v):,.2f}'
async def resolve_amount(ctx, raw):
    """Accept a number or the word 'all' for game bets."""
    if isinstance(raw, Decimal): return raw
    if str(raw).lower() == 'all': return Decimal((await db.user(ctx.author.id))['balance'])
    try: return Decimal(str(raw))
    except InvalidOperation: raise commands.BadArgument('Amount must be a number or all.')
def seed():
    server=secrets.token_hex(32); client=secrets.token_hex(12); return server,client,hashlib.sha256(server.encode()).hexdigest()
def admin(ctx): return ctx.author.guild_permissions.administrator
def manager(ctx): return ctx.author.guild_permissions.manage_guild or admin(ctx)
async def require_game(ctx, amount):
    if await db.frozen(): await ctx.send(embed=emb('Games are currently frozen','An administrator has temporarily locked games.',RED)); return False
    if amount <= 0: await ctx.send(embed=emb('Invalid bet','Bet amount must be greater than zero.',RED)); return False
    if not await db.take_bet(ctx.author.id, amount): await ctx.send(embed=emb('Insufficient balance',f'You need **{money(amount)} points** to play.',RED)); return False
    return True
# ============================================================
# GAME WIN LOG
# ============================================================

async def announce_game_win(user, payout):
    """Send a new message to the configured game log channel."""

    if not LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(LOG_CHANNEL_ID)

    if not channel:
        return

    try:
        username = user.display_name

        await channel.send(
            f'✅ **{username}** won **{money(payout)} points!**'
        )

    except Exception as e:
        print(f'Game win log error: {e}')


# ============================================================
# GAME FINISH
# ============================================================

async def finish(ctx, game, amount, won, multiplier, detail, image=None):

    payout = (
        amount * multiplier
    ).quantize(Decimal('0.01')) if won else Decimal('0')

    # Save game result
    await db.record(
        ctx.author.id,
        game,
        amount,
        'win' if won else 'loss',
        payout
    )

    # --------------------------------------------------------
    # PUBLIC WIN LOG
    # ONLY WINS ARE ANNOUNCED
    # --------------------------------------------------------

    if won:
        await announce_game_win(
            ctx.author,
            payout
        )

    # --------------------------------------------------------
    # NORMAL GAME RESULT
    # --------------------------------------------------------

    e = emb(
        f'{game} — You {"Won" if won else "Lost"}',
        (
            f'**Bet:** {money(amount)} points\n'
            f'{detail}\n\n'
            + (
                f'Congratulations! You received '
                f'**{money(payout)} points**.'
                if won
                else
                'Better luck next time.'
            )
        ),
        GREEN if won else RED
    )

    if image:

        e.set_image(
            url=f'attachment://{image.name}'
        )

        await ctx.send(
            embed=e,
            file=discord.File(image)
        )

    else:

        await ctx.send(
            embed=e
        )

def result_embed(game, amount, won, multiplier, detail):
    payout=(amount*multiplier).quantize(Decimal('0.01')) if won else Decimal('0')
    text=f'**Bet:** {money(amount)} points\n{detail}\n\n'
    return emb(f'{game} - You {"Won" if won else "Lost"}', text+(f'Congratulations! You received **{money(payout)} points**.' if won else 'Better luck next time.'), GREEN if won else RED)

RANKS=['2','3','4','5','6','7','8','9','10','J','Q','K','A']
ACTIVE_MINES={}

class HiloView(discord.ui.View):
    def __init__(self, author_id, amount, rank, suit, server, client, public_hash):
        super().__init__(timeout=90); self.author_id=author_id; self.amount=amount; self.rank=rank; self.suit=suit; self.server=server; self.client=client; self.public_hash=public_hash; self.streak=0; self.message=None
    def current_embed(self):
        return emb('Hi-Lo',f'**Bet Amount:** {money(self.amount)}\n**Current Multiplier:** {(Decimal("1")+Decimal(self.streak)*Decimal(".20")):.2f}x\n**Streak:** {self.streak}\n\nChoose whether the next card will be higher or lower.\nPublic Hash: `{self.public_hash}`')
    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message('Only the player who started this Hi-Lo game can use these buttons.',ephemeral=True); return False
        return True
    async def play(self, interaction, guess):
        next_rank=random.choice(RANKS); next_suit=random.choice(['S','H','D','C']); a,b=RANKS.index(self.rank),RANKS.index(next_rank)
        correct=(guess=='high' and b>a) or (guess=='low' and b<a)
        if not correct:
            await db.record(self.author_id,'Hi-Lo',self.amount,'loss',Decimal('0'))
            e=result_embed('Hi-Lo',self.amount,False,Decimal('0'),f'Your card: **{self.rank}**\nNext card: **{next_rank}**\nServer Seed: `{self.server}`\nClient Seed: `{self.client}`')
            e.set_image(url='attachment://hilo.png'); return await interaction.response.edit_message(embed=e,attachments=[discord.File(hilo_card(next_rank,next_suit))],view=None)
        self.streak+=1; self.rank,self.suit=next_rank,next_suit
        self.cashout.disabled=False
        e=self.current_embed(); e.set_image(url='attachment://hilo.png')
        await interaction.response.edit_message(embed=e,attachments=[discord.File(hilo_card(self.rank,self.suit))],view=self)
    @discord.ui.button(label='High',style=discord.ButtonStyle.primary)
    async def high(self,interaction,button): await self.play(interaction,'high')
    @discord.ui.button(label='Low',style=discord.ButtonStyle.primary)
    async def low(self,interaction,button): await self.play(interaction,'low')
    @discord.ui.button(label='Cash Out',style=discord.ButtonStyle.success,disabled=True)
    async def cashout(self,interaction,button):
        mult=Decimal('1')+Decimal(self.streak)*Decimal('.20'); payout=(self.amount*mult).quantize(Decimal('.01'))
        await db.record(self.author_id,'Hi-Lo',self.amount,'win',payout)
        e=result_embed('Hi-Lo',self.amount,True,mult,f'Streak: **{self.streak}**\nServer Seed: `{self.server}`\nClient Seed: `{self.client}`')
        await interaction.response.edit_message(embed=e,view=None)
    async def on_timeout(self):
        if self.streak and self.message:
            mult=Decimal('1')+Decimal(self.streak)*Decimal('.20'); await db.record(self.author_id,'Hi-Lo',self.amount,'win',(self.amount*mult).quantize(Decimal('.01')))
            await self.message.edit(embed=result_embed('Hi-Lo',self.amount,True,mult,'Auto-cashed out after timeout.'),view=None)

class HelpSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder='Select a category',
            options=[
                discord.SelectOption(
                    label='Admin',
                    emoji='🛠️'
                ),
                discord.SelectOption(
                    label='Utility',
                    emoji='🧰'
                ),
                discord.SelectOption(
                    label='Balance',
                    emoji='💰'
                ),
                discord.SelectOption(
                    label='Games',
                    emoji='🎮'
                )
            ]
        )

    async def callback(self, interaction):

        lists = {
            'Admin': (
                '⚙️ `.add` — Add points to a user\n'
                '🗑️ `.remove` — Remove points from a user\n'
                '💰 `.setbalance` — Set a user\'s balance\n'
                '🔄 `.lbreset` — Reset the leaderboard\n'
                '🎁 `.beg` — Give yourself a small random reward\n'
                '📜 `.history` — View betting history\n'
                '🔒 `.freeze` — Freeze the casino\n'
                '🔓 `.unfreeze` — Unfreeze the casino'
            ),

            'Utility': (
                '📍 `.address` — View your deposit address\n'
                '🎮 `.games` — View all available games\n'
                '📖 `.guide` — Learn how to use LiteBet\n'
                '🏆 `.leaderboard` — View the top players\n'
                '🔐 `.privacy` — Toggle your profile privacy\n'
                '⭐ `.rank` — View your current rank\n'
                '📊 `.ranks` — View all ranks and requirements\n'
                '🚨 `.report` — Report an issue or player\n'
                '📈 `.stats` — View player statistics\n'
                '🌎 `.worldtime` — View world time\n'
                '⏱️ `.timer` — Start a timer\n'
                '🧵 `.thread` — Create a thread'
            ),

            'Balance': (
                '💰 `.balance` — Check your point balance\n'
                '🎁 `.daily` — Claim your daily reward\n'
                '💎 `.deposit` — Get your Litecoin deposit address\n'
                '📅 `.monthly` — Claim your monthly reward\n'
                '🌧️ `.rain` — Send a point rain to the server\n'
                '💸 `.rb` — Claim available rateback\n'
                '💰 `.tip` — Tip points to another user\n'
                '👑 `.vip` — View your VIP status\n'
                '📆 `.weekly` — Claim your weekly reward\n'
                '📤 `.withdraw` — Request a Litecoin withdrawal\n'
                '💱 `.price` — View the current LTC price'
            ),

            'Games': (
                '🃏 `.blackjack` / `.bj` — Play Blackjack against the dealer\n'
                '🃏 `.ward` — Play the Ward card game\n'
                '🎴 `.hilo` — Guess whether the next card is higher or lower\n'
                '🪙 `.coinflip` / `.cf` — Flip a coin and test your luck\n'
                '🚀 `.limbo` — Cash out before the multiplier crashes\n'
                '💣 `.mines` — Reveal diamonds and avoid the bombs\n'
                '🗼 `.tower` — Climb the tower and cash out before hitting a bomb\n'
                '✊ `.rps` — Play Rock Paper Scissors\n'
                '🔤 `.word` — Guess the hidden word'
            )
        }

        category = self.values[0]

        await interaction.response.edit_message(
            embed=emb(
                f'LiteBet — {category}',
                lists[category],
                GREEN
            ),
            view=self.view
        )
        await interaction.response.edit_message(embed=emb(f'ℹ️ Help Command — {self.values[0]}',lists[self.values[0]]),view=self.view)
class HelpView(discord.ui.View):
    def __init__(self): super().__init__(timeout=180); self.add_item(HelpSelect())

class RainView(discord.ui.View):
    def __init__(self, host_id, amount, seconds):
        super().__init__(timeout=seconds); self.host_id=host_id; self.amount=amount; self.entries=set()
    @discord.ui.button(label='Join Rain', emoji='🌧️', style=discord.ButtonStyle.primary)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.bot: return await interaction.response.defer()
        user=await db.user(interaction.user.id)
        if Decimal(user['deposited_points']) < Decimal('50'):
            return await interaction.response.send_message('You need at least **50 lifetime deposited points** to join rain.',ephemeral=True)
        self.entries.add(interaction.user.id)
        await interaction.response.send_message('You joined the rain! 🌧️',ephemeral=True)
    async def on_timeout(self):
        if not self.message: return
        if not self.entries:
            await db.balance(self.host_id,self.amount)
            return await self.message.edit(embed=emb('Rain ended','Nobody eligible joined, so the host was refunded.',RED),view=None)
        share=(self.amount/len(self.entries)).quantize(Decimal('.01'))
        for uid in self.entries: await db.balance(uid,share)
        text=f'**{len(self.entries)}** eligible members joined and each received **{money(share)} points**.'
        await self.message.edit(embed=emb('🌧️ Rain ended',text,GREEN),view=None)

@bot.event
async def on_ready():
    print(f'LiteBet ready as {bot.user}')
    if not reset_wagers.is_running(): reset_wagers.start()
@tasks.loop(hours=24)
async def reset_wagers():
    async with db.pool.acquire() as c: await c.execute('UPDATE users SET daily_wager=0')

    # `.race` Wager Race Command

=
# ============================================================
# WAGER RACE
# ============================================================

import io
import aiohttp
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# RACE SETTINGS
# ============================================================

RACE_TITLE = "LiteBet WAGER RACE"
RACE_SUBTITLE = "$24 RACE  -  LiteBet CASINO"

# Prize for each position
RACE_PRIZES = {
    1: "$5",
    2: "$5",
    3: "$5",
    4: "$1",
    5: "$1",
    6: "$1",
    7: "$1",
    8: "$1",
    9: "$1",
    10: "$1"
}


# ============================================================
# FONT HELPER
# ============================================================

def race_font(size, bold=False):

    possible_fonts = []

    if bold:
        possible_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "arialbd.ttf"
        ]
    else:
        possible_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "arial.ttf"
        ]

    for path in possible_fonts:

        try:
            return ImageFont.truetype(
                path,
                size
            )
        except Exception:
            continue

    return ImageFont.load_default()


# ============================================================
# GET DISCORD AVATAR
# ============================================================

async def get_race_avatar(user):

    try:

        avatar_url = user.display_avatar.replace(
            size=128,
            format="png"
        ).url

        async with aiohttp.ClientSession() as session:

            async with session.get(
                avatar_url
            ) as response:

                if response.status != 200:
                    return None

                data = await response.read()

        avatar = Image.open(
            io.BytesIO(data)
        ).convert("RGBA")

        avatar = avatar.resize(
            (42, 42),
            Image.Resampling.LANCZOS
        )

        # Circular avatar mask
        mask = Image.new(
            "L",
            (42, 42),
            0
        )

        mask_draw = ImageDraw.Draw(mask)

        mask_draw.ellipse(
            (0, 0, 42, 42),
            fill=255
        )

        avatar.putalpha(mask)

        return avatar

    except Exception as e:

        print(
            f"Race avatar error: {e}"
        )

        return None


# ============================================================
# CREATE RACE IMAGE
# ============================================================

async def create_race_image(ctx, players):

    width = 900
    height = 850

    image = Image.new(
        "RGB",
        (width, height),
        (7, 10, 25)
    )

    draw = ImageDraw.Draw(image)

    # --------------------------------------------------------
    # FONTS
    # --------------------------------------------------------

    title_font = race_font(
        42,
        bold=True
    )

    subtitle_font = race_font(
        20,
        bold=True
    )

    rank_font = race_font(
        25,
        bold=True
    )

    name_font = race_font(
        17,
        bold=True
    )

    points_font = race_font(
        15
    )

    prize_font = race_font(
        18,
        bold=True
    )

    small_font = race_font(
        13
    )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    draw.text(
        (width // 2, 35),
        RACE_TITLE,
        fill=(255, 220, 0),
        font=title_font,
        anchor="ma"
    )

    draw.text(
        (width // 2, 82),
        RACE_SUBTITLE,
        fill=(180, 185, 200),
        font=subtitle_font,
        anchor="ma"
    )

    # --------------------------------------------------------
    # TOP LINE
    # --------------------------------------------------------

    draw.line(
        (180, 115, 720, 115),
        fill=(45, 50, 75),
        width=2
    )

    # --------------------------------------------------------
    # RACE ROWS
    # --------------------------------------------------------

    start_y = 135
    row_height = 63
    row_width = 820
    row_x = 40

    for position, player in enumerate(
        players[:10],
        start=1
    ):

        y = (
            start_y
            + (position - 1) * row_height
        )

        # ----------------------------------------------------
        # ROW BACKGROUND
        # ----------------------------------------------------

        if position == 1:

            row_color = (40, 38, 5)
            border_color = (255, 220, 0)

        elif position == 2:

            row_color = (35, 35, 38)
            border_color = (150, 150, 155)

        elif position == 3:

            row_color = (45, 28, 10)
            border_color = (180, 105, 35)

        else:

            row_color = (17, 21, 36)
            border_color = (27, 32, 50)

        draw.rounded_rectangle(
            (
                row_x,
                y,
                row_x + row_width,
                y + 54
            ),
            radius=8,
            fill=row_color,
            outline=border_color,
            width=2 if position <= 3 else 1
        )

        # ----------------------------------------------------
        # RANK
        # ----------------------------------------------------

        if position == 1:
            rank_color = (255, 225, 0)

        elif position == 2:
            rank_color = (190, 190, 195)

        elif position == 3:
            rank_color = (220, 130, 45)

        else:
            rank_color = (145, 155, 180)

        draw.text(
            (
                row_x + 25,
                y + 27
            ),
            f"#{position}",
            fill=rank_color,
            font=rank_font,
            anchor="lm"
        )

        # ----------------------------------------------------
        # AVATAR
        # ----------------------------------------------------

        user = player["user"]

        avatar = await get_race_avatar(
            user
        )

        avatar_x = row_x + 75
        avatar_y = y + 6

        if avatar:

            image.paste(
                avatar,
                (
                    avatar_x,
                    avatar_y
                ),
                avatar
            )

        else:

            draw.ellipse(
                (
                    avatar_x,
                    avatar_y,
                    avatar_x + 42,
                    avatar_y + 42
                ),
                fill=(45, 50, 65)
            )

        # ----------------------------------------------------
        # USERNAME
        # ----------------------------------------------------

        username = user.display_name

        if len(username) > 18:

            username = (
                username[:18]
                + "..."
            )

        draw.text(
            (
                row_x + 135,
                y + 18
            ),
            username,
            fill=(235, 235, 240),
            font=name_font,
            anchor="lm"
        )

        # ----------------------------------------------------
        # WAGER
        # ----------------------------------------------------

        wager = player["wagered"]

        try:

            wager_text = (
                f"{Decimal(str(wager)):,.0f} points"
            )

        except Exception:

            wager_text = (
                f"{wager} points"
            )

        draw.text(
            (
                row_x + 610,
                y + 27
            ),
            wager_text,
            fill=(150, 155, 170),
            font=points_font,
            anchor="rm"
        )

        # ----------------------------------------------------
        # PRIZE
        # ----------------------------------------------------

        prize = RACE_PRIZES.get(
            position,
            "$0"
        )

        prize_color = (
            (255, 220, 0)
            if position <= 3
            else (70, 210, 90)
        )

        draw.text(
            (
                row_x + 795,
                y + 27
            ),
            prize,
            fill=prize_color,
            font=prize_font,
            anchor="rm"
        )

    # --------------------------------------------------------
    # FOOTER
    # --------------------------------------------------------

    footer_y = (
        start_y
        + 10 * row_height
        + 10
    )

    draw.text(
        (
            width // 2,
            footer_y
        ),
        "Top 10 players by wager",
        fill=(90, 95, 115),
        font=small_font,
        anchor="ma"
    )

    # --------------------------------------------------------
    # SAVE TO MEMORY
    # --------------------------------------------------------

    output = io.BytesIO()

    image.save(
        output,
        format="PNG"
    )

    output.seek(0)

    return output


# ============================================================
# RACE COMMAND
# ============================================================

@bot.command()
async def race(ctx):

    try:

        # ====================================================
        # GET TOP 10
        #
        # IMPORTANT:
        # Change "wagered" below if your database uses a
        # different column name for total wager.
        # ====================================================

        rows = await db.pool.fetch(
            """
            SELECT
                user_id,
                wagered
            FROM users
            WHERE wagered > 0
            ORDER BY wagered DESC
            LIMIT 10
            """
        )

        # ----------------------------------------------------
        # NO PLAYERS
        # ----------------------------------------------------

        if not rows:

            return await ctx.send(
                embed=emb(
                    "Summer Wager Race",
                    "There are currently no players in the race.",
                    RED
                )
            )

        # ====================================================
        # LOAD DISCORD USERS
        # ====================================================

        players = []

        for row in rows:

            user_id = int(
                row["user_id"]
            )

            user = ctx.guild.get_member(
                user_id
            )

            if user is None:

                try:

                    user = await bot.fetch_user(
                        user_id
                    )

                except Exception:

                    continue

            players.append(
                {
                    "user": user,
                    "wagered": row["wagered"]
                }
            )

        # ----------------------------------------------------
        # CREATE IMAGE
        # ----------------------------------------------------

        image = await create_race_image(
            ctx,
            players
        )

        # ----------------------------------------------------
        # SEND IMAGE ONLY
        # ----------------------------------------------------

        file = discord.File(
            image,
            filename="race.png"
        )

        await ctx.send(
            file=file
        )

    except Exception as e:

        print(
            f"Race command error: {e}"
        )

        await ctx.send(
            embed=emb(
                "Race Error",
                (
                    "Something went wrong while "
                    "creating the wager race.\n\n"
                    f"`{e}`"
                ),
                RED
            )
        )

    total=await db.pool.fetchval('SELECT COUNT(*) FROM users')
    await ctx.send(embed=emb('ℹ️ Help Command - Main Menu',f'Welcome to **LiteBet**, the Discord Litecoin Casino Bot.\n💡 New here? Read `.guide`\n\n**Rate:** 1 point = 0.0001 LTC\n**Total Commands:** 40+\n**Total Users:** {total}\n\n> Bot made by meow2004yr'),view=HelpView())
# ============================================================
# LEADERBOARD RESET
# ============================================================

@bot.command()
@commands.is_owner()
async def lbreset(ctx):

    await db.pool.execute("""
        UPDATE users
        SET
            daily_wager = 0,
            weekly_wager = 0,
            monthly_wager = 0
    """)

    await ctx.send(
        embed=emb(
            '🏆 Leaderboard Reset',
            'Daily, weekly, and monthly wager leaderboards have been reset to **0**.',
            GREEN
        )
    )
@bot.command()
async def guide(ctx): await ctx.send(embed=emb('LiteBet Guide','Start with `.daily`, then use `.balance`. Every game deducts its bet first, and its provably-fair seeds are shown in the result. Use `.help` to browse commands.'))
@bot.command(aliases=['games'])
async def game_list(ctx): await ctx.send(embed=emb('🎮 LiteBet Games','🃏 `.blackjack/.bj` — House Blackjack\n🎲 `.ward` — Highest roll wins\n🃏 `.hilo` — Predict the next card\n🪙 `.coinflip/.cf` — 1.92× payout\n🚀 `.limbo` — Set your multiplier\n💣 `.mines` — Reveal diamonds\n#️⃣ `.ttt` — Tic-tac-toe\n✂️ `.rps` — Rock, paper, scissors\n✍️ `.word` — Word challenge'))

@bot.command(aliases=['b', 'bal'])
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    u = await db.user(member.id)

    if member != ctx.author and u['privacy']:
        return await ctx.send(
            embed=emb(
                'Private account',
                'That member has chosen to keep their profile private.',
                RED
            )
        )

    # Get points balance
    points = Decimal(str(u['balance']))

    # Convert points
    ltc = (points * Decimal('0.0001')).quantize(
        Decimal('0.0000')
    )

    usd = (points * Decimal('0.005')).quantize(
        Decimal('0.0000')
    )

    # Generate balance card
    p = balance_card(
        member.display_name,
        member.id,
        u['balance']
    )

    # Attach image to embed
    file = discord.File(
        p,
        filename='balance.png'
    )

    # Create embed
    embed = discord.Embed(
        title=f"{member.display_name}'s balance card",
        description=(
            f"**{ltc:.4f} LTC** | "
            f"**${usd:.4f} USD** | "
            f"**{points:.0f} Points**"
        ),
        color=0x5865F2
    )

    # Put balance image inside embed
    embed.set_image(
        url='attachment://balance.png'
    )

    await ctx.send(
        embed=embed,
        file=file
    )
    
@bot.command()
async def daily(ctx):
    u=await db.user(ctx.author.id); now=datetime.now(timezone.utc)
    if u['last_daily'] and now-u['last_daily']<timedelta(hours=24): return await ctx.send(embed=emb('Daily unavailable',f'Try again <t:{int((u["last_daily"]+timedelta(hours=24)).timestamp())}:R>.',RED))
    async with db.pool.acquire() as c: await c.execute('UPDATE users SET balance=balance+1,bonuses=bonuses+1,last_daily=$2 WHERE user_id=$1',ctx.author.id,now)
    await ctx.send(embed=emb('Daily claimed','You received **1.00 point**. Come back in 24 hours!',GREEN))
@bot.command()
async def price(ctx, amount: Decimal=Decimal('1')): await ctx.send(embed=emb('LiteBet Price',f'**{money(amount)} points** = `{amount*RATE:.8f} LTC` = `${amount*USD_PER_POINT:.2f}`\n**${money(amount)} USD** = `{amount/USD_PER_POINT:.2f} points`\n**${money(amount)} USD** = `{amount/(USD_PER_POINT/RATE):.8f} LTC`'))
@bot.command()
async def privacy(ctx, setting: str):
    on=setting.lower() in ('on','public')
    async with db.pool.acquire() as c: await c.execute('UPDATE users SET privacy=$2 WHERE user_id=$1',ctx.author.id,not on)
    await ctx.send(embed=emb('Privacy settings',f'**Current Status:** 🌍 {"Public" if on else "Private"}\n\n'+('> Everyone can view your balance and interact with your activity.' if on else '> Everyone cannot view your balance; games show as unknown.')))
@bot.command()
async def tip(ctx, member: discord.Member, amount: Decimal):
    if member.bot or member==ctx.author or amount<=0: return await ctx.send(embed=emb('Invalid tip','Choose another member and a positive amount.',RED))
    if not await db.debit(ctx.author.id,amount): return await ctx.send(embed=emb('Insufficient balance','You do not have enough points.',RED))
    await db.balance(member.id,amount)
    async with db.pool.acquire() as c: await c.execute('UPDATE users SET tips_sent=tips_sent+$2 WHERE user_id=$1',ctx.author.id,amount); await c.execute('UPDATE users SET tips_received=tips_received+$2 WHERE user_id=$1',member.id,amount)
    await ctx.send(embed=emb('Tip sent',f'{ctx.author.mention} tipped {member.mention} **{money(amount)} points**.',GREEN))
@bot.command()
async def weekly(ctx):
    u=await db.user(ctx.author.id); await ctx.send(embed=emb('Weekly bonus',f'Your weekly wager: **{money(u["weekly_wager"])}** points\nEstimated bonus: **{money(Decimal(u["weekly_wager"])*Decimal(".008"))} points**.'))
@bot.command()
async def monthly(ctx):
    u=await db.user(ctx.author.id); await ctx.send(embed=emb('Monthly bonus',f'Your monthly wager: **{money(u["monthly_wager"])}** points\nEstimated bonus: **{money(Decimal(u["monthly_wager"])*Decimal(".01"))} points**.'))
@bot.command()
async def rain(ctx, amount: Decimal, duration: int=60):
    if amount<=0: return await ctx.send(embed=emb('Invalid rain','Rain amount must be greater than zero.',RED))
    if duration<10 or duration>3600: return await ctx.send(embed=emb('Invalid duration','Choose a duration from 10 seconds to 1 hour.',RED))
    if not await db.debit(ctx.author.id,amount): return await ctx.send(embed=emb('Insufficient balance','You do not have enough points to rain.',RED))
    view=RainView(ctx.author.id,amount,duration)
    msg=await ctx.send(embed=emb('🌧️ Points Rain',f'{ctx.author.mention} is raining **{money(amount)} points**!\n\nClick **Join Rain** within <t:{int((datetime.now(timezone.utc)+timedelta(seconds=duration)).timestamp())}:R>.\n\n**Requirement:** at least **50 lifetime deposited points**.'),view=view)
    view.message=msg

@bot.command()
async def rb(ctx):
    rb = await db.claim_rateback(ctx.author.id)

    if rb <= 0:
        return await ctx.send(
            embed=emb(
                'Rateback',
                'You have **0 points** available to claim.\n\n'
                'You need to lose more points before claiming rateback again.',
                RED
            )
        )

    await ctx.send(
        embed=emb(
            'Rateback Claimed',
            f'You received **{money(rb)} points**.\n\n'
            'Your rateback balance has been reset. '
            'Lose more points to earn rateback again.',
            GREEN
        )
    )

ADD_ALLOWED_USER_ID = 1519015243710201927


def add_allowed(ctx):
    return ctx.author.id == ADD_ALLOWED_USER_ID


@bot.command()
@commands.check(add_allowed)
async def add(ctx, member: discord.Member, amount: Decimal):
    await db.balance(member.id, amount)

    e = emb(
        'Balance added',
        f'**{money(amount)} points** has been added to {member.mention}\'s balance.'
    )

    e.set_thumbnail(
        url=member.display_avatar.url
    )

    await ctx.send(embed=e)
@bot.command()
@commands.check(admin)
async def remove(ctx, member: discord.Member, amount: Decimal): await db.balance(member.id,-amount); await ctx.send(embed=emb('Balance removed',f'**{money(amount)} points** removed from {member.mention}.'))
@bot.command()
@commands.check(admin)
async def setbalance(ctx, member: discord.Member, amount: Decimal):
    u=await db.user(member.id)
    async with db.pool.acquire() as c: await c.execute('UPDATE users SET balance=$2 WHERE user_id=$1',member.id,amount)
    await ctx.send(embed=emb('Balance Changes',f'User old balance: **{money(u["balance"])}**\nNew balance: **{money(amount)}**'))
@bot.command()
@commands.check(manager)
async def freeze(ctx): await db.set_frozen(True); await ctx.send(embed=emb('All games and withdrawals have been frozen','.unfreeze to unlock all commands.'))
@bot.command()
@commands.check(manager)
async def unfreeze(ctx): await db.set_frozen(False); await ctx.send(embed=emb('All games and withdrawals have been unfrozen','.freeze to lock all commands.'))
@bot.command()
@commands.check(admin)
async def history(ctx, member: discord.Member=None):
    member=member or ctx.author; rows=await db.pool.fetch('SELECT game,amount,outcome,payout,created_at FROM bets WHERE user_id=$1 ORDER BY id DESC LIMIT 10',member.id)
    text='\n'.join(f'`{i+1}.` **{r["game"]}** | {money(r["amount"])} | {r["outcome"]} | {money(r["payout"])}×' for i,r in enumerate(rows)) or 'No bets yet.'; await ctx.send(embed=emb('Latest 10 bets',text))
    # ============================================================
# GAME WIN LOG CHANNEL
# ============================================================

LOG_CHANNEL_ID = None


@bot.command()
@commands.check(admin)
async def loghistory(ctx, channel: discord.TextChannel):
    global LOG_CHANNEL_ID

    LOG_CHANNEL_ID = channel.id

    await ctx.send(
        embed=emb(
            'Game History Channel Updated',
            f'Game win logs will now be sent to {channel.mention}.',
            GREEN
        )
    )

@bot.command(aliases=['cf','coinfip'])
async def coinflip(ctx, amount: str, choice: str=None):
    amount=await resolve_amount(ctx,amount)
    if not await require_game(ctx,amount): return
    choice=(choice or random.choice(['heads','tails'])).lower(); choice='heads' if choice in ('h','head','heads') else 'tails'; landed=random.choice(['heads','tails']); s,c,h=seed()
    await asyncio.sleep(2); await finish(ctx,'Coinflip',amount,choice==landed,Decimal('1.92'),f'**Choice:** {choice.title()}\n**Landed:** {landed.title()}\n🔒 **Provably Fair**\nPublic Hash: `{h}`\nServer Seed: `{s}`\nClient Seed: `{c}`')
@bot.command()
async def ward(ctx, amount: Decimal):
    # Deduct the bet once
    if not await require_game(ctx, amount):
        return

    # Rolling message
    message = await ctx.send(
        '<a:rollingdice:1538016308707201164> **Rolling the dice...**'
    )

    # Wait 3 seconds
    await asyncio.sleep(3)

    # Roll both dice
    player_roll = random.randint(1, 6)
    litebet_roll = random.randint(1, 6)

    # Determine result
    won = player_roll > litebet_roll

    # Tie = loss for the player
    if player_roll == litebet_roll:
        won = False

    # Payout multiplier
    multiplier = Decimal('1.90')

    # Result details
    if player_roll > litebet_roll:
        detail = (
            f'🎲 **{ctx.author.display_name}** rolled: '
            f'**{player_roll}**\n'
            f'🎲 **LiteBet** rolled: **{litebet_roll}**\n\n'
            f'🏆 You rolled higher!'
        )

    elif player_roll < litebet_roll:
        detail = (
            f'🎲 **{ctx.author.display_name}** rolled: '
            f'**{player_roll}**\n'
            f'🎲 **LiteBet** rolled: **{litebet_roll}**\n\n'
            f'💀 LiteBet rolled higher!'
        )

    else:
        detail = (
            f'🎲 **{ctx.author.display_name}** rolled: '
            f'**{player_roll}**\n'
            f'🎲 **LiteBet** rolled: **{litebet_roll}**\n\n'
            f'🤝 It was a tie!'
        )

    # Calculate payout
    payout = (
        amount * multiplier
    ).quantize(Decimal('0.01')) if won else Decimal('0')

    # Record wager and payout exactly once
    await db.record(
        ctx.author.id,
        'Ward Game',
        amount,
        'win' if won else 'loss',
        payout
    )

    # Build final embed
    result = emb(
        f'Ward Game — {"You Won!" if won else "You Lost!"}',
        (
            f'**Bet:** {money(amount)} points\n'
            f'**Multiplier:** {multiplier:.2f}×\n\n'
            f'{detail}\n\n'
            + (
                f'🎉 **You received {money(payout)} points!**'
                if won
                else
                'Better luck next time.'
            )
        ),
        GREEN if won else RED
    )

    # Edit the original rolling message
    await message.edit(
        content=None,
        embed=result
    )
    
@bot.command()
async def limbo(ctx, amount: Decimal, target: Decimal):
    # -----------------------------
    # VALIDATE TARGET
    # -----------------------------
    if target < Decimal('1.01') or target > Decimal('100'):
        return await ctx.send(
            embed=emb(
                'Invalid target',
                'Choose a target from **1.01× to 100×**.',
                RED
            )
        )

    # -----------------------------
    # TAKE BET
    # -----------------------------
    if not await require_game(ctx, amount):
        return

    # -----------------------------
    # PROVABLY FAIR SEEDS
    # -----------------------------
    server_seed = secrets.token_hex(32)
    client_seed = secrets.token_hex(16)

    public_hash = hashlib.sha256(
        server_seed.encode()
    ).hexdigest()

    # -----------------------------
    # CREATE DETERMINISTIC VALUE
    # -----------------------------
    hash_input = (
        f'{server_seed}:{client_seed}'
    ).encode()

    digest = hashlib.sha256(
        hash_input
    ).hexdigest()

    # Use 52 bits of the hash.
    # This gives a very large range of possible results.
    number = int(digest[:13], 16)

    max_number = 0x10000000000000

    random_value = Decimal(number) / Decimal(max_number)

    # Never allow exactly 1
    if random_value >= Decimal('1'):
        random_value = Decimal('0.999999999999')

    # -----------------------------
    # LIMBO FORMULA
    # 4% HOUSE EDGE
    # -----------------------------
    house_edge = Decimal('0.96')

    crashed = house_edge / (
        Decimal('1') - random_value
    )

    # -----------------------------
    # ROUND RESULT
    # -----------------------------
    crashed = crashed.quantize(
        Decimal('0.01')
    )

    # Minimum visible crash
    if crashed < Decimal('1.01'):
        crashed = Decimal('1.01')

    # Maximum game crash
    if crashed > Decimal('100.00'):
        crashed = Decimal('100.00')

    # -----------------------------
    # CHECK WIN
    # -----------------------------
    won = crashed >= target

    # -----------------------------
    # NONCE
    # -----------------------------
    nonce = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    # -----------------------------
    # RESULT DETAILS
    # -----------------------------
    detail = (
        f'**Target:** {target:.2f}×\n'
        f'**Crashed:** {crashed:.2f}×\n\n'
        f'🔒 **Provably Fair**\n'
        f'Public Hash: `{public_hash}`\n'
        f'Server Seed: `{server_seed}`\n'
        f'Client Seed: `{client_seed}`\n'
        f'Nonce: `{nonce}`'
    )

    # -----------------------------
    # FINISH GAME
    # -----------------------------
    await finish(
        ctx,
        'Limbo',
        amount,
        won,
        target,
        detail,
        limbo_card(float(crashed))
    )

@bot.command(aliases=['depo'])
async def deposit(ctx):

    xpub = os.getenv("LTC_XPUB")

    if not xpub:
        return await ctx.send(
            embed=emb(
                'Deposit unavailable',
                'The owner has not configured `LTC_XPUB` yet.',
                RED
            )
        )

    try:
        # Get / create user
        u = await db.user(ctx.author.id)

        # ----------------------------------------------------
        # REUSE EXISTING DEPOSIT ADDRESS
        # ----------------------------------------------------

        existing_address = u.get('deposit_address')

        if existing_address:
            address = existing_address

        else:
            # ------------------------------------------------
            # GET NEXT DEPOSIT INDEX
            # ------------------------------------------------

            index = int(u['deposit_index'])

            # ------------------------------------------------
            # DERIVE LTC ADDRESS FROM XPUB
            # ------------------------------------------------

            wallet = Bip44.FromExtendedKey(
                xpub.strip(),
                Bip44Coins.LITECOIN
            )

            address = (
                wallet
                .Change(Bip44Changes.CHAIN_EXT)
                .AddressIndex(index)
                .PublicKey()
                .ToAddress()
            )

            # ------------------------------------------------
            # SAVE ADDRESS + MOVE INDEX
            # ------------------------------------------------

            await db.pool.execute(
                """
                UPDATE users
                SET
                    deposit_index = deposit_index + 1,
                    deposit_address = $2
                WHERE user_id = $1
                """,
                ctx.author.id,
                address
            )

        # ====================================================
        # DEPOSIT EMBED
        # ====================================================

        deposit_embed = discord.Embed(
            title='💰 Your Litecoin Deposit Address',
            description=(
                f'**Send Litecoin (LTC) to the address below.**\n\n'
                f'`{address}`\n\n'
                f'**Network:** Litecoin Mainnet\n'
                f'**Currency:** LTC\n\n'
                f'Your deposit will be credited after the required '
                f'confirmations.'
            ),
            color=GREEN,
            timestamp=datetime.now(timezone.utc)
        )

        deposit_embed.set_footer(
            text='LiteBet • Litecoin Deposits'
        )

        # ====================================================
        # SEND ADDRESS IN DM
        # ====================================================

        try:

            await ctx.author.send(
                embed=deposit_embed
            )

            await ctx.send(
                embed=emb(
                    'Deposit Address Sent',
                    'Your **Litecoin (LTC)** deposit address has been sent to your DMs.',
                    GREEN
                )
            )

        except discord.Forbidden:

            # ------------------------------------------------
            # DMs CLOSED
            # ------------------------------------------------

            await ctx.send(
                embed=emb(
                    'Unable to DM You',
                    (
                        'I could not send your deposit address '
                        'because your DMs are closed.\n\n'
                        f'**Your Litecoin Address:**\n'
                        f'`{address}`'
                    ),
                    RED
                )
            )

    except Exception as e:

        print(
            f'Deposit error for {ctx.author.id}: {e}'
        )

        await ctx.send(
            embed=emb(
                'Deposit Error',
                'I could not generate your Litecoin deposit address. Please try again.',
                RED
            )
        )
        
    
@bot.command(aliases=['bj'])
async def blackjack(ctx, amount: Decimal):
    if not await require_game(ctx, amount):
        return

    ranks = list('23456789') + ['10', 'J', 'Q', 'K', 'A']
    suits = ['S', 'H', 'D', 'C']

    deck = [(rank, suit) for rank in ranks for suit in suits]
    random.shuffle(deck)

    def card_value(card):
        rank = card[0]

        if rank == 'A':
            return 11

        if rank in ('J', 'Q', 'K'):
            return 10

        return int(rank)

    def hand_value(hand):
        total = sum(card_value(card) for card in hand)
        aces = sum(1 for card in hand if card[0] == 'A')

        while total > 21 and aces:
            total -= 10
            aces -= 1

        return total

    # -----------------------------
    # DEAL INITIAL CARDS
    # -----------------------------

    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    # -----------------------------
    # BLACKJACK VIEW
    # -----------------------------

    class BlackjackView(discord.ui.View):

        def __init__(self):
            super().__init__(timeout=180)

            self.finished = False
            self.message = None

        async def interaction_check(self, interaction):

            if interaction.user.id != ctx.author.id:

                await interaction.response.send_message(
                    'Only the player who started this game can use these buttons.',
                    ephemeral=True
                )

                return False

            return True

        # -----------------------------
        # CURRENT GAME EMBED
        # -----------------------------

        def game_embed(self):

            pv = hand_value(player)

            dealer_visible = card_value(dealer[0])

            return emb(
                'Blackjack',

                f'**Bet:** {money(amount)} points\n\n'

                f'🎴 **Dealer:** '
                f'`{dealer[0][0]}{dealer[0][1]}` `??`\n'
                f'**Dealer visible total:** `{dealer_visible}`\n\n'

                f'🃏 **Your hand:** '
                f'{" ".join(f"`{r}{s}`" for r, s in player)}\n'
                f'**Your total:** `{pv}`\n\n'

                f'Choose **Hit** or **Stand**.'
            )

        # -----------------------------
        # IMAGE HANDS
        # -----------------------------

        def image_hands(self, finished=False):

            if finished:
                return player, dealer

            # Dealer's second card is hidden
            hidden_dealer = [
                dealer[0],
                ('?', '?')
            ]

            return player, hidden_dealer

        # -----------------------------
        # CREATE CURRENT IMAGE
        # -----------------------------

        def create_game_image(self, finished=False):

            image_player, image_dealer = self.image_hands(
                finished
            )

            return blackjack_card(
                ctx.author.display_name,
                image_player,
                image_dealer
            )

        # -----------------------------
        # UPDATE GAME
        # -----------------------------

        async def update_game(self, interaction):

            if self.finished:
                return

            try:

                image_path = self.create_game_image(False)

                file = discord.File(
                    image_path,
                    filename='blackjack.png'
                )

                embed = self.game_embed()

                embed.set_image(
                    url='attachment://blackjack.png'
                )

                await interaction.response.edit_message(
                    embed=embed,
                    attachments=[file],
                    view=self
                )

            except Exception as e:

                print(
                    f'Blackjack update image error: {e}'
                )

                await interaction.response.edit_message(
                    embed=self.game_embed(),
                    view=self
                )

        # -----------------------------
        # FINISH GAME
        # -----------------------------

        async def finish_game(self, interaction):

            if self.finished:
                return

            self.finished = True

            self.stop()

            # Disable buttons
            for button in self.children:
                button.disabled = True

            pv = hand_value(player)

            # -----------------------------
            # DEALER PLAYS
            # -----------------------------

            while hand_value(dealer) < 17:

                if not deck:
                    break

                dealer.append(deck.pop())

            dv = hand_value(dealer)

            # -----------------------------
            # DETERMINE RESULT
            # -----------------------------

            if pv > 21:

                result_type = 'loss'
                payout = Decimal('0')

                title = 'Blackjack — Bust!'
                colour = RED

                result = (
                    '💥 **You busted!**\n'
                    'Your total went over 21.'
                )

            elif dv > 21:

                result_type = 'win'

                payout = (
                    amount * Decimal('1.95')
                ).quantize(
                    Decimal('0.01')
                )

                title = 'Blackjack — You Win!'
                colour = GREEN

                result = (
                    '🎉 **Dealer busted!**\n\n'
                    f'🎊 **Payout:** {money(payout)} points'
                )

            elif pv > dv:

                result_type = 'win'

                payout = (
                    amount * Decimal('1.95')
                ).quantize(
                    Decimal('0.01')
                )

                title = 'Blackjack — You Win!'
                colour = GREEN

                result = (
                    '🏆 **You beat the dealer!**\n\n'
                    f'🎊 **Payout:** {money(payout)} points'
                )

            elif pv < dv:

                result_type = 'loss'
                payout = Decimal('0')

                title = 'Blackjack — Dealer Wins'
                colour = RED

                result = (
                    '💀 **Dealer wins.**'
                )

            else:

                result_type = 'push'
                payout = Decimal('0')

                title = 'Blackjack — Push!'
                colour = NAVY

                result = (
                    '🤝 **Push!**\n'
                    f'Your **{money(amount)} points** bet '
                    'was refunded.'
                )

                # Return original bet
                await db.balance(
                    ctx.author.id,
                    amount
                )

            # -----------------------------
            # PAY WINNER
            # -----------------------------

            if result_type == 'win':

                await db.balance(
                    ctx.author.id,
                    payout
                )

            # -----------------------------
            # DATABASE RECORD
            # -----------------------------

            await db.record(
                ctx.author.id,
                'Blackjack',
                amount,
                result_type,
                payout
                if result_type == 'win'
                else Decimal('0')
            )

            # -----------------------------
            # FINAL IMAGE
            # -----------------------------

            try:

                image_path = self.create_game_image(
                    True
                )

                file = discord.File(
                    image_path,
                    filename='blackjack.png'
                )

                final_embed = emb(
                    title,

                    f'**Bet:** {money(amount)} points\n\n'

                    f'🃏 **Your hand:** '
                    f'{" ".join(f"`{r}{s}`" for r, s in player)}\n'
                    f'**Your total:** `{pv}`\n\n'

                    f'🎴 **Dealer hand:** '
                    f'{" ".join(f"`{r}{s}`" for r, s in dealer)}\n'
                    f'**Dealer total:** `{dv}`\n\n'

                    f'{result}',

                    colour
                )

                final_embed.set_image(
                    url='attachment://blackjack.png'
                )

                await interaction.response.edit_message(
                    embed=final_embed,
                    attachments=[file],
                    view=None
                )

            except Exception as e:

                print(
                    f'Blackjack final image error: {e}'
                )

                await interaction.response.edit_message(
                    embed=emb(
                        title,

                        f'**Bet:** {money(amount)} points\n\n'
                        f'🃏 Your total: **{pv}**\n'
                        f'🎴 Dealer total: **{dv}**\n\n'
                        f'{result}',

                        colour
                    ),
                    view=None
                )

        # -----------------------------
        # HIT
        # -----------------------------

        @discord.ui.button(
            label='Hit',
            emoji='🃏',
            style=discord.ButtonStyle.primary
        )
        async def hit(
            self,
            interaction,
            button
        ):

            if self.finished:
                return

            if not deck:
                return await self.finish_game(
                    interaction
                )

            player.append(
                deck.pop()
            )

            pv = hand_value(player)

            # Bust or 21 = automatically finish
            if pv >= 21:

                await self.finish_game(
                    interaction
                )

                return

            await self.update_game(
                interaction
            )

        # -----------------------------
        # STAND
        # -----------------------------

        @discord.ui.button(
            label='Stand',
            emoji='✋',
            style=discord.ButtonStyle.success
        )
        async def stand(
            self,
            interaction,
            button
        ):

            if self.finished:
                return

            await self.finish_game(
                interaction
            )

        # -----------------------------
        # TIMEOUT
        # -----------------------------

        async def on_timeout(self):

            if self.finished:
                return

            self.finished = True

            self.stop()

            for button in self.children:
                button.disabled = True

            try:

                if self.message:

                    await self.message.edit(
                        embed=emb(
                            'Blackjack — Expired',

                            'You took too long to make a move.\n\n'
                            f'**Bet lost:** '
                            f'{money(amount)} points',

                            RED
                        ),
                        view=None
                    )

            except Exception as e:

                print(
                    f'Blackjack timeout error: {e}'
                )

    # -----------------------------
    # NATURAL BLACKJACK CHECK
    # -----------------------------

    player_blackjack = (
        len(player) == 2
        and hand_value(player) == 21
    )

    dealer_blackjack = (
        len(dealer) == 2
        and hand_value(dealer) == 21
    )

    # -----------------------------
    # NATURAL BLACKJACK
    # -----------------------------

    if player_blackjack or dealer_blackjack:

        if player_blackjack and not dealer_blackjack:

            payout = (
                amount * Decimal('2.00')
            ).quantize(
                Decimal('0.01')
            )

            await db.balance(
                ctx.author.id,
                payout
            )

            await db.record(
                ctx.author.id,
                'Blackjack',
                amount,
                'win',
                payout
            )

            title = 'Blackjack — Natural Blackjack!'
            colour = GREEN

            result = (
                '🎉 **Natural Blackjack!**\n\n'
                f'🎊 **Payout:** {money(payout)} points'
            )

        elif dealer_blackjack and not player_blackjack:

            await db.record(
                ctx.author.id,
                'Blackjack',
                amount,
                'loss',
                Decimal('0')
            )

            title = 'Blackjack — Dealer Blackjack'
            colour = RED

            result = (
                '💀 **Dealer has Blackjack.**'
            )

        else:

            await db.balance(
                ctx.author.id,
                amount
            )

            await db.record(
                ctx.author.id,
                'Blackjack',
                amount,
                'push',
                Decimal('0')
            )

            title = 'Blackjack — Push!'
            colour = NAVY

            result = (
                '🤝 **Both have Blackjack!**\n'
                f'Your **{money(amount)} points** '
                'were refunded.'
            )

        # Final natural blackjack image
        try:

            image_path = blackjack_card(
                ctx.author.display_name,
                player,
                dealer
            )

            file = discord.File(
                image_path,
                filename='blackjack.png'
            )

            final_embed = emb(
                title,

                f'**Bet:** {money(amount)} points\n\n'

                f'🃏 **Your hand:** '
                f'{" ".join(f"`{r}{s}`" for r, s in player)}\n'
                f'**Your total:** `{hand_value(player)}`\n\n'

                f'🎴 **Dealer hand:** '
                f'{" ".join(f"`{r}{s}`" for r, s in dealer)}\n'
                f'**Dealer total:** `{hand_value(dealer)}`\n\n'

                f'{result}',

                colour
            )

            final_embed.set_image(
                url='attachment://blackjack.png'
            )

            return await ctx.send(
                embed=final_embed,
                file=file
            )

        except Exception as e:

            print(
                f'Blackjack natural image error: {e}'
            )

            return await ctx.send(
                embed=emb(
                    title,
                    result,
                    colour
                )
            )

    # -----------------------------
    # START NORMAL GAME
    # -----------------------------

    view = BlackjackView()

    try:

        image_player, image_dealer = (
            view.image_hands(False)
        )

        image_path = blackjack_card(
            ctx.author.display_name,
            image_player,
            image_dealer
        )

        file = discord.File(
            image_path,
            filename='blackjack.png'
        )

        starting_embed = view.game_embed()

        starting_embed.set_image(
            url='attachment://blackjack.png'
        )

        message = await ctx.send(
            embed=starting_embed,
            file=file,
            view=view
        )

    except Exception as e:

        print(
            f'Blackjack starting image error: {e}'
        )

        message = await ctx.send(
            embed=view.game_embed(),
            view=view
        )

    view.message = message
    
@bot.command()
async def hilo(ctx, amount: Decimal):
    if not await require_game(ctx,amount): return
    ranks=['2','3','4','5','6','7','8','9','10','J','Q','K','A']; first,second=random.choice(ranks),random.choice(ranks)
    high=ranks.index(second)>=ranks.index(first); won=random.choice([True,False])
    s,c,h=seed(); await finish(ctx,'Hi-Lo',amount,won,Decimal('1.70'),f'Card: **{first}** → Next card: **{second}**\nResult: **{"Higher" if high else "Lower"}**\n🔒 Hash: `{h}`\nServer Seed: `{s}`\nClient Seed: `{c}`')


@bot.command()
async def tower(ctx, amount: Decimal, difficulty: str = None):

    difficulties = {
        'easy': {
            'slots': 4,
            'bombs': 1,
            'multipliers': [
                Decimal('1.26'),
                Decimal('1.59'),
                Decimal('2.00'),
                Decimal('2.52'),
                Decimal('3.18'),
                Decimal('4.00'),
                Decimal('5.03'),
                Decimal('6.35')
            ]
        },
        'medium': {
            'slots': 4,
            'bombs': 2,
            'multipliers': [
                Decimal('1.30'),
                Decimal('2.00'),
                Decimal('2.84'),
                Decimal('4.02'),
                Decimal('5.77'),
                Decimal('8.20'),
                Decimal('11.54'),
                Decimal('15.66')
            ]
        },
        'hard': {
            'slots': 2,
            'bombs': 1,
            'multipliers': [
                Decimal('1.91'),
                Decimal('3.65'),
                Decimal('6.97'),
                Decimal('13.31'),
                Decimal('25.44'),
                Decimal('48.50'),
                Decimal('92.72'),
                Decimal('150.00')
            ]
        }
    }

    if difficulty is None:

        return await ctx.send(
            embed=emb(
                'Tower',
                '**Choose a difficulty:**\n\n'
                '💚 **Easy**\n'
                '3 Diamonds • 1 Bomb\n'
                '4 tiles per row\n\n'
                '💛 **Medium**\n'
                '2 Diamonds • 2 Bombs\n'
                '4 tiles per row\n\n'
                '❤️ **Hard**\n'
                '1 Diamond • 1 Bomb\n'
                '2 tiles per row\n\n'
                'Usage:\n'
                '` .tower 50 easy`\n'
                '` .tower 50 medium`\n'
                '` .tower 50 hard`',
                NAVY
            )
        )

    difficulty = difficulty.lower()

    if difficulty not in difficulties:
        return await ctx.send(
            embed=emb(
                'Invalid difficulty',
                'Choose **easy**, **medium**, or **hard**.',
                RED
            )
        )

    if amount <= 0:
        return await ctx.send(
            embed=emb(
                'Invalid bet',
                'Bet amount must be greater than zero.',
                RED
            )
        )

    if not await require_game(ctx, amount):
        return

    config = difficulties[difficulty]

    slots = config['slots']
    bombs = config['bombs']
    multipliers = config['multipliers']

    # Create the tower.
    rows = []

    for _ in range(8):

        row = ['diamond'] * (slots - bombs)
        row += ['bomb'] * bombs

        random.shuffle(row)
        rows.append(row)

    class TowerView(discord.ui.View):

        def __init__(self):
            super().__init__(timeout=180)

            self.current_row = 0
            self.finished = False
            self.message = None

        async def interaction_check(self, interaction):

            if interaction.user.id != ctx.author.id:

                await interaction.response.send_message(
                    'Only the player who started this game can use these buttons.',
                    ephemeral=True
                )

                return False

            return True

        def current_multiplier(self):

            if self.current_row <= 0:
                return Decimal('1.00')

            return multipliers[self.current_row - 1]

        def board_embed(self):

            multiplier = self.current_multiplier()

            next_multiplier = (
                multipliers[self.current_row]
                if self.current_row < 8
                else multipliers[-1]
            )

            return emb(
                f'Tower — {difficulty.title()}',
                f'**Bet:** {money(amount)} points\n\n'
                f'**Floor:** `{self.current_row + 1}/8`\n'
                f'**Current:** `{multiplier:.2f}×`\n'
                f'**Next:** `{next_multiplier:.2f}×`\n\n'
                f'Choose a tile to climb the tower.',
                NAVY
            )

        async def update_board(self, interaction):

            path = tower_card(
                rows,
                self.current_row,
                difficulty,
                ctx.author.display_name
            )

            file = discord.File(
                path,
                filename='tower.png'
            )

            embed = self.board_embed()

            embed.set_image(
                url='attachment://tower.png'
            )

            await interaction.response.edit_message(
                embed=embed,
                attachments=[file],
                view=self
            )

        async def lose(self, interaction):

            if self.finished:
                return

            self.finished = True
            self.stop()

            for button in self.children:
                button.disabled = True

            path = tower_card(
                rows,
                8,
                difficulty,
                ctx.author.display_name
            )

            file = discord.File(
                path,
                filename='tower.png'
            )

            floor = self.current_row + 1

            await db.record(
                ctx.author.id,
                'Tower',
                amount,
                'loss',
                Decimal('0')
            )

            embed = emb(
                'Tower — Boom!',
                f'💣 You hit a bomb on **Floor {floor}**.\n\n'
                f'**Lost:** {money(amount)} points',
                RED
            )

            embed.set_image(
                url='attachment://tower.png'
            )

            await interaction.response.edit_message(
                embed=embed,
                attachments=[file],
                view=None
            )

        async def cashout(self, interaction):

            if self.finished:
                return

            self.finished = True
            self.stop()

            for button in self.children:
                button.disabled = True

            multiplier = self.current_multiplier()

            payout = (
                amount * multiplier
            ).quantize(
                Decimal('0.01')
            )

            path = tower_card(
                rows,
                self.current_row,
                difficulty,
                ctx.author.display_name
            )

            file = discord.File(
                path,
                filename='tower.png'
            )

            await db.record(
                ctx.author.id,
                'Tower',
                amount,
                'win',
                payout
            )

            embed = emb(
                'Tower — Cashed Out!',
                f'💰 **You cashed out!**\n\n'
                f'**Floor:** {self.current_row}/8\n'
                f'**Multiplier:** `{multiplier:.2f}×`\n'
                f'**Payout:** {money(payout)} points',
                GREEN
            )

            embed.set_image(
                url='attachment://tower.png'
            )

            await interaction.response.edit_message(
                embed=embed,
                attachments=[file],
                view=None
            )

        async def choose(self, interaction, column):

            if self.finished:
                return

            if self.current_row >= 8:
                return

            result = rows[self.current_row][column]

            if result == 'bomb':
                await self.lose(interaction)
                return

            # Safe tile.
            self.current_row += 1

            # Reached the final floor.
            if self.current_row >= 8:

                self.finished = True
                self.stop()

                for button in self.children:
                    button.disabled = True

                multiplier = multipliers[-1]

                payout = (
                    amount * multiplier
                ).quantize(
                    Decimal('0.01')
                )

                path = tower_card(
                    rows,
                    8,
                    difficulty,
                    ctx.author.display_name
                )

                file = discord.File(
                    path,
                    filename='tower.png'
                )

                await db.record(
                    ctx.author.id,
                    'Tower',
                    amount,
                    'win',
                    payout
                )

                embed = emb(
                    'Tower — Completed!',
                    f'🏆 **You reached Floor 8!**\n\n'
                    f'**Multiplier:** `{multiplier:.2f}×`\n'
                    f'**Payout:** {money(payout)} points',
                    GREEN
                )

                embed.set_image(
                    url='attachment://tower.png'
                )

                await interaction.response.edit_message(
                    embed=embed,
                    attachments=[file],
                    view=None
                )

                return

            await self.update_board(interaction)

        @discord.ui.button(
            label='1',
            style=discord.ButtonStyle.primary
        )
        async def tile1(self, interaction, button):
            await self.choose(interaction, 0)

        @discord.ui.button(
            label='2',
            style=discord.ButtonStyle.primary
        )
        async def tile2(self, interaction, button):
            await self.choose(interaction, 1)

        @discord.ui.button(
            label='3',
            style=discord.ButtonStyle.primary
        )
        async def tile3(self, interaction, button):
            await self.choose(interaction, 2)

        @discord.ui.button(
            label='4',
            style=discord.ButtonStyle.primary
        )
        async def tile4(self, interaction, button):
            await self.choose(interaction, 3)

        @discord.ui.button(
            label='Cashout',
            style=discord.ButtonStyle.success
        )
        async def cashout_button(self, interaction, button):
            await self.cashout(interaction)

        async def on_timeout(self):

            if self.finished:
                return

            self.finished = True
            self.stop()

            for button in self.children:
                button.disabled = True

            try:

                if self.message:

                    await self.message.edit(
                        embed=emb(
                            'Tower — Expired',
                            f'You took too long to play.\n\n'
                            f'**Lost:** {money(amount)} points',
                            RED
                        ),
                        view=None
                    )

                    await db.record(
                        ctx.author.id,
                        'Tower',
                        amount,
                        'loss',
                        Decimal('0')
                    )

            except Exception as e:
                print(f'Tower timeout error: {e}')

    view = TowerView()

    # Disable buttons 3 and 4 on Hard.
    if difficulty == 'hard':

        view.tile3.disabled = True
        view.tile4.disabled = True

    path = tower_card(
        rows,
        0,
        difficulty,
        ctx.author.display_name
    )

    file = discord.File(
        path,
        filename='tower.png'
    )

    embed = view.board_embed()

    embed.set_image(
        url='attachment://tower.png'
    )

    message = await ctx.send(
        embed=embed,
        file=file,
        view=view
    )

    view.message = message
    
@bot.command()
async def rps(ctx, member: discord.Member, amount: Decimal):
    if member.bot or member==ctx.author: return await ctx.send(embed=emb('Invalid opponent','Choose another member.',RED))
    if not await require_game(ctx,amount) or not await db.take_bet(member.id,amount):
        if await db.user(ctx.author.id): await db.balance(ctx.author.id,amount)
        return await ctx.send(embed=emb('Challenge unavailable','Both players need the bet amount.',RED))
    winner=random.choice([ctx.author,member]); await db.balance(winner.id,amount*Decimal('2'))
    await ctx.send(embed=emb('Rock / Paper / Scissors',f'{ctx.author.mention} vs {member.mention}\nWinner: {winner.mention}\nPrize: **{money(amount*2)} points**',GREEN))

@bot.command()
async def ttt(ctx, member: discord.Member, amount: Decimal):
    if member.bot or member==ctx.author: return await ctx.send(embed=emb('Invalid opponent','Choose another member.',RED))
    if not await require_game(ctx,amount) or not await db.take_bet(member.id,amount):
        await db.balance(ctx.author.id,amount); return await ctx.send(embed=emb('Challenge unavailable','Both players need the bet amount.',RED))
    winner=random.choice([ctx.author,member]); await db.balance(winner.id,amount*2)
    await ctx.send(embed=emb('Tic-Tac-Toe',f'🎮 {ctx.author.mention} (❌) vs {member.mention} (⭕)\nWinner: {winner.mention}\nPrize: **{money(amount*2)} points**',GREEN))



# ============================================================
# LEADERBOARD
# ============================================================

class LeaderboardView(discord.ui.View):

    def __init__(self, author_id):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.period = "daily"

    async def update_leaderboard(self, interaction):

        rows = await db.pool.fetch(
            """
            SELECT user_id, daily_wager, weekly_wager, monthly_wager
            FROM users
            ORDER BY
                CASE
                    WHEN $1 = 'daily' THEN daily_wager
                    WHEN $1 = 'weekly' THEN weekly_wager
                    WHEN $1 = 'monthly' THEN monthly_wager
                END DESC
            LIMIT 10
            """,
            self.period
        )

        if self.period == "daily":
            title = "🏆 Daily Leaderboard"
            period_text = "Daily"
        elif self.period == "weekly":
            title = "🏆 Weekly Leaderboard"
            period_text = "Weekly"
        else:
            title = "🏆 Monthly Leaderboard"
            period_text = "Monthly"

        if not rows:
            description = "No wagers yet."
        else:
            lines = []

            medals = ["🥇", "🥈", "🥉"]

            for i, row in enumerate(rows, start=1):

                user = interaction.guild.get_member(
                    row["user_id"]
                )

                if user:
                    username = user.display_name
                else:
                    try:
                        user = await bot.fetch_user(
                            row["user_id"]
                        )
                        username = user.display_name
                    except Exception:
                        username = "Unknown User"

                if self.period == "daily":
                    wager = row["daily_wager"]
                elif self.period == "weekly":
                    wager = row["weekly_wager"]
                else:
                    wager = row["monthly_wager"]

                if i <= 3:
                    rank = medals[i - 1]
                else:
                    rank = f"`{i}`"

                lines.append(
                    f"{rank} **{username}** — "
                    f"**{money(wager)} points**"
                )

            description = "\n".join(lines)

        embed = discord.Embed(
            title=title,
            description=(
                f"Showing the **Top 10 {period_text.lower()} wagers**.\n\n"
                f"{description}"
            ),
            color=0x5865F2
        )

        embed.set_footer(
            text="LiteBet • Leaderboard"
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self
        )

    # ========================================================
    # DAILY
    # ========================================================

    @discord.ui.button(
        label="Daily",
        style=discord.ButtonStyle.success
    )
    async def daily(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        self.period = "daily"

        await self.update_leaderboard(
            interaction
        )

    # ========================================================
    # WEEKLY
    # ========================================================

    @discord.ui.button(
        label="Weekly",
        style=discord.ButtonStyle.primary
    )
    async def weekly(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        self.period = "weekly"

        await self.update_leaderboard(
            interaction
        )

    # ========================================================
    # MONTHLY
    # ========================================================

    @discord.ui.button(
        label="Monthly",
        style=discord.ButtonStyle.primary
    )
    async def monthly(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        self.period = "monthly"

        await self.update_leaderboard(
            interaction
        )

    # ========================================================
    # ONLY COMMAND USER CAN USE BUTTONS
    # ========================================================

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ):

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "❌ Only the person who opened this leaderboard can use these buttons.",
                ephemeral=True
            )

            return False

        return True


# ============================================================
# LEADERBOARD COMMAND
# ============================================================

@bot.command(aliases=['lb'])
async def leaderboard(ctx):

    rows = await db.pool.fetch(
        """
        SELECT user_id, daily_wager, weekly_wager, monthly_wager
        FROM users
        ORDER BY daily_wager DESC
        LIMIT 10
        """
    )

    if not rows:

        embed = discord.Embed(
            title="🏆 Leaderboard",
            description="No wagers yet.",
            color=0x5865F2
        )

    else:

        lines = []

        medals = ["🥇", "🥈", "🥉"]

        for i, row in enumerate(rows, start=1):

            member = ctx.guild.get_member(
                row["user_id"]
            )

            if member:
                username = member.display_name
            else:
                try:
                    user = await bot.fetch_user(
                        row["user_id"]
                    )
                    username = user.display_name
                except Exception:
                    username = "Unknown User"

            if i <= 3:
                rank = medals[i - 1]
            else:
                rank = f"`{i}`"

            lines.append(
                f"{rank} **{username}** — "
                f"**{money(row['daily_wager'])} points**"
            )

        embed = discord.Embed(
            title="🏆 Leaderboard",
            description=(
                "**Showing Top 10 daily wagers.**\n\n"
                + "\n".join(lines)
            ),
            color=0x5865F2
        )

    embed.set_footer(
        text="LiteBet • Leaderboard"
    )

    view = LeaderboardView(
        ctx.author.id
    )

    await ctx.send(
        embed=embed,
        view=view
    )


# ============================================================
# HOUSE WALLET
# ============================================================

HOUSE_LTC = Decimal("0")
HOUSE_USD = Decimal("0")

LTC_EMOJI = "<:SA_LTC_LiteCoin:1538206742201106542>"


@bot.command(aliases=["hb"])
async def housebal(ctx):

    embed = discord.Embed(
        title="🏦 House Wallet",
        description=(
            f"**House money:**\n"
            f"　• {LTC_EMOJI} **{HOUSE_LTC:.8f} LTC** "
            f"(${HOUSE_USD:,.2f})"
        ),
        color=GREEN
    )

    await ctx.send(embed=embed)


@bot.command(hidden=True)
@commands.is_owner()
async def sethb(ctx, ltc: str, usd: str):

    global HOUSE_LTC, HOUSE_USD

    try:
        new_ltc = Decimal(ltc)
        new_usd = Decimal(usd)
    except (InvalidOperation, ValueError):
        return await ctx.send(
            embed=emb(
                "Invalid Amount",
                "Usage: `.sethb <LTC> <USD>`\n\n"
                "Example: `.sethb 1.29480000 129.48`",
                RED
            )
        )

    if new_ltc < 0 or new_usd < 0:
        return await ctx.send(
            embed=emb(
                "Invalid Amount",
                "House balance cannot be negative.",
                RED
            )
        )

    HOUSE_LTC = new_ltc.quantize(Decimal("0.00000001"))
    HOUSE_USD = new_usd.quantize(Decimal("0.01"))

    await ctx.send(
        embed=emb(
            "House Wallet Updated",
            f"House balance set to:\n"
            f"{LTC_EMOJI} **{HOUSE_LTC:.8f} LTC** "
            f"(${HOUSE_USD:,.2f})",
            GREEN
        )
    )
    

# ============================================================
# WITHDRAW SYSTEM
# ============================================================

WITHDRAWAL_LOG_CHANNEL = None

# Minimum withdrawal = 111 points
MIN_WITHDRAWAL = Decimal("111.00")

# 1 point = 0.0001 LTC
POINT_TO_LTC = Decimal("0.0001")


# ============================================================
# LTC ADDRESS VALIDATION
# ============================================================

def valid_ltc_address(address):

    if not address:
        return False

    address = address.strip()

    # Litecoin bech32
    if address.startswith("ltc1"):
        return 43 <= len(address) <= 100

    # Litecoin legacy
    if address.startswith(("L", "M", "3")):
        return 26 <= len(address) <= 35

    return False


# ============================================================
# SET WITHDRAWAL LOG CHANNEL
# ============================================================

@bot.command()
@commands.has_permissions(administrator=True)
async def wlog2(ctx, channel: discord.TextChannel):

    global WITHDRAWAL_LOG_CHANNEL

    WITHDRAWAL_LOG_CHANNEL = channel.id

    await ctx.send(
        embed=emb(
            "Withdrawal Logs Updated",
            (
                f"All withdrawal requests will now be sent to "
                f"{channel.mention}.\n\n"
                f"Admins can **Accept** or **Decline** withdrawals "
                f"directly from the log."
            ),
            GREEN
        )
    )


# ============================================================
# WITHDRAW COMMAND
# ============================================================

@bot.command()
async def withdraw(ctx, points: str, address: str):

    # ========================================================
    # AMOUNT
    # ========================================================

    try:

        amount = Decimal(points).quantize(
            Decimal("0.01")
        )

    except (InvalidOperation, ValueError):

        return await ctx.send(
            embed=emb(
                "Invalid Amount",
                (
                    "Please enter a valid withdrawal amount.\n\n"
                    "Example:\n"
                    "`.withdraw 111 Lxxxxxxxxxxxxxxxxxxxxxxxx`"
                ),
                RED
            )
        )

    # ========================================================
    # MINIMUM WITHDRAWAL
    # ========================================================

    if amount < MIN_WITHDRAWAL:

        return await ctx.send(
            embed=emb(
                "Minimum Withdrawal",
                (
                    f"The minimum withdrawal is "
                    f"**{money(MIN_WITHDRAWAL)} points**.\n\n"
                    f"Minimum: **111 points**"
                ),
                RED
            )
        )

    # ========================================================
    # LTC ADDRESS
    # ========================================================

    address = address.strip()

    if not valid_ltc_address(address):

        return await ctx.send(
            embed=emb(
                "Invalid Litecoin Address",
                (
                    "Please enter a valid Litecoin Mainnet address."
                ),
                RED
            )
        )

    # ========================================================
    # CHECK BALANCE
    # ========================================================

    user = await db.user(
        ctx.author.id
    )

    if not user:

        return await ctx.send(
            embed=emb(
                "Account Error",
                "Your account could not be found.",
                RED
            )
        )

    balance = Decimal(
        str(user["balance"])
    )

    if balance < amount:

        return await ctx.send(
            embed=emb(
                "Insufficient Balance",
                (
                    f"You requested **{money(amount)} points** "
                    f"but only have **{money(balance)} points**."
                ),
                RED
            )
        )

    # ========================================================
    # POINTS -> LTC
    # ========================================================

    ltc_amount = (
        amount * POINT_TO_LTC
    ).quantize(
        Decimal("0.00000001")
    )

    # ========================================================
    # REQUEST ID
    # ========================================================

    request_id = secrets.token_hex(
        8
    ).upper()

    # ========================================================
    # INITIAL REQUEST MESSAGE
    # ========================================================

    confirmation = discord.Embed(
        title="💸 Litecoin Withdrawal Requested",
        description=(
            f"**{ctx.author.mention}**, your withdrawal request "
            f"has been submitted.\n\n"

            f"**Points:** `{money(amount)}`\n"
            f"**LTC Amount:** `{ltc_amount:.8f} LTC`\n\n"

            f"**Litecoin Address:**\n"
            f"```{address}```\n\n"

            f"**Request ID:** `{request_id}`\n\n"

            f"⏳ **Status:** `PENDING`\n\n"

            f"An administrator will review your withdrawal."
        ),
        color=0xF0B90B,
        timestamp=datetime.now(timezone.utc)
    )

    confirmation.add_field(
        name="Request Location",
        value=(
            f"Server: **{ctx.guild.name if ctx.guild else 'DM'}**\n"
            f"Channel: "
            f"{ctx.channel.mention if hasattr(ctx.channel, 'mention') else ctx.channel}"
        ),
        inline=False
    )

    confirmation.set_footer(
        text="LiteBet • Withdrawal System"
    )

    message = await ctx.send(
        embed=confirmation
    )

    # ========================================================
    # SAVE PENDING WITHDRAWAL
    # ========================================================

    try:

        await db.pool.execute(
            """
            INSERT INTO withdrawals
            (user_id, address, points, ltc, status)
            VALUES ($1, $2, $3, $4, 'pending')
            """,
            ctx.author.id,
            address,
            amount,
            ltc_amount
        )

    except Exception as e:

        print(
            f"Withdrawal pending DB error: {e}"
        )

    # ========================================================
    # WITHDRAWAL LOG CHANNEL
    # ========================================================

    if WITHDRAWAL_LOG_CHANNEL:

        log_channel = bot.get_channel(
            WITHDRAWAL_LOG_CHANNEL
        )

        if log_channel:

            # ------------------------------------------------
            # ADMIN WITHDRAWAL VIEW
            # ------------------------------------------------

            class WithdrawalAdminView(discord.ui.View):

                def __init__(self):

                    super().__init__(
                        timeout=None
                    )

                    self.processed = False

                # ============================================
                # ADMIN CHECK
                # ============================================

                async def interaction_check(
                    self,
                    interaction
                ):

                    if not interaction.user.guild_permissions.administrator:

                        await interaction.response.send_message(
                            "❌ Only administrators can process withdrawals.",
                            ephemeral=True
                        )

                        return False

                    if self.processed:

                        await interaction.response.send_message(
                            "❌ This withdrawal has already been processed.",
                            ephemeral=True
                        )

                        return False

                    return True

                # ============================================
                # ACCEPT
                # ============================================

                @discord.ui.button(
                    label="Accept",
                    emoji="✅",
                    style=discord.ButtonStyle.success
                )
                async def accept(
                    self,
                    interaction,
                    button
                ):

                    if self.processed:
                        return

                    self.processed = True

                    # ----------------------------------------
                    # ATOMIC BALANCE CHECK + DEDUCTION
                    # ----------------------------------------

                    new_balance = await db.pool.fetchval(
                        """
                        UPDATE users
                        SET
                            balance = balance - $2,
                            withdrawals = withdrawals + $2
                        WHERE user_id = $1
                          AND balance >= $2
                        RETURNING balance
                        """,
                        ctx.author.id,
                        amount
                    )

                    # ----------------------------------------
                    # NOT ENOUGH BALANCE
                    # ----------------------------------------

                    if new_balance is None:

                        self.processed = False

                        await interaction.response.send_message(
                            (
                                "❌ This withdrawal cannot be paid "
                                "because the user no longer has "
                                "enough balance."
                            ),
                            ephemeral=True
                        )

                        return

                    # ----------------------------------------
                    # UPDATE DATABASE
                    # ----------------------------------------

                    try:

                        await db.pool.execute(
                            """
                            UPDATE withdrawals
                            SET status = 'paid'
                            WHERE user_id = $1
                              AND address = $2
                              AND points = $3
                              AND status = 'pending'
                            """,
                            ctx.author.id,
                            address,
                            amount
                        )

                    except Exception as e:

                        print(
                            f"Withdrawal paid DB error: {e}"
                        )

                    # ----------------------------------------
                    # DISABLE BUTTONS
                    # ----------------------------------------

                    for child in self.children:
                        child.disabled = True

                    # ----------------------------------------
                    # ADMIN WHO PAID
                    # ----------------------------------------

                    admin_name = interaction.user.display_name

                    # ----------------------------------------
                    # TIME
                    # ----------------------------------------

                    paid_time = datetime.now(
                        timezone.utc
                    )

                    # ----------------------------------------
                    # PAID LOG
                    # ----------------------------------------

                    paid_embed = discord.Embed(
                        title="💰 Withdrawal — PAID",
                        description=(
                            f"**Amount:** "
                            f"`{money(amount)} points`\n\n"

                            f"**LTC Amount:** "
                            f"`{ltc_amount:.8f} LTC`\n\n"

                            f"**LTC Address:**\n"
                            f"```{address}```\n\n"

                            f"**User:** "
                            f"{ctx.author.mention} "
                            f"`{ctx.author.id}`\n\n"

                            f"**Status:** `PAID`\n"
                            f"**By:** {interaction.user.mention}\n"
                            f"**Time:** "
                            f"<t:{int(paid_time.timestamp())}:F>\n\n"

                            f"**Request ID:** `{request_id}`"
                        ),
                        color=GREEN,
                        timestamp=paid_time
                    )

                    paid_embed.set_footer(
                        text="LiteBet • Withdrawal Logs"
                    )

                    # ----------------------------------------
                    # RESPOND
                    # ----------------------------------------

                    await interaction.response.edit_message(
                        embed=paid_embed,
                        view=self
                    )

                    # ----------------------------------------
                    # UPDATE ORIGINAL USER MESSAGE
                    # ----------------------------------------

                    try:

                        user_embed = discord.Embed(
                            title="💰 Withdrawal Paid",
                            description=(
                                f"Your withdrawal has been "
                                f"approved and paid.\n\n"

                                f"**Points:** "
                                f"`{money(amount)}`\n"

                                f"**LTC:** "
                                f"`{ltc_amount:.8f} LTC`\n\n"

                                f"**Address:**\n"
                                f"```{address}```\n\n"

                                f"**Status:** `PAID`\n"
                                f"**Request ID:** `{request_id}`"
                            ),
                            color=GREEN,
                            timestamp=paid_time
                        )

                        user_embed.set_footer(
                            text="LiteBet • Withdrawal"
                        )

                        await message.edit(
                            embed=user_embed
                        )

                    except Exception as e:

                        print(
                            f"Withdrawal user message update error: {e}"
                        )

                    # ----------------------------------------
                    # DM USER
                    # ----------------------------------------

                    try:

                        dm = discord.Embed(
                            title="💰 Withdrawal Paid",
                            description=(
                                f"Your withdrawal has been "
                                f"approved.\n\n"

                                f"**Points:** "
                                f"`{money(amount)}`\n"

                                f"**LTC:** "
                                f"`{ltc_amount:.8f} LTC`\n\n"

                                f"**Address:**\n"
                                f"```{address}```\n\n"

                                f"**Status:** `PAID`\n"
                                f"**Request ID:** `{request_id}`"
                            ),
                            color=GREEN,
                            timestamp=paid_time
                        )

                        await ctx.author.send(
                            embed=dm
                        )

                    except Exception:
                        pass

                # ============================================
                # DECLINE
                # ============================================

                @discord.ui.button(
                    label="Decline",
                    emoji="❌",
                    style=discord.ButtonStyle.danger
                )
                async def decline(
                    self,
                    interaction,
                    button
                ):

                    if self.processed:
                        return

                    self.processed = True

                    # ----------------------------------------
                    # UPDATE DATABASE
                    # ----------------------------------------

                    try:

                        await db.pool.execute(
                            """
                            UPDATE withdrawals
                            SET status = 'declined'
                            WHERE user_id = $1
                              AND address = $2
                              AND points = $3
                              AND status = 'pending'
                            """,
                            ctx.author.id,
                            address,
                            amount
                        )

                    except Exception as e:

                        print(
                            f"Withdrawal decline DB error: {e}"
                        )

                    # ----------------------------------------
                    # DISABLE BUTTONS
                    # ----------------------------------------

                    for child in self.children:
                        child.disabled = True

                    # ----------------------------------------
                    # ADMIN
                    # ----------------------------------------

                    admin_name = interaction.user.display_name

                    declined_time = datetime.now(
                        timezone.utc
                    )

                    # ----------------------------------------
                    # DECLINED LOG
                    # ----------------------------------------

                    declined_embed = discord.Embed(
                        title="❌ Withdrawal — DECLINED",
                        description=(
                            f"**Amount:** "
                            f"`{money(amount)} points`\n\n"

                            f"**LTC Amount:** "
                            f"`{ltc_amount:.8f} LTC`\n\n"

                            f"**LTC Address:**\n"
                            f"```{address}```\n\n"

                            f"**User:** "
                            f"{ctx.author.mention} "
                            f"`{ctx.author.id}`\n\n"

                            f"**Status:** `DECLINED`\n"
                            f"**By:** {interaction.user.mention}\n"
                            f"**Time:** "
                            f"<t:{int(declined_time.timestamp())}:F>\n\n"

                            f"**Request ID:** `{request_id}`"
                        ),
                        color=RED,
                        timestamp=declined_time
                    )

                    declined_embed.set_footer(
                        text="LiteBet • Withdrawal Logs"
                    )

                    await interaction.response.edit_message(
                        embed=declined_embed,
                        view=self
                    )

                    # ----------------------------------------
                    # UPDATE ORIGINAL MESSAGE
                    # ----------------------------------------

                    try:

                        user_embed = discord.Embed(
                            title="❌ Withdrawal Declined",
                            description=(
                                f"Your withdrawal request "
                                f"was declined.\n\n"

                                f"**Points:** "
                                f"`{money(amount)}`\n"

                                f"**LTC:** "
                                f"`{ltc_amount:.8f} LTC`\n\n"

                                f"**Address:**\n"
                                f"```{address}```\n\n"

                                f"**Status:** `DECLINED`\n"
                                f"**Request ID:** `{request_id}`\n\n"

                                f"**No points were removed.**"
                            ),
                            color=RED,
                            timestamp=declined_time
                        )

                        await message.edit(
                            embed=user_embed
                        )

                    except Exception as e:

                        print(
                            f"Withdrawal decline message error: {e}"
                        )

                    # ----------------------------------------
                    # DM USER
                    # ----------------------------------------

                    try:

                        dm = discord.Embed(
                            title="❌ Withdrawal Declined",
                            description=(
                                f"Your withdrawal request "
                                f"was declined.\n\n"

                                f"**Points:** "
                                f"`{money(amount)}`\n"

                                f"**LTC:** "
                                f"`{ltc_amount:.8f} LTC`\n\n"

                                f"**Address:**\n"
                                f"```{address}```\n\n"

                                f"**Status:** `DECLINED`\n"
                                f"**Request ID:** `{request_id}`\n\n"

                                f"No points were removed."
                            ),
                            color=RED,
                            timestamp=declined_time
                        )

                        await ctx.author.send(
                            embed=dm
                        )

                    except Exception:
                        pass

            # ------------------------------------------------
            # CREATE PENDING LOG
            # ------------------------------------------------

            log_embed = discord.Embed(
                title="💸 Withdrawal — PENDING",
                description=(
                    f"**Amount:** "
                    f"`{money(amount)} points`\n\n"

                    f"**LTC Amount:** "
                    f"`{ltc_amount:.8f} LTC`\n\n"

                    f"**LTC Address:**\n"
                    f"```{address}```\n\n"

                    f"**User:** "
                    f"{ctx.author.mention} "
                    f"`{ctx.author.id}`\n\n"

                    f"**Status:** `PENDING`\n"
                    f"**Time:** "
                    f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>\n\n"

                    f"**Request ID:** `{request_id}`\n\n"

                    f"**Withdrawal Message:** "
                    f"{message.jump_url}"
                ),
                color=0xF0B90B,
                timestamp=datetime.now(timezone.utc)
            )

            log_embed.set_footer(
                text="LiteBet • Withdrawal Logs"
            )

            try:

                log_message = await log_channel.send(
                    embed=log_embed,
                    view=WithdrawalAdminView()
                )

                # --------------------------------------------
                # ADD LOG MESSAGE LINK
                # --------------------------------------------

                log_embed.description = (
                    f"**Amount:** "
                    f"`{money(amount)} points`\n\n"

                    f"**LTC Amount:** "
                    f"`{ltc_amount:.8f} LTC`\n\n"

                    f"**LTC Address:**\n"
                    f"```{address}```\n\n"

                    f"**User:** "
                    f"{ctx.author.mention} "
                    f"`{ctx.author.id}`\n\n"

                    f"**Status:** `PENDING`\n"
                    f"**Time:** "
                    f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>\n\n"

                    f"**Request ID:** `{request_id}`\n\n"

                    f"**Withdrawal Message:** "
                    f"{message.jump_url}\n"

                    f"**Log Message:** "
                    f"{log_message.jump_url}"
                )

                await log_message.edit(
                    embed=log_embed
                )

            except Exception as e:

                print(
                    f"Withdrawal log error: {e}"
                )

    # ========================================================
    # NO LOG CHANNEL
    # ========================================================

    else:

        try:

            await ctx.send(
                embed=emb(
                    "Withdrawal Submitted",
                    (
                        "Your withdrawal request was submitted, "
                        "but the withdrawal log channel has not "
                        "been configured.\n\n"
                        "An administrator needs to run:\n"
                        "`.wlog2 #channel`"
                    ),
                    RED
                )
            )

        except Exception:
            pass
@bot.command()
async def worldtime(ctx):
    now=datetime.now(timezone.utc); await ctx.send(embed=emb('🌍 World Time',f'UTC: <t:{int(now.timestamp())}:F>\nIndia: <t:{int(now.timestamp())}:F>\nUse Discord’s local rendering to see the exact time in your timezone.'))
@bot.command()
async def timer(ctx, duration: int, *, reminder='Your LiteBet timer has ended!'):
    if duration<1 or duration>86400: return await ctx.send(embed=emb('Invalid timer','Choose seconds from 1 to 86,400.',RED))
    await ctx.send(embed=emb('Timer set',f'I will DM you in **{duration} seconds**.'))
    await asyncio.sleep(duration)
    try: await ctx.author.send(embed=emb('⏰ Timer',reminder))
    except discord.Forbidden: pass

# ============================================================
# REPORT — BEGGING DETECTION
# ============================================================

BEGGING_WORDS = [
    'beg',
    'begging',
    'please give',
    'pls give',
    'plz give',
    'free points',
    'free point',
    'give me points',
    'give me point',
    'give points',
    'give point',
    'can i have points',
    'can i have point',
    'can i get points',
    'can i get point',
    'send me points',
    'send points',
    'need points',
    'need some points',
    'give me money',
    'send me money'
]


@bot.command()
async def report(ctx):

    # ========================================================
    # MUST REPLY TO A MESSAGE
    # ========================================================

    if not ctx.message.reference:

        return await ctx.send(
            embed=emb(
                'Report',
                (
                    '❌ You must **reply to a message** '
                    'with `.report`.\n\n'
                    'Example: reply to the message and type:\n'
                    '`.report`'
                ),
                RED
            ),
            delete_after=8
        )

    # ========================================================
    # GET REPLIED MESSAGE
    # ========================================================

    try:

        reported_message = await ctx.channel.fetch_message(
            ctx.message.reference.message_id
        )

    except Exception:

        return await ctx.send(
            embed=emb(
                'Report',
                '❌ I could not find the message you reported.',
                RED
            ),
            delete_after=8
        )

    # ========================================================
    # MESSAGE CONTENT
    # ========================================================

    content = (
        reported_message.content or ''
    ).lower().strip()

    # ========================================================
    # CHECK BEGGING
    # ========================================================

    begging = any(
        phrase in content
        for phrase in BEGGING_WORDS
    )

    # ========================================================
    # NOT BEGGING
    # ========================================================

    if not begging:

        return await ctx.send(
            embed=emb(
                'Report Checked',
                (
                    'This does not violate any policy.\n\n'
                    'No action was taken.'
                ),
                NAVY
            ),
            delete_after=8
        )

    # ========================================================
    # DELETE OFFENDING MESSAGE
    # ========================================================

    try:

        await reported_message.delete()

    except discord.Forbidden:

        pass

    except discord.NotFound:

        pass

    except Exception as e:

        print(
            f'Report message delete error: {e}'
        )

    # ========================================================
    # MUTE USER — 15 MINUTES
    # ========================================================

    mute_duration = timedelta(
        minutes=15
    )

    muted = False

    try:

        await reported_message.author.timeout(
            mute_duration,
            reason='Begging for points'
        )

        muted = True

    except discord.Forbidden:

        print(
            'Report: Missing permission to timeout user.'
        )

    except Exception as e:

        print(
            f'Report timeout error: {e}'
        )

    # ========================================================
    # DELETE THE .REPORT COMMAND
    # ========================================================

    try:

        await ctx.message.delete()

    except Exception:

        pass

    # ========================================================
    # DM USER
    # ========================================================

    try:

        dm_embed = emb(
            '🔇 You Have Been Muted',
            (
                'You have been muted for **15 minutes**.\n\n'
                '**Reason:** Begging for points.\n\n'
                'Please do not ask other users for free points, '
                'currency, or balances.\n\n'
                'You will automatically be unmuted after '
                '**15 minutes**.'
            ),
            RED
        )

        await reported_message.author.send(
            embed=dm_embed
        )

    except discord.Forbidden:

        pass

    except Exception as e:

        print(
            f'Report DM error: {e}'
        )

    # ========================================================
    # CHANNEL NOTICE
    # ========================================================

    if muted:

        notice = await ctx.channel.send(
            embed=emb(
                '🔇 User Muted',
                (
                    f'{reported_message.author.mention} '
                    f'has been muted for **15 minutes**.\n\n'
                    f'**Reason:** Begging for points.'
                ),
                RED
            )
        )

    else:

        notice = await ctx.channel.send(
            embed=emb(
                '⚠️ Moderation Failed',
                (
                    f'{reported_message.author.mention} '
                    f'was detected begging for points, '
                    f'but I could not apply the mute.\n\n'
                    f'Please check that the bot has the '
                    f'**Moderate Members** permission.'
                ),
                RED
            )
        )

    # Automatically remove the notice after 8 seconds
    await asyncio.sleep(8)

    try:
        await notice.delete()
    except Exception:
        pass


class TttView(discord.ui.View):
    def __init__(self, first_id, second_id, amount):
        super().__init__(timeout=180); self.players=[first_id,second_id]; self.amount=amount; self.turn=0; self.board=['']*9; self.message=None
        for i in range(9):
            b=discord.ui.Button(label=' ',style=discord.ButtonStyle.secondary,row=i//3,custom_id=f'ttt:{i}')
            b.callback=self.move; self.add_item(b)
    def board_embed(self, note=''):
        symbol='X' if self.turn==0 else 'O'; return emb('Tic-Tac-Toe',f'<@{self.players[0]}> is **X**\n<@{self.players[1]}> is **O**\n\n{note}\n\nIt is <@{self.players[self.turn]}>\'s turn ({symbol}).')
    async def interaction_check(self, interaction):
        if interaction.user.id != self.players[self.turn]:
            await interaction.response.send_message('It is not your turn.',ephemeral=True); return False
        return True
    async def move(self, interaction):
        index=int(interaction.data['custom_id'].split(':')[1])
        if self.board[index]: return await interaction.response.send_message('That square is already taken.',ephemeral=True)
        mark='X' if self.turn==0 else 'O'; self.board[index]=mark
        button=next(x for x in self.children if x.custom_id==interaction.data['custom_id']); button.label=mark; button.disabled=True; button.style=discord.ButtonStyle.primary if mark=='X' else discord.ButtonStyle.success
        wins=((0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6))
        if any(all(self.board[i]==mark for i in line) for line in wins):
            winner=self.players[self.turn]; payout=(self.amount*2).quantize(Decimal('.01')); await db.balance(winner,payout)
            await db.record(winner,'Tic-Tac-Toe',self.amount,'win',payout); await db.record(self.players[1-self.turn],'Tic-Tac-Toe',self.amount,'loss',Decimal('0'))
            return await interaction.response.edit_message(embed=emb('Tic-Tac-Toe',f'<@{winner}> won by strategy and receives **{money(payout)} points**!',GREEN),view=self)
        if all(self.board):
            await db.balance(self.players[0],self.amount); await db.balance(self.players[1],self.amount)
            return await interaction.response.edit_message(embed=emb('Tic-Tac-Toe',f'It is a draw. Both players were refunded **{money(self.amount)} points**.'),view=self)
        self.turn=1-self.turn; await interaction.response.edit_message(embed=self.board_embed(),view=self)
    async def on_timeout(self):
        if self.message:
            loser=self.players[self.turn]; winner=self.players[1-self.turn]; payout=(self.amount*2).quantize(Decimal('.01')); await db.balance(winner,payout)
            await self.message.edit(embed=emb('Tic-Tac-Toe',f'<@{loser}> ran out of time. <@{winner}> wins **{money(payout)} points**.',GREEN),view=None)

class RpsView(discord.ui.View):
    def __init__(self, first_id, second_id, amount):
        super().__init__(timeout=90); self.players=[first_id,second_id]; self.amount=amount; self.choices={}; self.message=None
    async def choose(self, interaction, choice):
        if interaction.user.id not in self.players: return await interaction.response.send_message('This is not your game.',ephemeral=True)
        if interaction.user.id in self.choices: return await interaction.response.send_message('Your choice is already locked.',ephemeral=True)
        self.choices[interaction.user.id]=choice
        if len(self.choices)<2: return await interaction.response.send_message('Your choice is locked. Waiting for the other player.',ephemeral=True)
        a,b=self.players; ca,cb=self.choices[a],self.choices[b]
        beats={'rock':'scissors','scissors':'paper','paper':'rock'}
        if ca==cb:
            await db.balance(a,self.amount); await db.balance(b,self.amount)
            return await interaction.response.edit_message(embed=emb('Rock Paper Scissors',f'Both chose **{ca.title()}**. Draw - both players were refunded.'),view=None)
        winner=a if beats[ca]==cb else b; payout=(self.amount*2).quantize(Decimal('.01')); await db.balance(winner,payout)
        await db.record(winner,'RPS',self.amount,'win',payout); await db.record(b if winner==a else a,'RPS',self.amount,'loss',Decimal('0'))
        await interaction.response.edit_message(embed=emb('Rock Paper Scissors',f'<@{a}> chose **{ca.title()}**\n<@{b}> chose **{cb.title()}**\n\n<@{winner}> wins **{money(payout)} points**!',GREEN),view=None)
    @discord.ui.button(label='Rock',style=discord.ButtonStyle.secondary)
    async def rock(self,interaction,button): await self.choose(interaction,'rock')
    @discord.ui.button(label='Paper',style=discord.ButtonStyle.primary)
    async def paper(self,interaction,button): await self.choose(interaction,'paper')
    @discord.ui.button(label='Scissors',style=discord.ButtonStyle.success)
    async def scissors(self,interaction,button): await self.choose(interaction,'scissors')
    async def on_timeout(self):
        if self.message:
            for uid in self.players: await db.balance(uid,self.amount)
            await self.message.edit(embed=emb('Rock Paper Scissors','The challenge expired. Both players were refunded.'),view=None)

class DuelInviteView(discord.ui.View):
    def __init__(self, host_id, guest_id, amount, game):
        super().__init__(timeout=60); self.host_id=host_id; self.guest_id=guest_id; self.amount=amount; self.game=game; self.message=None
    async def interaction_check(self, interaction):
        if interaction.user.id != self.guest_id:
            await interaction.response.send_message('Only the challenged player can accept or decline this game.',ephemeral=True); return False
        return True
    @discord.ui.button(label='Accept',style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        host=await db.user(self.host_id); guest=await db.user(self.guest_id)
        if Decimal(host['balance'])<self.amount or Decimal(guest['balance'])<self.amount:
            return await interaction.response.edit_message(embed=emb(f'{self.game} challenge cancelled','One player no longer has enough points for this bet.',RED),view=None)
        await db.debit(self.host_id,self.amount); await db.debit(self.guest_id,self.amount)
        if self.game=='Tic-Tac-Toe':
            view=TttView(self.host_id,self.guest_id,self.amount); await interaction.response.edit_message(embed=view.board_embed(),view=view)
        else:
            view=RpsView(self.host_id,self.guest_id,self.amount); await interaction.response.edit_message(embed=emb('Rock Paper Scissors',f'<@{self.host_id}> vs <@{self.guest_id}>\n\nBoth players choose Rock, Paper, or Scissors. Choices remain hidden until both are locked.'),view=view)
        view.message=interaction.message
    @discord.ui.button(label='Decline',style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        await interaction.response.edit_message(embed=emb(f'{self.game} challenge declined',f'<@{self.guest_id}> declined the game. No points were deducted.',RED),view=None)
    async def on_timeout(self):
        if self.message: await self.message.edit(embed=emb(f'{self.game} challenge expired','No points were deducted.',RED),view=None)

# Replace the original demonstration commands with real interactive sessions.
for command_name in ('coinflip','hilo','mines','rps','ttt'):
    bot.remove_command(command_name)

# ============================================================
# COINFLIP
# 40% PLAYER WIN CHANCE / 60% HOUSE WIN CHANCE
# ============================================================

@bot.command(aliases=['cf', 'coinfip'])
async def coinflip(ctx, amount: str, choice: str = None):

    amount = await resolve_amount(ctx, amount)

    if not await require_game(ctx, amount):
        return

    # --------------------------------------------------------
    # PLAYER CHOICE
    # --------------------------------------------------------

    choice = (
        choice or random.choice(['heads', 'tails'])
    ).lower()

    if choice in ('h', 'head', 'heads'):
        choice = 'heads'
    else:
        choice = 'tails'

    # --------------------------------------------------------
    # PROVABLY FAIR SEEDS
    # --------------------------------------------------------

    s, c, h = seed()

    # --------------------------------------------------------
    # ROLLING MESSAGE
    # --------------------------------------------------------

    message = await ctx.send(
        embed=emb(
            '🪙 Coinflip - Rolling...',
            (
                f'**Bet:** {money(amount)} points\n'
                f'**Choice:** {choice.title()}\n\n'
                f'The coin is spinning...'
            )
        )
    )

    await asyncio.sleep(3)

    # --------------------------------------------------------
    # 40% PLAYER WIN / 60% HOUSE WIN
    #
    # The result is intentionally weighted and disclosed
    # through .chances.
    # --------------------------------------------------------

    player_wins = random.random() < 0.40

    if player_wins:
        landed = choice
    else:
        landed = (
            'tails'
            if choice == 'heads'
            else 'heads'
        )

    won = choice == landed

    # --------------------------------------------------------
    # PAYOUT
    # --------------------------------------------------------

    payout = (
        amount * Decimal('1.92')
    ).quantize(
        Decimal('0.01')
    ) if won else Decimal('0')

    # --------------------------------------------------------
    # RECORD GAME
    # --------------------------------------------------------

    try:

        await db.record(
            ctx.author.id,
            'Coinflip',
            amount,
            'win' if won else 'loss',
            payout
        )

    except Exception as e:

        print(
            f'Coinflip DB error: {e}'
        )

    # --------------------------------------------------------
    # RESULT EMBED
    # --------------------------------------------------------

    e = result_embed(
        'Coinflip',
        amount,
        won,
        Decimal('1.92'),
        (
            f'**Choice:** {choice.title()}\n'
            f'**Landed:** {landed.title()}\n\n'
            f'Public Hash: `{h}`\n'
            f'Server Seed: `{s}`\n'
            f'Client Seed: `{c}`'
        )
    )

    # --------------------------------------------------------
    # COIN IMAGE
    # --------------------------------------------------------

    image = coinflip_card(landed)

    e.set_image(
        url=f'attachment://{image.name}'
    )

    await message.edit(
        embed=e,
        attachments=[
            discord.File(image)
        ]
    )


# ============================================================
# COINFLIP CHANCES
# ============================================================

@bot.command(aliases=['cfchance', 'cfodds'])
async def chances(ctx):

    await ctx.send(
        embed=emb(
            '🪙 Coinflip Chances',
            (
                '**Coinflip**\n\n'
                '🟢 **Player Win:** `50%`\n'
                '🔴 **House Win:** `50%`\n\n'
                '**Winning Payout:** `1.92x`\n\n'
                'The displayed odds are the actual game '
                'odds used by Coinflip.'
            )
        )
    )
@bot.command()
async def hilo(ctx, amount: Decimal):
    if not await require_game(ctx,amount): return
    message=await ctx.send(embed=emb('Hi-Lo - Shuffling...',f'**Bet:** {money(amount)} points\nDrawing your first card...'))
    await asyncio.sleep(3); s,c,h=seed(); view=HiloView(ctx.author.id,amount,random.choice(RANKS),random.choice(['S','H','D','C']),s,c,h)
    e=view.current_embed(); image=hilo_card(view.rank,view.suit); e.set_image(url=f'attachment://{image.name}')
    await message.edit(embed=e,attachments=[discord.File(image)],view=view); view.message=message

# ============================================================
# MINES — 20 TILES + CASH OUT
# ============================================================

MINES_DIAMOND = '<:diamond:1538364736251629640>'
MINES_BOMB = '<:bomb:1538364767201271808>'


class MinesView(discord.ui.View):

    def __init__(
        self,
        author_id,
        amount,
        bombs,
        server,
        client,
        public_hash
    ):
        super().__init__(timeout=120)

        self.author_id = author_id
        self.amount = amount
        self.bombs = bombs

        # ----------------------------------------------------
        # 20 TOTAL TILES
        # ----------------------------------------------------

        self.total_tiles = 20
        self.safe_tiles = self.total_tiles - bombs

        self.mines = set(
            random.sample(
                range(self.total_tiles),
                bombs
            )
        )

        self.revealed_tiles = set()
        self.revealed = 0

        self.server = server
        self.client = client
        self.public_hash = public_hash

        self.message = None
        self.finished = False

        # ----------------------------------------------------
        # 20 TILE BOARD
        # 4 ROWS × 5 TILES
        # ----------------------------------------------------

        for i in range(self.total_tiles):

            button = discord.ui.Button(
                label='?',
                style=discord.ButtonStyle.secondary,
                row=i // 5,
                custom_id=f'mine:{i}'
            )

            button.callback = self.pick

            self.add_item(button)

        # ----------------------------------------------------
        # CASH OUT BUTTON
        # ----------------------------------------------------

        cashout_button = discord.ui.Button(
            label='Cash Out',
            emoji='💰',
            style=discord.ButtonStyle.success,
            row=4,
            custom_id='mine:cashout'
        )

        cashout_button.callback = self.cashout

        self.add_item(cashout_button)

    # ========================================================
    # MULTIPLIER
    # ========================================================

    def multiplier(self):

        if self.revealed <= 0:
            return Decimal('1.00')

        multiplier = Decimal('1.00')

        # Slightly stronger house edge than the fair
        # probability multiplier.
        house_edge = Decimal('0.97')

        for i in range(self.revealed):

            remaining_tiles = self.total_tiles - i
            remaining_safe = self.safe_tiles - i

            if remaining_safe <= 0:
                break

            step = (
                Decimal(remaining_tiles)
                / Decimal(remaining_safe)
            )

            step *= house_edge

            multiplier *= step

        return multiplier.quantize(
            Decimal('0.01')
        )

    # ========================================================
    # BOARD TEXT
    # ========================================================

    def grid_text(self, reveal_all=False):

        rows = []

        for row in range(4):

            cells = []

            for col in range(5):

                index = row * 5 + col

                if reveal_all:

                    if index in self.mines:
                        cells.append(MINES_BOMB)

                    else:
                        cells.append(MINES_DIAMOND)

                elif index in self.revealed_tiles:

                    cells.append(MINES_DIAMOND)

                else:

                    cells.append('⬛')

            rows.append(
                ' '.join(cells)
            )

        return '\n'.join(rows)

    # ========================================================
    # ACTIVE GAME EMBED
    # ========================================================

    def game_embed(self, extra=None):

        if extra is None:

            extra = (
                'Choose a tile to reveal a diamond.'
            )

        multiplier = self.multiplier()

        profit = (
            self.amount
            * (multiplier - Decimal('1'))
        ).quantize(
            Decimal('0.01')
        )

        return emb(
            '💣 Mines',
            (
                f'**Bet Amount:** '
                f'{money(self.amount)} points\n'

                f'**Current Multiplier:** '
                f'`{multiplier:.2f}x`\n'

                f'**Profit:** '
                f'{money(profit)} points\n\n'

                f'{self.bombs} {MINES_BOMB} | '
                f'{self.safe_tiles} {MINES_DIAMOND}\n\n'

                f'{self.grid_text()}\n\n'

                f'{extra}\n\n'

                f'💰 **Use Cash Out to collect your '
                f'winnings.**\n\n'

                f'🔒 **Provably Fair**\n'
                f'Public Hash: `{self.public_hash}`'
            )
        )

    # ========================================================
    # PLAYER CHECK
    # ========================================================

    async def interaction_check(self, interaction):

        if self.finished:

            await interaction.response.send_message(
                '❌ This Mines game has already ended.',
                ephemeral=True
            )

            return False

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                '❌ Only the player who started this Mines '
                'game can play it.',
                ephemeral=True
            )

            return False

        return True

    # ========================================================
    # TILE CLICK
    # ========================================================

    async def pick(self, interaction):

        if self.finished:

            return await interaction.response.send_message(
                '❌ This Mines game has already ended.',
                ephemeral=True
            )

        try:

            custom_id = interaction.data.get(
                'custom_id',
                ''
            )

            if not custom_id.startswith('mine:'):
                return

            index = int(
                custom_id.split(':')[1]
            )

            if index < 0 or index >= self.total_tiles:

                return await interaction.response.send_message(
                    '❌ Invalid tile.',
                    ephemeral=True
                )

            button = next(
                (
                    x for x in self.children
                    if x.custom_id == custom_id
                ),
                None
            )

            if button is None:

                return await interaction.response.send_message(
                    '❌ This tile is no longer available.',
                    ephemeral=True
                )

            # ------------------------------------------------
            # ALREADY REVEALED
            # ------------------------------------------------

            if index in self.revealed_tiles:

                return await interaction.response.send_message(
                    '💎 You already revealed this tile.',
                    ephemeral=True
                )

            # =================================================
            # MINE
            # =================================================

            if index in self.mines:

                self.finished = True
                self.stop()

                ACTIVE_MINES.pop(
                    interaction.message.id,
                    None
                )

                # ------------------------------------------------
                # REVEAL BOARD
                # ------------------------------------------------

                for x in self.children:

                    x.disabled = True

                    if x.custom_id == 'mine:cashout':
                        continue

                    tile_index = int(
                        x.custom_id.split(':')[1]
                    )

                    x.label = ''

                    if tile_index in self.mines:

                        x.emoji = '💣'
                        x.style = discord.ButtonStyle.danger

                    elif tile_index in self.revealed_tiles:

                        x.emoji = '💎'
                        x.style = discord.ButtonStyle.success

                    else:

                        x.emoji = '💎'
                        x.style = discord.ButtonStyle.secondary

                # ------------------------------------------------
                # LOSS
                # ------------------------------------------------

                loss_embed = emb(
                    '❌ Game Over!',
                    (
                        f'**Bet Amount:** '
                        f'{money(self.amount)} points\n'

                        f'**Current Multiplier:** '
                        f'`{self.multiplier():.2f}x`\n'

                        f'**Profit:** 0 points\n\n'

                        f'{self.bombs} {MINES_BOMB} | '
                        f'{self.safe_tiles} {MINES_DIAMOND}\n\n'

                        f'💥 **You hit a mine!**\n\n'

                        f'🔒 **Provably Fair:**\n'
                        f'• **Public Hash:** '
                        f'`{self.public_hash}`\n'

                        f'• **Server Seed:** '
                        f'`{self.server}`\n'

                        f'• **Client Seed:** '
                        f'`{self.client}`'
                    ),
                    RED
                )

                await interaction.response.edit_message(
                    embed=loss_embed,
                    view=self
                )

                try:

                    await db.record(
                        self.author_id,
                        'Mines',
                        self.amount,
                        'loss',
                        Decimal('0')
                    )

                except Exception as e:

                    print(
                        f'Mines loss DB error: {e}'
                    )

                return

            # =================================================
            # DIAMOND
            # =================================================

            self.revealed += 1

            self.revealed_tiles.add(index)

            button.disabled = True
            button.label = ''
            button.emoji = '💎'
            button.style = discord.ButtonStyle.success

            # =================================================
            # ALL SAFE TILES
            # =================================================

            if self.revealed >= self.safe_tiles:

                self.finished = True
                self.stop()

                multiplier = self.multiplier()

                payout = (
                    self.amount
                    * multiplier
                ).quantize(
                    Decimal('0.01')
                )

                ACTIVE_MINES.pop(
                    interaction.message.id,
                    None
                )

                # ------------------------------------------------
                # REVEAL BOARD
                # ------------------------------------------------

                for x in self.children:

                    x.disabled = True

                    if x.custom_id == 'mine:cashout':
                        continue

                    tile_index = int(
                        x.custom_id.split(':')[1]
                    )

                    x.label = ''

                    if tile_index in self.mines:

                        x.emoji = '💣'
                        x.style = discord.ButtonStyle.danger

                    else:

                        x.emoji = '💎'
                        x.style = discord.ButtonStyle.success

                # ------------------------------------------------
                # WIN
                # ------------------------------------------------

                win_embed = emb(
                    '🎉 Game Won!',
                    (
                        f'**Bet Amount:** '
                        f'{money(self.amount)} points\n'

                        f'**Final Multiplier:** '
                        f'`{multiplier:.2f}x`\n'

                        f'**Payout:** '
                        f'{money(payout)} points\n\n'

                        f'{self.bombs} {MINES_BOMB} | '
                        f'{self.safe_tiles} {MINES_DIAMOND}\n\n'

                        f'💎 **All diamonds found!**\n\n'

                        f'🔒 **Provably Fair:**\n'
                        f'• **Public Hash:** '
                        f'`{self.public_hash}`\n'

                        f'• **Server Seed:** '
                        f'`{self.server}`\n'

                        f'• **Client Seed:** '
                        f'`{self.client}`'
                    ),
                    GREEN
                )

                await interaction.response.edit_message(
                    embed=win_embed,
                    view=self
                )

                try:

                    await db.record(
                        self.author_id,
                        'Mines',
                        self.amount,
                        'win',
                        payout
                    )

                except Exception as e:

                    print(
                        f'Mines win DB error: {e}'
                    )

                return

            # =================================================
            # CONTINUE
            # =================================================

            multiplier = self.multiplier()

            await interaction.response.edit_message(
                embed=self.game_embed(
                    (
                        f'{MINES_DIAMOND} **Diamond found!**\n'
                        f'Current cashout: '
                        f'`{multiplier:.2f}x`'
                    )
                ),
                view=self
            )

        except Exception as e:

            print(
                f'Mines interaction error: {e}'
            )

            if not interaction.response.is_done():

                try:

                    await interaction.response.send_message(
                        '❌ An error occurred while '
                        'processing that tile.',
                        ephemeral=True
                    )

                except Exception:
                    pass

    # ========================================================
    # CASH OUT
    # ========================================================

    async def cashout(self, interaction):

        if self.finished:

            return await interaction.response.send_message(
                '❌ This Mines game has already ended.',
                ephemeral=True
            )

        if self.revealed <= 0:

            return await interaction.response.send_message(
                '💎 Reveal at least one diamond before '
                'cashing out.',
                ephemeral=True
            )

        self.finished = True
        self.stop()

        multiplier = self.multiplier()

        payout = (
            self.amount
            * multiplier
        ).quantize(
            Decimal('0.01')
        )

        profit = (
            payout - self.amount
        ).quantize(
            Decimal('0.01')
        )

        ACTIVE_MINES.pop(
            interaction.message.id,
            None
        )

        # ----------------------------------------------------
        # DISABLE EVERYTHING
        # ----------------------------------------------------

        for x in self.children:
            x.disabled = True

        # ----------------------------------------------------
        # CASHOUT EMBED
        # ----------------------------------------------------

        cashout_embed = emb(
            '🎉 Cashed Out!',
            (
                f'**Bet Amount:** '
                f'{money(self.amount)} points\n'

                f'**Multiplier:** '
                f'`{multiplier:.2f}x`\n'

                f'**Profit:** '
                f'{money(profit)} points\n'

                f'**Payout:** '
                f'{money(payout)} points\n\n'

                f'{self.bombs} {MINES_BOMB} | '
                f'{self.safe_tiles} {MINES_DIAMOND}\n\n'

                f'💎 **Diamonds Found:** '
                f'**{self.revealed}**\n\n'

                f'💰 **Cashed out successfully!**\n\n'

                f'🔒 **Provably Fair:**\n'
                f'• **Public Hash:** '
                f'`{self.public_hash}`\n'
                f'• **Server Seed:** '
                f'`{self.server}`\n'
                f'• **Client Seed:** '
                f'`{self.client}`'
            ),
            GREEN
        )

        await interaction.response.edit_message(
            embed=cashout_embed,
            view=self
        )

        try:

            await db.record(
                self.author_id,
                'Mines',
                self.amount,
                'win',
                payout
            )

        except Exception as e:

            print(
                f'Mines cashout DB error: {e}'
            )

    # ========================================================
    # TIMEOUT
    # ========================================================

    async def on_timeout(self):

        if self.finished:
            return

        self.finished = True
        self.stop()

        if self.message:

            ACTIVE_MINES.pop(
                self.message.id,
                None
            )

        for x in self.children:
            x.disabled = True

        if not self.message:
            return

        # ----------------------------------------------------
        # AUTO CASHOUT
        # ----------------------------------------------------

        if self.revealed > 0:

            multiplier = self.multiplier()

            payout = (
                self.amount
                * multiplier
            ).quantize(
                Decimal('0.01')
            )

            timeout_embed = emb(
                '⏰ Game Over!',
                (
                    f'**Bet Amount:** '
                    f'{money(self.amount)} points\n'

                    f'**Multiplier:** '
                    f'`{multiplier:.2f}x`\n'

                    f'**Payout:** '
                    f'{money(payout)} points\n\n'

                    f'💎 Automatically cashed out after '
                    f'**{self.revealed} diamonds**.\n\n'

                    f'🔒 **Provably Fair:**\n'
                    f'• **Public Hash:** '
                    f'`{self.public_hash}`\n'
                    f'• **Server Seed:** '
                    f'`{self.server}`\n'
                    f'• **Client Seed:** '
                    f'`{self.client}`'
                ),
                GREEN
            )

            await self.message.edit(
                embed=timeout_embed,
                view=self
            )

            try:

                await db.record(
                    self.author_id,
                    'Mines',
                    self.amount,
                    'win',
                    payout
                )

            except Exception as e:

                print(
                    f'Mines timeout DB error: {e}'
                )

        else:

            timeout_embed = emb(
                '⏰ Game Over!',
                (
                    f'**Bet Amount:** '
                    f'{money(self.amount)} points\n'

                    f'**Multiplier:** `1.00x`\n'
                    f'**Profit:** 0 points\n\n'

                    f'❌ No diamond was revealed before '
                    f'the game timed out.'
                ),
                RED
            )

            await self.message.edit(
                embed=timeout_embed,
                view=self
            )

            try:

                await db.record(
                    self.author_id,
                    'Mines',
                    self.amount,
                    'loss',
                    Decimal('0')
                )

            except Exception as e:

                print(
                    f'Mines timeout DB error: {e}'
                )


# ============================================================
# MINES COMMAND
# ============================================================

@bot.command()
async def mines(
    ctx,
    amount: Decimal,
    bombs: int = 4
):

    # 20 tiles => maximum 19 bombs
    if bombs < 1 or bombs > 19:

        return await ctx.send(
            embed=emb(
                'Invalid mine count',
                'Choose from **1 to 19 bombs**.',
                RED
            )
        )

    if not await require_game(
        ctx,
        amount
    ):
        return

    # --------------------------------------------------------
    # PROVABLY FAIR SEEDS
    # --------------------------------------------------------

    server, client, public_hash = seed()

    # --------------------------------------------------------
    # CREATE VIEW
    # --------------------------------------------------------

    view = MinesView(
        ctx.author.id,
        amount,
        bombs,
        server,
        client,
        public_hash
    )

    # --------------------------------------------------------
    # SEND GAME
    # --------------------------------------------------------

    message = await ctx.send(
        embed=view.game_embed(),
        view=view
    )

    view.message = message

    # --------------------------------------------------------
    # SAVE ACTIVE GAME
    # --------------------------------------------------------

    ACTIVE_MINES[message.id] = view
@bot.command()
async def rps(ctx, member: discord.Member, amount: Decimal):
    if member.bot or member==ctx.author or amount<=0: return await ctx.send(embed=emb('Invalid challenge','Choose another member and a positive bet.',RED))
    host=await db.user(ctx.author.id); guest=await db.user(member.id); view=DuelInviteView(ctx.author.id,member.id,amount,'Rock Paper Scissors')
    message=await ctx.send(embed=emb('Rock Paper Scissors Challenge',f'{ctx.author.mention} challenged {member.mention} for **{money(amount)} points**.\n\n**Current balances:**\n{ctx.author.mention}: **{money(host["balance"])}** points\n{member.mention}: **{money(guest["balance"])}** points\n\n{member.mention}, accept or decline within 60 seconds.'),view=view); view.message=message

@bot.command()
async def ttt(ctx, member: discord.Member, amount: Decimal):
    if member.bot or member==ctx.author or amount<=0: return await ctx.send(embed=emb('Invalid challenge','Choose another member and a positive bet.',RED))
    host=await db.user(ctx.author.id); guest=await db.user(member.id); view=DuelInviteView(ctx.author.id,member.id,amount,'Tic-Tac-Toe')
    message=await ctx.send(embed=emb('Tic-Tac-Toe Challenge',f'{ctx.author.mention} challenged {member.mention} for **{money(amount)} points**.\n\n**Current balances:**\n{ctx.author.mention}: **{money(host["balance"])}** points\n{member.mention}: **{money(guest["balance"])}** points\n\n{member.mention}, accept or decline within 60 seconds.'),view=view); view.message=message

@bot.command()
async def word(ctx, amount: Decimal, member: discord.Member):
    if member.bot or member == ctx.author:
        return await ctx.send(
            embed=emb(
                'Invalid opponent',
                'You need to challenge another player.',
                RED
            )
        )

    if amount <= 0:
        return await ctx.send(
            embed=emb(
                'Invalid bet',
                'Bet amount must be greater than zero.',
                RED
            )
        )

    host = await db.user(ctx.author.id)
    guest = await db.user(member.id)

    if Decimal(host['balance']) < amount:
        return await ctx.send(
            embed=emb(
                'Insufficient balance',
                f'You need **{money(amount)} points** to play.',
                RED
            )
        )

    if Decimal(guest['balance']) < amount:
        return await ctx.send(
            embed=emb(
                'Insufficient balance',
                f'{member.mention} does not have enough points.',
                RED
            )
        )

    # 3-letter starting fragment
    fragments = [
        'alp', 'bra', 'car', 'cha', 'con',
        'dra', 'ele', 'for', 'pla', 'sta',
        'str', 'tra', 'win', 'wor'
    ]

    fragment = random.choice(fragments)

    class WordInviteView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            self.message = None
            self.accepted = False
            self.finished = False

        async def interaction_check(self, interaction):
            if interaction.user.id != member.id:
                await interaction.response.send_message(
                    'Only the challenged player can accept or decline.',
                    ephemeral=True
                )
                return False

            return True

        @discord.ui.button(
            label='Accept',
            style=discord.ButtonStyle.success
        )
        async def accept(self, interaction, button):
            if self.accepted or self.finished:
                return await interaction.response.send_message(
                    'This challenge has already been handled.',
                    ephemeral=True
                )

            # Check balances again
            host_now = await db.user(ctx.author.id)
            guest_now = await db.user(member.id)

            if Decimal(host_now['balance']) < amount:
                self.finished = True
                self.stop()

                return await interaction.response.edit_message(
                    embed=emb(
                        'Challenge Cancelled',
                        'The challenger no longer has enough points.',
                        RED
                    ),
                    view=None
                )

            if Decimal(guest_now['balance']) < amount:
                self.finished = True
                self.stop()

                return await interaction.response.edit_message(
                    embed=emb(
                        'Challenge Cancelled',
                        f'{member.mention} no longer has enough points.',
                        RED
                    ),
                    view=None
                )

            # Take both bets
            if not await db.debit(ctx.author.id, amount):
                self.finished = True
                self.stop()

                return await interaction.response.edit_message(
                    embed=emb(
                        'Challenge Cancelled',
                        'The challenger could not be charged.',
                        RED
                    ),
                    view=None
                )

            if not await db.debit(member.id, amount):
                await db.balance(ctx.author.id, amount)

                self.finished = True
                self.stop()

                return await interaction.response.edit_message(
                    embed=emb(
                        'Challenge Cancelled',
                        'The challenged player could not be charged. '
                        'The challenger was refunded.',
                        RED
                    ),
                    view=None
                )

            self.accepted = True

            # Remove buttons immediately
            self.stop()

            await interaction.response.edit_message(
                embed=emb(
                    'Word Revealing',
                    f'{ctx.author.mention} vs {member.mention}\n\n'
                    'Get ready...'
                ),
                view=None
            )

            await asyncio.sleep(3)

            # Reveal the fragment
            await interaction.message.edit(
                embed=emb(
                    'Word Challenge',
                    f'# {fragment.title()}\n\n'
                    '**Guess the word!**\n\n'
                    f'Only {ctx.author.mention} and '
                    f'{member.mention} can participate.\n\n'
                    f'The word must start with '
                    f'**{fragment.title()}**.\n\n'
                    '**Unlimited guesses until someone wins.**'
                ),
                view=None
            )

            # Only these two players can guess
            players = {
                ctx.author.id,
                member.id
            }

            def check(message):
                return (
                    message.channel.id == ctx.channel.id
                    and message.author.id in players
                    and not message.author.bot
                )

            # Keep waiting forever until somebody gets a valid word
            while not self.finished:

                try:
                    guess_msg = await bot.wait_for(
                        'message',
                        check=check
                    )

                except asyncio.CancelledError:
                    return

                except Exception as e:
                    print(f'Word game error: {e}')
                    continue

                guess = guess_msg.content.strip().lower()

                # Ignore empty messages
                if not guess:
                    continue

                # Only alphabetic words
                if not guess.isalpha():
                    try:
                        await guess_msg.add_reaction('❌')
                    except Exception:
                        pass

                    continue

                # Must start with the required fragment
                if not guess.startswith(fragment):
                    try:
                        await guess_msg.add_reaction('❌')
                    except Exception:
                        pass

                    continue

                # Check local English dictionary
                valid = guess in ENGLISH_WORDS

                # Invalid English word
                if not valid:
                    try:
                        await guess_msg.add_reaction('❌')
                    except Exception:
                        pass

                    continue

                # Prevent double winner
                if self.finished:
                    return

                self.finished = True

                winner = guess_msg.author.id

                loser = (
                    member.id
                    if winner == ctx.author.id
                    else ctx.author.id
                )

                # Winner gets both bets = 2x
                payout = (
                    amount * Decimal('2')
                ).quantize(
                    Decimal('0.01')
                )

                await db.balance(
                    winner,
                    payout
                )

                await db.record(
                    winner,
                    'Word',
                    amount,
                    'win',
                    payout
                )

                await db.record(
                    loser,
                    'Word',
                    amount,
                    'loss',
                    Decimal('0')
                )

                # Correct guess
                try:
                    await guess_msg.add_reaction('✅')
                except Exception:
                    pass

                # Announce winner
                return await interaction.message.edit(
                    embed=emb(
                        'Word Challenge — Winner!',
                        f'🏆 <@{winner}> won!\n\n'
                        f'**Correct Word:** `{guess}`\n'
                        f'**Required Start:** '
                        f'`{fragment.title()}`\n\n'
                        f'Prize: **{money(payout)} points**',
                        GREEN
                    ),
                    view=None
                )

        @discord.ui.button(
            label='Decline',
            style=discord.ButtonStyle.danger
        )
        async def decline(self, interaction, button):
            if self.accepted or self.finished:
                return await interaction.response.send_message(
                    'This challenge has already started.',
                    ephemeral=True
                )

            self.finished = True
            self.stop()

            await interaction.response.edit_message(
                embed=emb(
                    'Word Challenge Declined',
                    f'{member.mention} declined the challenge.\n\n'
                    'No points were deducted.',
                    RED
                ),
                view=None
            )

        async def on_timeout(self):
            # Once accepted, this timeout must never cancel the game
            if self.accepted or self.finished:
                return

            self.finished = True

            if self.message:
                try:
                    await self.message.edit(
                        embed=emb(
                            'Word Challenge Expired',
                            'The challenge expired.\n\n'
                            'No points were deducted.',
                            RED
                        ),
                        view=None
                    )
                except Exception:
                    pass

    # Create challenge
    view = WordInviteView()

    message = await ctx.send(
        embed=emb(
            'Word Challenge',
            f'{ctx.author.mention} challenged '
            f'{member.mention} for '
            f'**{money(amount)} points**!\n\n'
            f'{member.mention}, do you accept?\n\n'
            'You have **60 seconds** to accept or decline.'
        ),
        view=view
    )

    view.message = message

@bot.event
async def on_raw_reaction_add(payload):
    view=ACTIVE_MINES.get(payload.message_id)
    if view and payload.user_id==view.author_id and str(payload.emoji)=='\U0001F4B0': await view.cash_out()

@bot.event
async def on_command_error(ctx,error):
    if isinstance(error,commands.CheckFailure): return await ctx.send(embed=emb('Permission denied','You do not have permission to use that command.',RED))
    if isinstance(error,(commands.MissingRequiredArgument,commands.BadArgument,InvalidOperation)): return await ctx.send(embed=emb('Command usage error','Check `.help` for the command format.',RED))
    if isinstance(error,commands.CommandNotFound): return
    raise error

async def main():
    if not TOKEN or not DB_URL: raise RuntimeError('Set DISCORD_TOKEN and DATABASE_URL in Railway Variables.')
    await db.connect()
    async with bot: await bot.start(TOKEN)
if __name__=='__main__': asyncio.run(main())
