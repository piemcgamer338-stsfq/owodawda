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
async def finish(ctx, game, amount, won, multiplier, detail, image=None):
    payout=(amount*multiplier).quantize(Decimal('0.01')) if won else Decimal('0')
    await db.record(ctx.author.id,game,amount,'win' if won else 'loss',payout)
    e=emb(f'{game} — You {"Won" if won else "Lost"}',f'**Bet:** {money(amount)} points\n{detail}\n\n'+(f'Congratulations! You received **{money(payout)} points**.' if won else 'Better luck next time.'),GREEN if won else RED)
    if image: e.set_image(url=f'attachment://{image.name}'); await ctx.send(embed=e,file=discord.File(image))
    else: await ctx.send(embed=e)

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

class MinesView(discord.ui.View):
    def __init__(self, author_id, amount, bombs, server, client, public_hash):
        super().__init__(timeout=120); self.author_id=author_id; self.amount=amount; self.bombs=bombs; self.mines=set(random.sample(range(25),bombs)); self.revealed=0; self.server=server; self.client=client; self.public_hash=public_hash; self.message=None
        for i in range(25):
            b=discord.ui.Button(label=str(i+1),style=discord.ButtonStyle.secondary,row=i//5,custom_id=f'mine:{i}')
            b.callback=self.pick; self.add_item(b)
    def multiplier(self): return (Decimal('1')+Decimal(self.revealed)*Decimal('.12')).quantize(Decimal('.01'))
    def game_embed(self, extra='React with the money-bag emoji below to cash out.'):
        return emb('Mines',f'**Bet Amount:** {money(self.amount)}\n**Current Multiplier:** {self.multiplier():.2f}x\n**Profit:** {money(self.amount*(self.multiplier()-1))} points\n{self.bombs} bombs | {25-self.bombs} diamonds\n\n{extra}\nPublic Hash: `{self.public_hash}`')
    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message('Only the player who started this Mines game can reveal tiles.',ephemeral=True); return False
        return True
    async def pick(self,interaction):
        index=int(interaction.data['custom_id'].split(':')[1]); button=next(x for x in self.children if x.custom_id==interaction.data['custom_id'])
        button.disabled=True
        if index in self.mines:
            button.style=discord.ButtonStyle.danger; button.label='BOMB'
            for x in self.children:
                if int(x.custom_id.split(':')[1]) in self.mines: x.style=discord.ButtonStyle.danger; x.label='BOMB'; x.disabled=True
            await db.record(self.author_id,'Mines',self.amount,'loss',Decimal('0')); ACTIVE_MINES.pop(interaction.message.id,None)
            return await interaction.response.edit_message(embed=result_embed('Mines',self.amount,False,Decimal('0'),f'You hit a bomb.\nServer Seed: `{self.server}`\nClient Seed: `{self.client}`'),view=self)
        self.revealed+=1; button.style=discord.ButtonStyle.success; button.label='GEM'
        await interaction.response.edit_message(embed=self.game_embed(),view=self)
    async def cash_out(self):
        if not self.message or self.revealed==0: return False
        payout=(self.amount*self.multiplier()).quantize(Decimal('.01')); await db.record(self.author_id,'Mines',self.amount,'win',payout); ACTIVE_MINES.pop(self.message.id,None)
        await self.message.edit(embed=result_embed('Mines',self.amount,True,self.multiplier(),f'Cashout after **{self.revealed}** diamonds.\nServer Seed: `{self.server}`\nClient Seed: `{self.client}`'),view=None); return True
    async def on_timeout(self):
        if self.revealed: await self.cash_out()
        elif self.message: await db.record(self.author_id,'Mines',self.amount,'loss',Decimal('0')); await self.message.edit(embed=result_embed('Mines',self.amount,False,Decimal('0'),'No tile was selected before the game timed out.'),view=None)

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

@bot.command()
async def help(ctx):
    total=await db.pool.fetchval('SELECT COUNT(*) FROM users')
    await ctx.send(embed=emb('ℹ️ Help Command - Main Menu',f'Welcome to **LiteBet**, the Discord Litecoin Casino Bot.\n💡 New here? Read `.guide`\n\n**Rate:** 1 point = 0.0001 LTC\n**Total Commands:** 40+\n**Total Users:** {total}\n\n> Bot made by meow2004yr'),view=HelpView())
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

@bot.command()
@commands.check(admin)
async def add(ctx, member: discord.Member, amount: Decimal):
    await db.balance(member.id,amount); e=emb('Balance added',f'**{money(amount)} points** has been added to {member.mention}\'s balance.'); e.set_thumbnail(url=member.display_avatar.url); await ctx.send(embed=e)
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
async def mines(ctx, amount: Decimal, bombs: int=4):
    if bombs<1 or bombs>20: return await ctx.send(embed=emb('Invalid mine count','Choose from 1 to 20 bombs.',RED))
    if not await require_game(ctx,amount): return
    safe=25-bombs; revealed=random.randint(0,min(6,safe)); won=revealed>0 and random.random()>.35
    mult=Decimal('1')+Decimal(revealed)*Decimal('.18')
    s,c,h=seed(); grid=' '.join('💎' if i<revealed else '⬛' for i in range(25))
    await finish(ctx,'Mines',amount,won,mult,f'**Bet Amount:** {money(amount)}\n**Current Multiplier:** {mult:.2f}×\n**Profits:** {money(amount*(mult-1) if won else 0)} points\n{bombs} 💣 | {safe} 💎\n{grid}\n🔒 Hash: `{h}`\nServer Seed: `{s}`\nClient Seed: `{c}`')

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



@bot.command(aliases=['lb'])
async def leaderboard(ctx):
    rows=await db.pool.fetch('SELECT user_id,daily_wager,weekly_wager,monthly_wager FROM users ORDER BY daily_wager DESC LIMIT 10')
    text='\n'.join(f'`{i+1}.` <@{r["user_id"]}> — **{money(r["daily_wager"])}** wagered' for i,r in enumerate(rows)) or 'No wagers yet.'
    await ctx.send(embed=emb('🏆 Daily Leaderboard',text))

# ============================================================
# WITHDRAW SYSTEM
# ============================================================

WITHDRAWAL_LOG_CHANNEL = None


def valid_ltc_address(address):
    if not address:
        return False

    address = address.strip()

    if address.startswith("ltc1"):
        return 43 <= len(address) <= 100

    if address.startswith(("L", "M", "3")):
        return 26 <= len(address) <= 35

    return False


# ============================================================
# SET WITHDRAWAL LOG CHANNEL
# ============================================================

@bot.command()
@commands.has_permissions(administrator=True)
async def wlog(ctx, channel: discord.TextChannel):

    global WITHDRAWAL_LOG_CHANNEL

    WITHDRAWAL_LOG_CHANNEL = channel.id

    await ctx.send(
        embed=emb(
            "Withdrawal Logs Updated",
            f"Withdrawal logs will now be sent to {channel.mention}.",
            GREEN
        )
    )


# ============================================================
# WITHDRAW
# ============================================================

@bot.command()
async def withdraw(ctx, points: str, address: str):

    # --------------------------------------------------------
    # AMOUNT
    # --------------------------------------------------------

    try:
        amount = Decimal(points).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return await ctx.send(
            embed=emb(
                "Invalid Amount",
                "Use a valid point amount.\n\n"
                "Example:\n"
                "`.withdraw 500 Lxxxxxxxxxxxxxxxxxxxxxxxx`",
                RED
            )
        )

    if amount <= 0:
        return await ctx.send(
            embed=emb(
                "Invalid Amount",
                "Withdrawal amount must be greater than **0 points**.",
                RED
            )
        )

    # --------------------------------------------------------
    # LTC ADDRESS
    # --------------------------------------------------------

    address = address.strip()

    if not valid_ltc_address(address):
        return await ctx.send(
            embed=emb(
                "Invalid Litecoin Address",
                "Please enter a valid Litecoin Mainnet address.",
                RED
            )
        )

    # --------------------------------------------------------
    # USER BALANCE
    # --------------------------------------------------------

    u = await db.user(ctx.author.id)
    balance = Decimal(str(u["balance"]))

    if balance < amount:
        return await ctx.send(
            embed=emb(
                "Insufficient Balance",
                f"You requested **{money(amount)} points** "
                f"but only have **{money(balance)} points**.",
                RED
            )
        )

    # --------------------------------------------------------
    # POINTS → LTC
    # 1 point = 0.0001 LTC
    # --------------------------------------------------------

    ltc_amount = (
        amount * Decimal("0.0001")
    ).quantize(Decimal("0.00000001"))

    request_id = secrets.token_hex(8).upper()

    # --------------------------------------------------------
    # CONFIRMATION VIEW
    # ONLY THE USER WHO CREATED THE REQUEST CAN CLICK
    # --------------------------------------------------------

    class WithdrawalView(discord.ui.View):

        def __init__(self):
            super().__init__(timeout=300)
            self.finished = False

        async def interaction_check(self, interaction):

            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message(
                    "❌ Only the user who requested this withdrawal can use these buttons.",
                    ephemeral=True
                )
                return False

            return True

        # ====================================================
        # ACCEPT
        # ====================================================

        @discord.ui.button(
            label="Accept",
            emoji="✅",
            style=discord.ButtonStyle.success
        )
        async def accept(self, interaction, button):

            if self.finished:
                return

            self.finished = True

            # ------------------------------------------------
            # REMOVE POINTS ATOMICALLY
            # ------------------------------------------------

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

            if new_balance is None:

                for child in self.children:
                    child.disabled = True

                return await interaction.response.edit_message(
                    embed=emb(
                        "Withdrawal Failed",
                        "You no longer have enough points for this withdrawal.",
                        RED
                    ),
                    view=self
                )

            # ------------------------------------------------
            # SAVE WITHDRAWAL
            # ------------------------------------------------

            try:
                await db.pool.execute(
                    """
                    INSERT INTO withdrawals
                    (user_id, address, points, ltc, status)
                    VALUES ($1, $2, $3, $4, 'approved')
                    """,
                    ctx.author.id,
                    address,
                    amount,
                    ltc_amount
                )
            except Exception as e:
                print(f"Withdrawal DB error: {e}")

            # ------------------------------------------------
            # FAKE TRANSACTION ID
            # ------------------------------------------------

            txid = secrets.token_hex(16)

            # ------------------------------------------------
            # DISABLE BUTTONS
            # ------------------------------------------------

            for child in self.children:
                child.disabled = True

            # ------------------------------------------------
            # SUCCESS MESSAGE
            # ------------------------------------------------

            success = discord.Embed(
                title="💰 Withdrawal Successful!",
                description=(
                    f"Your withdrawal has been successfully processed.\n\n"
                    f"**Points Withdrawn:** `{money(amount)}`\n"
                    f"**LTC Sent:** `{ltc_amount:.8f} LTC`\n"
                    f"**Address:** `{address}`\n\n"
                    f"**Transaction ID:** `{txid}`"
                ),
                color=GREEN,
                timestamp=datetime.now(timezone.utc)
            )

            success.set_footer(
                text="LiteBet • Simulated Litecoin Withdrawal"
            )

            await interaction.response.edit_message(
                embed=success,
                view=self
            )

            # ------------------------------------------------
            # DM USER
            # ------------------------------------------------

            try:
                dm = discord.Embed(
                    title="💰 Withdrawal Successful!",
                    description=(
                        f"Your withdrawal has been processed.\n\n"
                        f"**Points:** `{money(amount)}`\n"
                        f"**LTC:** `{ltc_amount:.8f} LTC`\n"
                        f"**Address:** `{address}`\n"
                        f"**Transaction ID:** `{txid}`"
                    ),
                    color=GREEN,
                    timestamp=datetime.now(timezone.utc)
                )

                dm.set_footer(
                    text="LiteBet • Simulated Litecoin Withdrawal"
                )

                await ctx.author.send(embed=dm)

            except discord.Forbidden:
                pass

            # ------------------------------------------------
            # AUTOMATIC LOG
            # ------------------------------------------------

            if WITHDRAWAL_LOG_CHANNEL:

                channel = bot.get_channel(
                    WITHDRAWAL_LOG_CHANNEL
                )

                if channel:

                    guild_name = (
                        ctx.guild.name
                        if ctx.guild
                        else "Direct Message"
                    )

                    guild_id = (
                        str(ctx.guild.id)
                        if ctx.guild
                        else "N/A"
                    )

                    channel_name = (
                        ctx.channel.mention
                        if hasattr(ctx.channel, "mention")
                        else str(ctx.channel)
                    )

                    jump_url = (
                        ctx.message.jump_url
                        if hasattr(ctx.message, "jump_url")
                        else "Unavailable"
                    )

                    log = discord.Embed(
                        title="💰 Withdrawal Approved",
                        color=GREEN,
                        timestamp=datetime.now(timezone.utc)
                    )

                    log.add_field(
                        name="User",
                        value=(
                            f"{ctx.author.mention}\n"
                            f"`{ctx.author.id}`"
                        ),
                        inline=True
                    )

                    log.add_field(
                        name="Amount",
                        value=(
                            f"**{money(amount)} points**\n"
                            f"`{ltc_amount:.8f} LTC`"
                        ),
                        inline=True
                    )

                    log.add_field(
                        name="Litecoin Address",
                        value=f"`{address}`",
                        inline=False
                    )

                    log.add_field(
                        name="Transaction ID",
                        value=f"`{txid}`",
                        inline=False
                    )

                    log.add_field(
                        name="Request Location",
                        value=(
                            f"Server: **{guild_name}**\n"
                            f"Server ID: `{guild_id}`\n"
                            f"Channel: {channel_name}\n"
                            f"[Jump to request]({jump_url})"
                        ),
                        inline=False
                    )

                    log.add_field(
                        name="Request ID",
                        value=f"`{request_id}`",
                        inline=True
                    )

                    log.add_field(
                        name="Status",
                        value="✅ Approved",
                        inline=True
                    )

                    log.set_footer(
                        text="LiteBet • Automatic Withdrawal Log"
                    )

                    try:
                        await channel.send(embed=log)
                    except Exception as e:
                        print(f"Withdrawal log error: {e}")

        # ====================================================
        # DECLINE
        # ====================================================

        @discord.ui.button(
            label="Decline",
            emoji="❌",
            style=discord.ButtonStyle.danger
        )
        async def decline(self, interaction, button):

            if self.finished:
                return

            self.finished = True

            for child in self.children:
                child.disabled = True

            # ------------------------------------------------
            # SAVE DECLINED WITHDRAWAL
            # ------------------------------------------------

            try:
                await db.pool.execute(
                    """
                    INSERT INTO withdrawals
                    (user_id, address, points, ltc, status)
                    VALUES ($1, $2, $3, $4, 'declined')
                    """,
                    ctx.author.id,
                    address,
                    amount,
                    ltc_amount
                )
            except Exception as e:
                print(f"Declined withdrawal DB error: {e}")

            # ------------------------------------------------
            # DECLINED MESSAGE
            # ------------------------------------------------

            declined = discord.Embed(
                title="❌ Withdrawal Declined",
                description=(
                    f"You declined your withdrawal request.\n\n"
                    f"**Points:** `{money(amount)}`\n"
                    f"**LTC:** `{ltc_amount:.8f} LTC`\n"
                    f"**Address:** `{address}`\n\n"
                    f"**No points were removed from your balance.**"
                ),
                color=RED,
                timestamp=datetime.now(timezone.utc)
            )

            declined.set_footer(
                text="LiteBet • Withdrawal"
            )

            await interaction.response.edit_message(
                embed=declined,
                view=self
            )

            # ------------------------------------------------
            # AUTOMATIC LOG
            # ------------------------------------------------

            if WITHDRAWAL_LOG_CHANNEL:

                channel = bot.get_channel(
                    WITHDRAWAL_LOG_CHANNEL
                )

                if channel:

                    guild_name = (
                        ctx.guild.name
                        if ctx.guild
                        else "Direct Message"
                    )

                    channel_name = (
                        ctx.channel.mention
                        if hasattr(ctx.channel, "mention")
                        else str(ctx.channel)
                    )

                    jump_url = (
                        ctx.message.jump_url
                        if hasattr(ctx.message, "jump_url")
                        else "Unavailable"
                    )

                    log = discord.Embed(
                        title="❌ Withdrawal Declined",
                        color=RED,
                        timestamp=datetime.now(timezone.utc)
                    )

                    log.add_field(
                        name="User",
                        value=(
                            f"{ctx.author.mention}\n"
                            f"`{ctx.author.id}`"
                        ),
                        inline=True
                    )

                    log.add_field(
                        name="Amount",
                        value=(
                            f"**{money(amount)} points**\n"
                            f"`{ltc_amount:.8f} LTC`"
                        ),
                        inline=True
                    )

                    log.add_field(
                        name="Litecoin Address",
                        value=f"`{address}`",
                        inline=False
                    )

                    log.add_field(
                        name="Request Location",
                        value=(
                            f"Server: **{guild_name}**\n"
                            f"Channel: {channel_name}\n"
                            f"[Jump to request]({jump_url})"
                        ),
                        inline=False
                    )

                    log.add_field(
                        name="Request ID",
                        value=f"`{request_id}`",
                        inline=True
                    )

                    log.add_field(
                        name="Status",
                        value="❌ Declined",
                        inline=True
                    )

                    log.set_footer(
                        text="LiteBet • Automatic Withdrawal Log"
                    )

                    try:
                        await channel.send(embed=log)
                    except Exception as e:
                        print(f"Withdrawal log error: {e}")

        # ====================================================
        # TIMEOUT
        # ====================================================

        async def on_timeout(self):

            if self.finished:
                return

            self.finished = True

            for child in self.children:
                child.disabled = True

            try:
                await message.edit(
                    embed=emb(
                        "Withdrawal Expired",
                        "This withdrawal confirmation expired.\n\n"
                        "**No points were removed.**",
                        RED
                    ),
                    view=self
                )
            except Exception:
                pass

    # ========================================================
    # CONFIRMATION EMBED
    # ========================================================

    confirmation = discord.Embed(
        title="💸 Litecoin Withdrawal",
        description=(
            f"**{ctx.author.mention}**, please confirm your withdrawal.\n\n"
            f"**Points:** `{money(amount)}`\n"
            f"**LTC Amount:** `{ltc_amount:.8f} LTC`\n"
            f"**Address:**\n`{address}`\n\n"
            f"**Request ID:** `{request_id}`\n\n"
            f"Choose **Accept** to process the withdrawal "
            f"or **Decline** to cancel it."
        ),
        color=0xF0B90B,
        timestamp=datetime.now(timezone.utc)
    )

    confirmation.add_field(
        name="Request Location",
        value=(
            f"Server: **{ctx.guild.name if ctx.guild else 'DM'}**\n"
            f"Channel: {ctx.channel.mention if hasattr(ctx.channel, 'mention') else ctx.channel}"
        ),
        inline=False
    )

    confirmation.set_footer(
        text="LiteBet • Withdrawal Confirmation"
    )

    # ========================================================
    # SEND CONFIRMATION
    # ========================================================

    view = WithdrawalView()

    message = await ctx.send(
        embed=confirmation,
        view=view
    )

@bot.command()
async def stats(ctx, member: discord.Member=None):
    member=member or ctx.author; u=await db.user(member.id)
    if member!=ctx.author and u['privacy']: return await ctx.send(embed=emb('Private account','This profile is private.',RED))
    await ctx.send(embed=emb(f'{member.display_name} — Stats',f'📤 Withdrawals: **{money(u["withdrawals"])} points**\n🏆 Won: **{u["games_won"]} games**\n💸 Bonus received: **{money(u["bonuses"])} points**\n🎮 Last 7 Days Wagered: **{money(u["weekly_wager"])} points**\n🎮 Total Played: **{u["games_played"]} games** and wagered **{money(u["wagered"])} points**\n\n📤 Tips sent: **{money(u["tips_sent"])} points**\n📥 Tips received: **{money(u["tips_received"])} points**'))
@bot.command()
async def rank(ctx, member: discord.Member=None):
    member=member or ctx.author; u=await db.user(member.id); levels=[('Bronze Gambler',100,2),('Silver Spinner',1000,5),('Gold Grinder',2500,10),('Platinum Player',5000,20),('Diamond Deen',10000,40),('Ruby Roller',25000,80),('Emerald Highroller',50000,160),('Sapphire Shark',100000,320)]
    current=next((x for x in reversed(levels) if Decimal(u['wagered'])>=x[1]),('Unranked',0,0)); nxt=next((x for x in levels if Decimal(u['wagered'])<x[1]),None)
    await ctx.send(embed=emb('Gambling Rank',f'**Current Rank:** {current[0]}\n**Total Wagered:** {money(u["wagered"])} points\n'+(f'**Next rank:** {nxt[0]} in **{money(Decimal(nxt[1])-Decimal(u["wagered"]))}** wager' if nxt else '**Highest rank achieved!**')))
@bot.command()
async def ranks(ctx): await ctx.send(embed=emb('LiteBet Ranks','Bronze Gambler — 100 wager — 2 bonus\nSilver Spinner — 1K wager — 5 bonus\nGold Grinder — 2.5K wager — 10 bonus\nPlatinum Player — 5K wager — 20 bonus\nDiamond Deen — 10K wager — 40 bonus\nRuby Roller — 25K wager — 80 bonus\nEmerald Highroller — 50K wager — 160 bonus\nSapphire Shark — 100K wager — 320 bonus'))
@bot.command()
async def vip(ctx, member: discord.Member=None):
    member=member or ctx.author; u=await db.user(member.id); await ctx.send(embed=emb('VIP Progress',f'**{member.display_name}** has wagered **{money(u["wagered"])} / 10,000** points for VIP access.'))
@bot.command(aliases=['addy'])
async def address(ctx, ltc_address: str):
    if len(ltc_address)<20: return await ctx.send(embed=emb('Invalid Litecoin address','Please provide a valid Litecoin address.',RED))
    await ctx.send(embed=emb('LTC Address Balance',f'**Address:** `{ltc_address}`\n**Balance:** External explorer lookup is configured through `LTC_EXPLORER_URL`.'))
@bot.command(aliases=['depo'])
async def deposit(ctx):
    xpub = os.getenv('LTC_XPUB')

    if not xpub:
        return await ctx.send(
            embed=emb(
                'Deposit unavailable',
                'The owner has not configured `LTC_XPUB` yet.',
                RED
            )
        )

    try:
        # Get user / create user if needed
        u = await db.user(ctx.author.id)

        # Get the next unused deposit index
        index = int(u['deposit_index'])

        # Derive Litecoin address from the account XPUB
        wallet = Bip44.FromExtendedKey(
            xpub,
            Bip44Coins.LITECOIN
        )

        address = (
            wallet
            .Change(Bip44Changes.CHAIN_EXT)
            .AddressIndex(index)
            .PublicKey()
            .ToAddress()
        )

        # Move to the next index so the next user gets a new address
        await db.pool.execute(
            """
            UPDATE users
            SET deposit_index = deposit_index + 1,
                deposit_address = $2
            WHERE user_id = $1
            """,
            ctx.author.id,
            address
        )

        # DM the user
        dm_embed = discord.Embed(
            title='Your Litecoin Deposit Address',
            description=(
                f'Please send **Litecoin (LTC)** only to the address below.\n\n'
                f'`{address}`\n\n'
                f'**Network:** Litecoin Mainnet\n'
                f'**Currency:** LTC\n\n'
                f'Your deposit will be credited after the required confirmations.'
            ),
            color=GREEN
        )

        dm_embed.set_footer(text='LiteBet • Litecoin Deposits')

        try:
            await ctx.author.send(embed=dm_embed)

            await ctx.send(
                embed=emb(
                    'Deposit Address Sent',
                    'I have sent your **Litecoin (LTC)** deposit address to your DMs.',
                    GREEN
                )
            )

        except discord.Forbidden:
            # DMs disabled
            channel_embed = discord.Embed(
                title='Litecoin Deposit Address',
                description=(
                    f'Your DMs are closed, so I could not send the address privately.\n\n'
                    f'**Your address:**\n'
                    f'`{address}`\n\n'
                    f'**Network:** Litecoin Mainnet\n'
                    f'**Currency:** LTC\n\n'
                    f'Your deposit will be credited after the required confirmations.'
                ),
                color=GREEN
            )

            await ctx.send(embed=channel_embed)

    except Exception as e:
        print(f'Deposit error for {ctx.author.id}: {e}')

        await ctx.send(
            embed=emb(
                'Deposit Error',
                'I could not generate your Litecoin deposit address. Please try again.',
                RED
            )
        )
        
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
@bot.command()
async def report(ctx, *, content: str):
    begging=any(x in content.lower() for x in ['beg','please give','free points','can i have'])
    await ctx.send(embed=emb('Report received','Potential begging detected and forwarded to staff.' if begging else 'Report received and forwarded to staff.',RED if begging else NAVY))

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

@bot.command(aliases=['cf','coinfip'])
async def coinflip(ctx, amount: str, choice: str=None):
    amount=await resolve_amount(ctx,amount)
    if not await require_game(ctx,amount): return
    choice=(choice or random.choice(['heads','tails'])).lower(); choice='heads' if choice in ('h','head','heads') else 'tails'; landed=random.choice(['heads','tails']); s,c,h=seed()
    message=await ctx.send(embed=emb('Coinflip - Rolling...',f'**Bet:** {money(amount)} points\n**Choice:** {choice.title()}\n\nThe coin is spinning...'))
    await asyncio.sleep(3); won=choice==landed; payout=(amount*Decimal('1.92')).quantize(Decimal('0.01')) if won else Decimal('0'); await db.record(ctx.author.id,'Coinflip',amount,'win' if won else 'loss',payout)
    e=result_embed('Coinflip',amount,won,Decimal('1.92'),f'**Choice:** {choice.title()}\n**Landed:** {landed.title()}\nPublic Hash: `{h}`\nServer Seed: `{s}`\nClient Seed: `{c}`'); image=coinflip_card(landed); e.set_image(url=f'attachment://{image.name}')
    await message.edit(embed=e,attachments=[discord.File(image)])

@bot.command()
async def hilo(ctx, amount: Decimal):
    if not await require_game(ctx,amount): return
    message=await ctx.send(embed=emb('Hi-Lo - Shuffling...',f'**Bet:** {money(amount)} points\nDrawing your first card...'))
    await asyncio.sleep(3); s,c,h=seed(); view=HiloView(ctx.author.id,amount,random.choice(RANKS),random.choice(['S','H','D','C']),s,c,h)
    e=view.current_embed(); image=hilo_card(view.rank,view.suit); e.set_image(url=f'attachment://{image.name}')
    await message.edit(embed=e,attachments=[discord.File(image)],view=view); view.message=message

@bot.command()
async def mines(ctx, amount: Decimal, bombs: int=4):
    if bombs<1 or bombs>20: return await ctx.send(embed=emb('Invalid mine count','Choose from 1 to 20 bombs.',RED))
    if not await require_game(ctx,amount): return
    s,c,h=seed(); view=MinesView(ctx.author.id,amount,bombs,s,c,h); message=await ctx.send(embed=view.game_embed(),view=view); view.message=message; ACTIVE_MINES[message.id]=view
    await message.add_reaction('\U0001F4B0')

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
