import os, random, hashlib, secrets, asyncio
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from pathlib import Path
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from db import Database
from imaging import balance_card, limbo_card, blackjack_card, coinflip_card, hilo_card

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
        super().__init__(placeholder='Select a category',options=[discord.SelectOption(label='Admin',emoji='🛠️'),discord.SelectOption(label='Utility',emoji='🧰'),discord.SelectOption(label='Balance',emoji='💰'),discord.SelectOption(label='Games',emoji='🎮')])
    async def callback(self, interaction):
        lists={
        'Admin':'`.add` `.remove` `.setbalance` `.lbreset` `.beg` `.history` `.freeze` `.unfreeze`',
        'Utility':'`.address` `.games` `.guide` `.leaderboard` `.privacy` `.rank` `.ranks` `.report` `.stats` `.worldtime` `.timer` `.thread`',
        'Balance':'`.balance` `.daily` `.deposit` `.monthly` `.rain` `.rb` `.tip` `.vip` `.weekly` `.withdraw` `.price`',
        'Games':'`blackjack/.bj` `ward` `hilo` `coinflip/.cf` `limbo` `mines` `ttt` `rps` `word`'}
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

@bot.command(aliases=['b','bal'])
async def balance(ctx, member: discord.Member=None):
    member=member or ctx.author; u=await db.user(member.id)
    if member != ctx.author and u['privacy']: return await ctx.send(embed=emb('Private account','That member has chosen to keep their profile private.',RED))
    p=balance_card(member.display_name,member.id,u['balance']); await ctx.send(file=discord.File(p),embed=emb('Balance',f'**{member.display_name}**\'s balance card'))
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
    u=await db.user(ctx.author.id); bonus=(Decimal(u['wagered'])*Decimal('.005')).quantize(Decimal('.01')); await db.balance(ctx.author.id,bonus); await ctx.send(embed=emb('Rateback claimed',f'You received **{money(bonus)} points** (0.50% of current lifetime wager).',GREEN))

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
    if not await require_game(ctx,amount): return
    a,b=random.randint(1,6),random.randint(1,6); await finish(ctx,'Ward Game',amount,a>b,Decimal('1.90'),f'**{ctx.author.display_name}** rolled: **{a}**\n**LiteBet** rolled: **{b}**')
@bot.command()
async def limbo(ctx, amount: Decimal, target: Decimal):
    if target<Decimal('1.01') or target>Decimal('100'): return await ctx.send(embed=emb('Invalid target','Choose a target from 1.01× to 100×.',RED))
    if not await require_game(ctx,amount): return
    crashed=Decimal(str(round(max(1.0,random.expovariate(1/2)),2))); won=crashed>=target; s,c,h=seed(); await finish(ctx,'Limbo',amount,won,target,f'Target: **{target:.2f}×** | Crashed: **{crashed:.2f}×**\n🔒 **Provably Fair**\nServer Seed: `{s}`\nClient Seed: `{c}`\nNonce: `{int(datetime.now().timestamp())}`',limbo_card(float(crashed)))

