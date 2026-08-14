import os, random, hashlib, secrets, asyncio
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from pathlib import Path
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from db import Database
from imaging import balance_card, limbo_card, blackjack_card, coinflip_card, hilo_card

# Litecoin derivation
try:
    from bip_utils import Bip44, Bip44Coins, Bip44Changes
except Exception:
    Bip44 = None
    Bip44Coins = None
    Bip44Changes = None

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
async def finish(ctx, game, amount, won, multiplier, detail, image=None):
    payout=(amount*multiplier).quantize(Decimal('0.01')) if won else Decimal('0')
    await db.record(ctx.author.id,game,amount,'win' if won else 'loss',payout)
    e=emb(f'{game} — You {"Won" if won else "Lost"}',f'**Bet:** {money(amount)} points\n{detail}\n\n'+(f'Congratulations! You received **{money(payout)} points**.' if won else 'Better luck next [...]')
    if image: e.set_image(url=f'attachment://{image.name}'); await ctx.send(embed=e,file=discord.File(image))
    else: await ctx.send(embed=e)

def result_embed(game, amount, won, multiplier, detail):
    payout=(amount*multiplier).quantize(Decimal('0.01')) if won else Decimal('0')
    text=f'**Bet:** {money(amount)} points\n{detail}\n\n'
    return emb(f'{game} - You {"Won" if won else "Lost"}', text+(f'Congratulations! You received **{money(payout)} points**.' if won else 'Better luck next time.'), GREEN if won else RED)

RANKS=['2','3','4','5','6','7','8','9','10','J','Q','K','A']
ACTIVE_MINES={}

# Litecoin address derivation helper
def derive_ltc_address_from_xpub(xpub: str, index: int) -> str:
    """
    Derive a Litecoin mainnet address at path m/0/index from an extended public key (xpub/ltub/etc).
    Requires bip_utils.
    """
    if Bip44 is None:
        raise RuntimeError('bip_utils is not available; install bip-utils to derive addresses')
    # Bip44 will produce Litecoin-compatible addresses when Bip44Coins.LITECOIN is used.
    try:
        bip_obj = Bip44.FromExtendedKey(xpub, Bip44Coins.LITECOIN)
        addr = bip_obj.Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
        return addr
    except Exception as e:
        # bubble up a clear error
        raise

class HiloView(discord.ui.View):
    def __init__(self, author_id, amount, rank, suit, server, client, public_hash):
        super().__init__(timeout=90); self.author_id=author_id; self.amount=amount; self.rank=rank; self.suit=suit; self.server=server; self.client=client; self.public_hash=public_hash; self.strea[...]
    def current_embed(self):
        return emb('Hi-Lo',f'**Bet Amount:** {money(self.amount)}\n**Current Multiplier:** {(Decimal("1")+Decimal(self.streak)*Decimal(".20")):.2f}x\n**Streak:** {self.streak}\n\nChoose whether the ne[...]')
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

# ... rest of the file unchanged until deposit command ...
