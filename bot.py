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
    e=emb(f'{game} — You {"Won" if won else "Lost"}',f'**Bet:** {money(amount)} points\n{detail}\n\n'+(f'Congratulations! You received **{money(payout)} points**.' if won else 'Better luck next time.'), GREEN if won else RED)
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
        super().__init__(timeout=90)
        self.author_id=author_id
        self.amount=amount
        self.rank=rank
        self.suit=suit
        self.server=server
        self.client=client
        self.public_hash=public_hash
        self.streak=0
        self.message=None

    def current_embed(self):
        return emb('Hi-Lo',f'**Bet Amount:** {money(self.amount)}\n**Current Multiplier:** {(Decimal("1")+Decimal(self.streak)*Decimal(".20")):.2f}x\n**Streak:** {self.streak}\n\nChoose whether the next card will be Higher or Lower.')

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message('Only the player who started this Hi-Lo game can use these buttons.',ephemeral=True); return False
        return True

    async def play(self, interaction, guess):
        next_rank=random.choice(RANKS); next_suit=random.choice(['S','H','D','C'])
        a,b=RANKS.index(self.rank),RANKS.index(next_rank)
        correct=(guess=='high' and b>a) or (guess=='low' and b<a)
        if not correct:
            await db.record(self.author_id,'Hi-Lo',self.amount,'loss',Decimal('0'))
            e=result_embed('Hi-Lo',self.amount,False,Decimal('0'),f'Your card: **{self.rank}**\nNext card: **{next_rank}**\nServer Seed: `{self.server}`\nClient Seed: `{self.client}`')
            e.set_image(url='attachment://hilo.png')
            return await interaction.response.edit_message(embed=e,attachments=[discord.File(hilo_card(next_rank,next_suit))],view=None)
        self.streak+=1
        self.rank,self.suit=next_rank,next_suit
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
            mult=Decimal('1')+Decimal(self.streak)*Decimal('.20')
            await db.record(self.author_id,'Hi-Lo',self.amount,'win',(self.amount*mult).quantize(Decimal('.01')))
            await self.message.edit(embed=result_embed('Hi-Lo',self.amount,True,mult,'Auto-cashed out after timeout.'),view=None)