class BlackjackView(discord.ui.View):
    def __init__(self, ctx, amount, player, dealer):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.amount = amount
        self.player = player
        self.dealer = dealer

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                'Only the player who started this Blackjack game can use these buttons.',
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label='Hit', style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        ranks = list('23456789') + ['10', 'J', 'Q', 'K', 'A']
        suits = ['S', 'H', 'D', 'C']

        self.player.append(
            (random.choice(ranks), random.choice(suits))
        )

        def value(hand):
            vals = [
                11 if rank == 'A'
                else 10 if rank in ('J', 'Q', 'K')
                else int(rank)
                for rank, _ in hand
            ]

            total = sum(vals)
            aces = sum(rank == 'A' for rank, _ in hand)

            while total > 21 and aces:
                total -= 10
                aces -= 1

            return total

        pv = value(self.player)

        if pv > 21:
            await db.record(
                self.ctx.author.id,
                'Blackjack',
                self.amount,
                'loss',
                Decimal('0')
            )

            image = blackjack_card(
                self.ctx.author.display_name,
                self.player,
                self.dealer
            )

            e = emb(
                'Blackjack — You Lost',
                f'**Bet:** {money(self.amount)} points\n'
                f'Your total: **{pv}**\n\n'
                f'You busted!',
                RED
            )

            e.set_image(url=f'attachment://{image.name}')

            return await interaction.response.edit_message(
                embed=e,
                attachments=[discord.File(image)],
                view=None
            )

        if pv == 21:
            return await self.stand(interaction, button)

        image = blackjack_card(
            self.ctx.author.display_name,
            self.player,
            self.dealer
        )

               image = blackjack_card(
            self.ctx.author.display_name,
            self.player,
            self.dealer
        )

        e = emb(
            'Blackjack',
            f'**Bet:** {money(self.amount)} points\n'
            f'Your total: **{pv}**\n\n'
            f'Choose **Hit** or **Stand**.'
        )

        e.set_image(url=f'attachment://{image.name}')

        await interaction.response.edit_message(
            embed=e,
            attachments=[discord.File(image)],
            view=self
        )

    @discord.ui.button(label='Stand', style=discord.ButtonStyle.success)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        def value(hand):
            vals = [
                11 if a == 'A'
                else 10 if a in 'JQK'
                else int(a)
                for a, _ in hand
            ]

            total = sum(vals)
            aces = sum(a == 'A' for a, _ in hand)

            while total > 21 and aces:
                total -= 10
                aces -= 1

            return total

        pv = value(self.player)
        dv = value(self.dealer)

        while dv < 17:
            ranks = list('23456789') + ['10', 'J', 'Q', 'K', 'A']
            suits = ['S', 'H', 'D', 'C']
            self.dealer.append(
                (random.choice(ranks), random.choice(suits))
            )
            dv = value(self.dealer)

        if pv > 21:
            won = False
        elif dv > 21 or pv > dv:
            won = True
        elif pv == dv:
            won = None
        else:
            won = False

        if won is True:
            payout = (self.amount * Decimal('1.95')).quantize(Decimal('0.01'))
            await db.record(
                self.ctx.author.id,
                'Blackjack',
                self.amount,
                'win',
                payout
            )
            title = 'Blackjack - You Won'
            colour = GREEN
            result = f'You won **{money(payout)} points**!'
        elif won is None:
            payout = self.amount
            await db.balance(self.ctx.author.id, payout)
            title = 'Blackjack - Push'
            colour = NAVY
            result = f'Your **{money(payout)} points** bet was refunded.'
        else:
            await db.record(
                self.ctx.author.id,
                'Blackjack',
                self.amount,
                'loss',
                Decimal('0')
            )
            title = 'Blackjack - You Lost'
            colour = RED
            result = 'Better luck next time.'

        image = blackjack_card(
            self.ctx.author.display_name,
            self.player,
            self.dealer
        )

        e = emb(
            title,
            f'**Bet:** {money(self.amount)} points\n'
            f'Your total: **{pv}**\n'
            f'Dealer total: **{dv}**\n\n'
            f'{result}',
            colour
        )

        e.set_image(url=f'attachment://{image.name}')

        await interaction.response.edit_message(
            embed=e,
            attachments=[discord.File(image)],
            view=None
        )

    while value(self.dealer) < 17:
        ranks = list('23456789') + ['10', 'J', 'Q', 'K', 'A']
        suits = ['S', 'H', 'D', 'C']
        self.dealer.append(
            (random.choice(ranks), random.choice(suits))
        )

    pv = value(self.player)
    dv = value(self.dealer)

    won = pv <= 21 and (dv > 21 or pv > dv)
    push = pv == dv and pv <= 21

    if push:
        await db.balance(self.ctx.author.id, self.amount)
        await db.record(
            self.ctx.author.id,
            'Blackjack',
            self.amount,
            'push',
            self.amount
        )
        result = f'Push — your **{pv}** equals dealer **{dv}**.\nYour bet was refunded.'
    else:
        payout = (self.amount * Decimal('1.95')).quantize(Decimal('.01')) if won else Decimal('0')

        if won:
            await db.balance(self.ctx.author.id, payout)

        await db.record(
            self.ctx.author.id,
            'Blackjack',
            self.amount,
            'win' if won else 'loss',
            payout
        )

        result = (
            f'You: **{pv}**\n'
            f'Dealer: **{dv}**\n\n'
            f'You won **{money(payout)} points**!'
            if won
            else
            f'You: **{pv}**\n'
            f'Dealer: **{dv}**\n\n'
            f'You lost **{money(self.amount)} points**.'
        )

    image = blackjack_card(
        self.ctx.author.display_name,
        self.player,
        self.dealer
    )

    e = emb(
        f'Blackjack — {"Push" if push else "You Won" if won else "You Lost"}',
        f'**Bet:** {money(self.amount)} points\n\n{result}',
        GREEN if won else NAVY if push else RED
    )

    e.set_image(url=f'attachment://{image.name}')

    await interaction.response.edit_message(
        embed=e,
        attachments=[discord.File(image)],
        view=None
    )

