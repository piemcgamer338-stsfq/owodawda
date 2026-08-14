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
    e=emb(f'{game} — You {"Won" if won else "Lost"}',f'**Bet:** {money(amount)} points\n{detail}\n\n'+(f'Congratulations! You received **{money(payout)} points**.' if won else 'Better luck next time.'))
    if image: e.set_image(url=f'attachment://{image.name}'); await ctx.send(embed=e,file=discord.File(image))
    else: await ctx.send(embed=e)

def result_embed(game, amount, won, multiplier, detail):
    payout=(amount*multiplier).quantize(Decimal('0.01')) if won else Decimal('0')
    text=f'**Bet:** {money(amount)} points\n{detail}\n\n'
    return emb(f'{game} - You {"Won" if won else "Lost"}', text+(f'Congratulations! You received **{money(payout)} points**.' if won else 'Better luck next time.'), GREEN if won else RED)

RANKS=['2','3','4','5','6','7','8','9','10','J','Q','K','A']
ACTIVE_MINES={}

# Helper to derive Litecoin address from xpub
def derive_ltc_address_from_xpub(xpub: str, index: int) -> str:
    if Bip44 is None:
        raise RuntimeError('bip_utils not installed')
    # Use Bip44 for Litecoin mainnet; derive m/0/index (external chain)
    bip_obj = Bip44.FromExtendedKey(xpub, Bip44Coins.LITECOIN)
    addr = bip_obj.Change(Bip44Changes.CHAIN_EXT).AddressIndex(int(index)).PublicKey().ToAddress()
    return addr

class HiloView(discord.ui.View):
    def __init__(self, author_id, amount, rank, suit, server, client, public_hash):
        super().__init__(timeout=90); self.author_id=author_id; self.amount=amount; self.rank=rank; self.suit=suit; self.server=server; self.client=client; self.public_hash=public_hash; self.streak=0;[...]
    def current_embed(self):
        return emb('Hi-Lo',f'**Bet Amount:** {money(self.amount)}\n**Current Multiplier:** {(Decimal("1")+Decimal(self.streak)*Decimal(".20")):.2f}x\n**Streak:** {self.streak}\n\nChoose whether the ne[...]
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

@bot.command(aliases=['cf','coinfip'])
async def coinflip(ctx, amount: str, choice: str=None):
    amount=await resolve_amount(ctx,amount)
    if not await require_game(ctx,amount): return
    choice=(choice or random.choice(['heads','tails'])).lower(); choice='heads' if choice in ('h','head','heads') else 'tails'; landed=random.choice(['heads','tails']); s,c,h=seed()
    await asyncio.sleep(2); await finish(ctx,'Coinflip',amount,choice==landed,Decimal('1.92'),f'**Choice:** {choice.title()}\n**Landed:** {landed.title()}\n🔒 **Provably Fair**\nPublic Hash: `{h}`\nServer Seed: `{s}`\nClient Seed: `{c}`')

# ... other commands unchanged ...

@bot.command(aliases=['depo'])
async def deposit(ctx):
    """Generate and return a watch-only Litecoin deposit address derived from LTC_XPUB.

    - Uses the environment variable LTC_XPUB (must be set in Railway).
    - If the user already has deposit_address, returns the same address.
    - Otherwise derives address at m/0/index using the user's deposit_index, stores it, and increments deposit_index atomically.
    - Tries to DM the user; falls back to channel if DMs are blocked.
    """
    xpub = os.getenv('LTC_XPUB')
    if not xpub:
        return await ctx.send(embed=emb('Deposit unavailable', 'The owner has not configured LTC_XPUB yet.', RED))

    if Bip44 is None:
        return await ctx.send(embed=emb('Deposit unavailable', 'Server missing dependency: install bip-utils (pip install bip-utils).', RED))

    try:
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", ctx.author.id)
                row = await conn.fetchrow("SELECT deposit_address, deposit_index FROM users WHERE user_id=$1 FOR UPDATE", ctx.author.id)

                if row and row.get('deposit_address'):
                    address = row['deposit_address']
                else:
                    index = int(row['deposit_index']) if row and row['deposit_index'] is not None else 0
                    try:
                        # derive Litecoin address on mainnet at m/0/index
                        bip_obj = Bip44.FromExtendedKey(xpub, Bip44Coins.LITECOIN)
                        address = bip_obj.Change(Bip44Changes.CHAIN_EXT).AddressIndex(index).PublicKey().ToAddress()
                    except Exception:
                        return await ctx.send(embed=emb('Derivation error', 'Could not derive an address from the configured LTC_XPUB. Ensure LTC_XPUB is a valid Litecoin extended public key (watch-only) for mainnet.', RED))

                    await conn.execute("UPDATE users SET deposit_address=$2, deposit_index=deposit_index+1 WHERE user_id=$1", ctx.author.id, address)

    except Exception:
        return await ctx.send(embed=emb('Deposit error', 'An internal error occurred while generating your deposit address. Try again later.', RED))

    # Send via DM if possible, otherwise post in channel
    embed_msg = emb('Litecoin Deposit', f'Your unique LTC deposit address:\n\n`{address}`\n\nSend LTC to this address.')
    try:
        await ctx.author.send(embed=embed_msg)
        # Inform the channel that a DM was sent
        try:
            await ctx.send(embed=emb('Litecoin Deposit', 'I DMed your unique deposit address. Check your DMs.'))
        except Exception:
            pass
    except discord.Forbidden:
        await ctx.send(embed=embed_msg)

@bot.command()
async def withdraw(ctx, address: str, amount: Decimal):
    if amount<50: return await ctx.send(embed=emb('Minimum withdrawal','Minimum withdrawal is **50 points**.',RED))
    if await db.frozen(): return await ctx.send(embed=emb('Withdrawals are frozen','An administrator has temporarily locked withdrawals.',RED))
    if not await db.debit(ctx.author.id,amount): return await ctx.send(embed=emb('Insufficient balance','You do not have enough points.',RED))
    ltc=(amount*RATE).quantize(Decimal('.00000001'))
    async with db.pool.acquire() as c:
        req=await c.fetchrow('INSERT INTO withdrawals(user_id,address,points,ltc) VALUES($1,$2,$3,$4) RETURNING id',ctx.author.id,address,amount,ltc)
    await ctx.send(embed=emb('Withdrawal requested',f'🎉 `{ctx.author.display_name}` has successfully withdrawn **{money(amount)}** points for **{ltc} LTC** (~${amount*USD_PER_POINT:.2f})!\n\nYour request ID is `{req["id"]}` and will be processed by the operator.',GREEN))
    if LOG_CHANNEL_ID and (ch:=bot.get_channel(LOG_CHANNEL_ID)): await ch.send(embed=emb('Withdrawal payout required',f'ID: `{req["id"]}`\nUser: {ctx.author.mention}\nAddress: `{address}`\nAmount: **{money(amount)} points**'))

# ... remainder of file unchanged ...

async def main():
    if not TOKEN or not DB_URL: raise RuntimeError('Set DISCORD_TOKEN and DATABASE_URL in Railway Variables.')
    await db.connect()
    async with bot: await bot.start(TOKEN)
if __name__=='__main__': asyncio.run(main())