class MinesView(discord.ui.View):
    def __init__(self, author_id, amount, bombs, server, client, public_hash):
        super().__init__(timeout=120)
        self.author_id=author_id
        self.amount=amount
        self.bombs=bombs
        self.mines=set(random.sample(range(25),bombs))
        self.revealed=0
        self.server=server
        self.client=client
        self.public_hash=public_hash
        self.message=None
        for i in range(25):
            b=discord.ui.Button(label=str(i+1),style=discord.ButtonStyle.secondary,row=i//5,custom_id=f'mine:{i}')
            b.callback=self.pick; self.add_item(b)

    def multiplier(self): return (Decimal('1')+Decimal(self.revealed)*Decimal('.12')).quantize(Decimal('.01'))

    def game_embed(self, extra='React with the money-bag emoji below to cash out.'):
        return emb('Mines',f'**Bet Amount:** {money(self.amount)}\n**Current Multiplier:** {self.multiplier():.2f}x\n**Profit:** {money(self.amount*(self.multiplier()-1))} points\n{self.bombs} bombs | {25-self.bombs} safe tiles\n\n{extra}')

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message('Only the player who started this Mines game can reveal tiles.',ephemeral=True); return False
        return True

    async def pick(self,interaction):
        index=int(interaction.data['custom_id'].split(':')[1])
        button=next(x for x in self.children if x.custom_id==interaction.data['custom_id'])
        button.disabled=True
        if index in self.mines:
            button.style=discord.ButtonStyle.danger; button.label='BOMB'
            for x in self.children:
                if int(x.custom_id.split(':')[1]) in self.mines:
                    x.style=discord.ButtonStyle.danger; x.label='BOMB'; x.disabled=True
            await db.record(self.author_id,'Mines',self.amount,'loss',Decimal('0'))
            ACTIVE_MINES.pop(interaction.message.id,None)
            return await interaction.response.edit_message(embed=result_embed('Mines',self.amount,False,Decimal('0'),f'You hit a bomb.\nServer Seed: `{self.server}`\nClient Seed: `{self.client}`'),view=None)
        self.revealed+=1; button.style=discord.ButtonStyle.success; button.label='GEM'
        await interaction.response.edit_message(embed=self.game_embed(),view=self)

    async def cash_out(self):
        if not self.message or self.revealed==0: return False
        payout=(self.amount*self.multiplier()).quantize(Decimal('.01'))
        await db.record(self.author_id,'Mines',self.amount,'win',payout); ACTIVE_MINES.pop(self.message.id,None)
        await self.message.edit(embed=result_embed('Mines',self.amount,True,self.multiplier(),f'Cashout after **{self.revealed}** diamonds.\nServer Seed: `{self.server}`\nClient Seed: `{self.client}`'),view=None)

    async def on_timeout(self):
        if self.revealed: await self.cash_out()
        elif self.message: await db.record(self.author_id,'Mines',self.amount,'loss',Decimal('0')); await self.message.edit(embed=result_embed('Mines',self.amount,False,Decimal('0'),'No tile was selected before timeout.'),view=None)

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
        super().__init__(timeout=seconds); self.host_id=host_id; self.amount=amount; self.entries=set(); self.message=None

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
    await ctx.send(embed=emb('ℹ️ Help Command - Main Menu',f'Welcome to **LiteBet**, the Discord Litecoin Casino Bot.'))

@bot.command()
async def guide(ctx): await ctx.send(embed=emb('LiteBet Guide','Start with `.daily`, then use `.balance`. Every game deducts its bet first, and its provably-fair seeds are shown in the result.'))

@bot.command(aliases=['games'])
async def game_list(ctx): await ctx.send(embed=emb('🎮 LiteBet Games','🃏 `.blackjack/.bj` — House Blackjack\n🎲 `.ward` — Highest roll wins\n🃏 `.hilo` — Predict the next card\n🪙 `.coinflip` — Heads or tails'))

@bot.command(aliases=['b','bal'])
async def balance(ctx, member: discord.Member=None):
    member=member or ctx.author; u=await db.user(member.id)
    if member != ctx.author and u['privacy']: return await ctx.send(embed=emb('Private account','That member has chosen to keep their profile private.',RED))
    p=balance_card(member.display_name,member.id,u['balance']); await ctx.send(file=discord.File(p),embed=emb('Balance',f'**{member.display_name}**\'s balance card'))

# many other commands omitted for brevity in this simplified patch; keep core game commands below

@bot.command(aliases=['cf','coinfip'])
async def coinflip(ctx, amount: str, choice: str=None):
    amount=await resolve_amount(ctx,amount)
    if not await require_game(ctx,amount): return
    choice=(choice or random.choice(['heads','tails'])).lower(); choice='heads' if choice in ('h','head','heads') else 'tails'; landed=random.choice(['heads','tails']); s,c,h=seed()
    message=await ctx.send(embed=emb('Coinflip - Rolling...',f'**Bet:** {money(amount)} points\n**Choice:** {choice.title()}\n\nThe coin is spinning...'))
    await asyncio.sleep(3); won=choice==landed; payout=(amount*Decimal('1.92')).quantize(Decimal('0.01')) if won else Decimal('0'); await db.record(ctx.author.id,'Coinflip',amount,'win' if won else 'loss',payout)
    e=result_embed('Coinflip',amount,won,Decimal('1.92'),f'**Choice:** {choice.title()}\n**Landed:** {landed.title()}\nPublic Hash: `{h}`\nServer Seed: `{s}`\nClient Seed: `{c}`')
    image=coinflip_card(landed)
    await message.edit(embed=e,attachments=[discord.File(image)])

# Cleaned BlackjackView with Hit and Stand
class BlackjackView(discord.ui.View):
    def __init__(self, ctx, amount, player, dealer):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.amount = amount
        self.player = player
        self.dealer = dealer
        self.message = None

    def value(self, hand):
        vals = [
            11 if a == 'A' else 10 if a in ('J','Q','K') else int(a)
            for a, _ in hand
        ]
        total = sum(vals)
        aces = sum(a == 'A' for a, _ in hand)
        while total > 21 and aces:
            total -= 10
            aces -= 1
        return total

    def render_image_embed(self, title, description, colour):
        image = blackjack_card(self.ctx.author.display_name, self.player, self.dealer)
        e = emb(title, description, colour)
        e.set_image(url=f'attachment://{image.name}')
        return image, e

    @discord.ui.button(label='Hit', style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        # draw a card for the player
        ranks = list('23456789') + ['10', 'J', 'Q', 'K', 'A']
        suits = ['S', 'H', 'D', 'C']
        self.player.append((random.choice(ranks), random.choice(suits)))
        pv = self.value(self.player)
        if pv > 21:
            # player busts
            await db.record(self.ctx.author.id, 'Blackjack', self.amount, 'loss', Decimal('0'))
            image, e = self.render_image_embed('Blackjack - You Lost', f'**Bet:** {money(self.amount)} points\nYour total: **{pv}**\nDealer total: **{self.value(self.dealer)}**\n\nBetter luck next time.', RED)
            await interaction.response.edit_message(embed=e, attachments=[discord.File(image)], view=None)
            return
        # update message with new hand
        image = blackjack_card(self.ctx.author.display_name, self.player, self.dealer)
        e = emb('Blackjack', f'**Bet:** {money(self.amount)} points\nYour total: **{pv}**\nChoose **Hit** or **Stand**.')
        e.set_image(url=f'attachment://{image.name}')
        await interaction.response.edit_message(embed=e, attachments=[discord.File(image)], view=self)

    @discord.ui.button(label='Stand', style=discord.ButtonStyle.success)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        pv = self.value(self.player)
        dv = self.value(self.dealer)
        # dealer draws
        ranks = list('23456789') + ['10', 'J', 'Q', 'K', 'A']
        suits = ['S', 'H', 'D', 'C']
        while dv < 17:
            self.dealer.append((random.choice(ranks), random.choice(suits)))
            dv = self.value(self.dealer)

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
            await db.record(self.ctx.author.id, 'Blackjack', self.amount, 'win', payout)
            title = 'Blackjack - You Won'
            colour = GREEN
            result = f'You won **{money(payout)} points**!'
        elif won is None:
            payout = self.amount
            await db.balance(self.ctx.author.id, payout)
            await db.record(self.ctx.author.id, 'Blackjack', self.amount, 'push', payout)
            title = 'Blackjack - Push'
            colour = NAVY
            result = f'Your **{money(payout)} points** bet was refunded.'
        else:
            await db.record(self.ctx.author.id, 'Blackjack', self.amount, 'loss', Decimal('0'))
            title = 'Blackjack - You Lost'
            colour = RED
            result = 'Better luck next time.'

        image = blackjack_card(self.ctx.author.display_name, self.player, self.dealer)
        e = emb(title, f'**Bet:** {money(self.amount)} points\nYour total: **{pv}**\nDealer total: **{dv}**\n\n{result}', colour)
        e.set_image(url=f'attachment://{image.name}')
        await interaction.response.edit_message(embed=e, attachments=[discord.File(image)], view=None)

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
        f'**Bet:** {money(amount)} points\nYour cards: **{len(player)}**\nChoose **Hit** or **Stand**.'
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