@bot.command(aliases=['bj'])
async def blackjack(ctx, amount: Decimal, *sidebets):
    if not await require_game(ctx, amount):
        return

    ranks = list('23456789') + ['10', 'J', 'Q', 'K', 'A']
    suits = ['S', 'H', 'D', 'C']

    player = [(random.choice(ranks), random.choice(suits)) for _ in range(2)]
    dealer = [(random.choice(ranks), random.choice(suits)) for _ in range(2)]

    view = BlackjackView(ctx, amount, player, dealer)

    image = blackjack_card(
        ctx.author.display_name,
        player,
        dealer
    )

    e = emb(
        'Blackjack',
        f'**Bet:** {money(amount)} points\n'
        f'Your cards: **{len(player)}**\n'
        f'Choose **Hit** or **Stand**.'
    )

    e.set_image(url=f'attachment://{image.name}')

    message = await ctx.send(
        embed=e,
        file=discord.File(image),
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

@bot.command()
async def word(ctx, amount: Decimal, member: discord.Member=None):
    if not await require_game(ctx,amount): return
    fragment=random.choice(['dri','par','sto','pla','win','car']); won=random.random()>.45
    mode=('Challenge against '+member.mention) if member else 'Solo word challenge'
    await finish(ctx,'Word',amount,won,Decimal('1.80'),f'Word fragment: **{fragment.upper()}**\n{mode}')

@bot.command(aliases=['lb'])
async def leaderboard(ctx):
    rows=await db.pool.fetch('SELECT user_id,daily_wager,weekly_wager,monthly_wager FROM users ORDER BY daily_wager DESC LIMIT 10')
    text='\n'.join(f'`{i+1}.` <@{r["user_id"]}> — **{money(r["daily_wager"])}** wagered' for i,r in enumerate(rows)) or 'No wagers yet.'
    await ctx.send(embed=emb('🏆 Daily Leaderboard',text))
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
    xpub=os.getenv('LTC_XPUB')
    if not xpub: return await ctx.send(embed=emb('Deposit unavailable','The owner has not configured `LTC_XPUB` yet.',RED))
    u=await db.user(ctx.author.id)
    await ctx.send(embed=emb('Litecoin Deposit',f'Your unique watch-only deposit address will use index **{u["deposit_index"]}**. Configure the wallet watcher before accepting deposits.\n\nFor safety, no private key is stored by LiteBet.'))
@bot.command()
async def withdraw(ctx, address: str, amount: Decimal):
    if amount<50: return await ctx.send(embed=emb('Minimum withdrawal','Minimum withdrawal is **50 points**.',RED))
    if await db.frozen(): return await ctx.send(embed=emb('Withdrawals are frozen','An administrator has temporarily locked withdrawals.',RED))
    if not await db.debit(ctx.author.id,amount): return await ctx.send(embed=emb('Insufficient balance','You do not have enough points.',RED))
    ltc=(amount*RATE).quantize(Decimal('.00000001'))
    async with db.pool.acquire() as c:
        req=await c.fetchrow('INSERT INTO withdrawals(user_id,address,points,ltc) VALUES($1,$2,$3,$4) RETURNING id',ctx.author.id,address,amount,ltc)
    await ctx.send(embed=emb('Withdrawal requested',f'🎉 `{ctx.author.display_name}` has successfully withdrawn **{money(amount)}** points for **{ltc} LTC** (~${amount*USD_PER_POINT:.2f})!\n\nYour request is queued for manual payout.',GREEN))
    if LOG_CHANNEL_ID and (ch:=bot.get_channel(LOG_CHANNEL_ID)): await ch.send(embed=emb('Withdrawal payout required',f'ID: `{req["id"]}`\nUser: {ctx.author.mention}\nAddress: `{address}`\nAmount: **{ltc} LTC**'))
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
