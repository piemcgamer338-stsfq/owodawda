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
        super().__init__(timeout=90); self.author_id=author_id; self.amount=amount; self.rank=rank; self.suit=suit; self.server=server; self.client=client; self.public_hash=public_hash; self.streak=0; self.message=None
    def current_embed(self):
        return emb('Hi-Lo',f'**Bet Amount:** {money(self.amount)}\n**Current Multiplier:** {(Decimal("1")+Decimal(self.streak)*Decimal(".20")):.2f}x\n**Streak:** {self.streak}\n\nChoose whether the next card will be higher or lower than the current one.' )
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
        return emb('Mines',f'**Bet Amount:** {money(self.amount)}\n**Current Multiplier:** {self.multiplier():.2f}x\n**Profit:** {money(self.amount*(self.multiplier()-1))} points\n{self.bombs} bombs | {25-self.bombs} safe tiles\n\n{extra}')
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
            return await interaction.response.edit_message(embed=result_embed('Mines',self.amount,False,Decimal('0'),f'You hit a bomb.\nServer Seed: `{self.server}`\nClient Seed: `{self.client}`'),view=None)
        self.revealed+=1; button.style=discord.ButtonStyle.success; button.label='GEM'
        await interaction.response.edit_message(embed=self.game_embed(),view=self)
    async def cash_out(self):
        if not self.message or self.revealed==0: return False
        payout=(self.amount*self.multiplier()).quantize(Decimal('.01')); await db.record(self.author_id,'Mines',self.amount,'win',payout); ACTIVE_MINES.pop(self.message.id,None)
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

# ... (help, guide, many commands unchanged) ...

# For brevity keep rest of file unchanged until DuelInviteView and TttView sections

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
        # Safely debit: ensure we never leave one party debited without the other
        ok_host = await db.debit(self.host_id,self.amount)
        if not ok_host:
            return await interaction.response.edit_message(embed=emb(f'{self.game} challenge cancelled','Could not debit the challenger. Challenge cancelled.',RED),view=None)
        ok_guest = await db.debit(self.guest_id,self.amount)
        if not ok_guest:
            # refund host
            await db.balance(self.host_id,self.amount)
            return await interaction.response.edit_message(embed=emb(f'{self.game} challenge cancelled','Could not debit the challenged player. Challenge cancelled.',RED),view=None)
        # Start the game view
        if self.game=='Tic-Tac-Toe':
            view=TttView(self.host_id,self.guest_id,self.amount);
            await interaction.response.edit_message(embed=view.board_embed(),view=view)
        else:
            view=RpsView(self.host_id,self.guest_id,self.amount);
            await interaction.response.edit_message(embed=emb('Rock Paper Scissors',f'<@{self.host_id}> vs <@{self.guest_id}>\n\nBoth players choose their move using the buttons below.'),view=view)
        view.message=interaction.message
    @discord.ui.button(label='Decline',style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        await interaction.response.edit_message(embed=emb(f'{self.game} challenge declined',f'<@{self.guest_id}> declined the game. No points were deducted.',RED),view=None)
    async def on_timeout(self):
        if self.message: await self.message.edit(embed=emb(f'{self.game} challenge expired','No points were deducted.',RED),view=None)

# Replace the original demonstration commands with real interactive sessions.
for command_name in ('coinflip','hilo','mines','rps','ttt'):
    try:
        bot.remove_command(command_name)
    except Exception:
        pass

@bot.command(aliases=['cf','coinfip'])
async def coinflip(ctx, amount: str, choice: str=None):
    amount=await resolve_amount(ctx,amount)
    if not await require_game(ctx,amount): return
    # interactive coinflip handled elsewhere (unchanged here)
    choice=(choice or random.choice(['heads','tails'])).lower(); choice='heads' if choice in ('h','head','heads') else 'tails'; landed=random.choice(['heads','tails']); s,c,h=seed()
    message=await ctx.send(embed=emb('Coinflip - Rolling...',f'**Bet:** {money(amount)} points\n**Choice:** {choice.title()}\n\nThe coin is spinning...'))
    await asyncio.sleep(3); won=choice==landed; payout=(amount*Decimal('1.92')).quantize(Decimal('0.01')) if won else Decimal('0'); await db.record(ctx.author.id,'Coinflip',amount,'win' if won else 'loss',payout)
    e=result_embed('Coinflip',amount,won,Decimal('1.92'),f'**Choice:** {choice.title()}\n**Landed:** {landed.title()}\nPublic Hash: `{h}`\nServer Seed: `{s}`\nClient Seed: `{c}`'); image=coinflip_card()
    await message.edit(embed=e,attachments=[discord.File(image)])

# RPS and TTT challenge commands: ensure we DO NOT debit on challenge, only on accept, and set view.message so timeouts work.
@bot.command()
async def rps(ctx, member: discord.Member, amount: Decimal):
    if member.bot or member==ctx.author or amount<=0: return await ctx.send(embed=emb('Invalid challenge','Choose another member and a positive bet.',RED))
    host=await db.user(ctx.author.id); guest=await db.user(member.id)
    if Decimal(host['balance'])<amount or Decimal(guest['balance'])<amount:
        return await ctx.send(embed=emb('Challenge unavailable','Both players need the bet amount.',RED))
    view=DuelInviteView(ctx.author.id,member.id,amount,'Rock Paper Scissors')
    message=await ctx.send(embed=emb('Rock Paper Scissors Challenge',f'{ctx.author.mention} challenged {member.mention} for **{money(amount)} points**.\n\n**Current balances:**\n{ctx.author.mention}: **{money(Decimal(host["balance"]))}**\n{member.mention}: **{money(Decimal(guest["balance"]))}**'),view=view)
    view.message=message

@bot.command()
async def ttt(ctx, member: discord.Member, amount: Decimal):
    if member.bot or member==ctx.author or amount<=0: return await ctx.send(embed=emb('Invalid challenge','Choose another member and a positive bet.',RED))
    host=await db.user(ctx.author.id); guest=await db.user(member.id)
    if Decimal(host['balance'])<amount or Decimal(guest['balance'])<amount:
        return await ctx.send(embed=emb('Challenge unavailable','Both players need the bet amount.',RED))
    view=DuelInviteView(ctx.author.id,member.id,amount,'Tic-Tac-Toe')
    message=await ctx.send(embed=emb('Tic-Tac-Toe Challenge',f'{ctx.author.mention} challenged {member.mention} for **{money(amount)} points**.\n\n**Current balances:**\n{ctx.author.mention}: **{money(Decimal(host["balance"]))}**\n{member.mention}: **{money(Decimal(guest["balance"]))}**'),view=view)
    view.message=message

# Tic-Tac-Toe view/game
class TttView(discord.ui.View):
    def __init__(self, first_id, second_id, amount):
        super().__init__(timeout=180)
        self.players=[first_id,second_id]
        self.amount=amount
        self.turn=0
        self.board=['']*9
        self.message=None
        self.finished=False
        for i in range(9):
            b=discord.ui.Button(label=' ',style=discord.ButtonStyle.secondary,row=i//3,custom_id=f'ttt:{i}')
            b.callback=self.move; self.add_item(b)
    def board_embed(self, note=''):
        symbol='X' if self.turn==0 else 'O'
        board_lines=[]
        for i in range(9):
            board_lines.append(self.board[i] or str(i+1))
        board_display = f"{board_lines[0]} | {board_lines[1]} | {board_lines[2]}\n{board_lines[3]} | {board_lines[4]} | {board_lines[5]}\n{board_lines[6]} | {board_lines[7]} | {board_lines[8]}"
        return emb('Tic-Tac-Toe',f'<@{self.players[0]}> is **X**\n<@{self.players[1]}> is **O**\n\n{note}\n\nIt is <@{self.players[self.turn]}>\'s turn ({symbol}).\n\n{board_display}')
    async def interaction_check(self, interaction):
        if self.finished:
            await interaction.response.send_message('This game has already finished.',ephemeral=True); return False
        if interaction.user.id != self.players[self.turn]:
            await interaction.response.send_message('It is not your turn.',ephemeral=True); return False
        return True
    async def move(self, interaction):
        if self.finished:
            return await interaction.response.send_message('This game has already finished.',ephemeral=True)
        index=int(interaction.data['custom_id'].split(':')[1])
        if self.board[index]: return await interaction.response.send_message('That square is already taken.',ephemeral=True)
        mark='X' if self.turn==0 else 'O'; self.board[index]=mark
        # update button visual
        button=next(x for x in self.children if getattr(x,'custom_id',None)==interaction.data['custom_id'])
        button.label=mark; button.disabled=True; button.style=discord.ButtonStyle.primary if mark=='X' else discord.ButtonStyle.success
        wins=((0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6))
        # Check for win
        if any(all(self.board[i]==mark for i in line) for line in wins):
            self.finished=True
            winner=self.players[self.turn]; payout=(self.amount*2).quantize(Decimal('.01'))
            await db.balance(winner,payout)
            await db.record(winner,'Tic-Tac-Toe',self.amount,'win',payout)
            await db.record(self.players[1-self.turn],'Tic-Tac-Toe',self.amount,'loss',Decimal('0'))
            # disable all buttons
            for c in self.children:
                try: c.disabled=True
                except: pass
            return await interaction.response.edit_message(embed=emb('Tic-Tac-Toe',f'<@{winner}> won by strategy and receives **{money(payout)} points**!',GREEN),view=None)
        # Check for draw
        if all(self.board):
            self.finished=True
            # refund both
            await db.balance(self.players[0],self.amount); await db.balance(self.players[1],self.amount)
            # disable buttons
            for c in self.children:
                try: c.disabled=True
                except: pass
            return await interaction.response.edit_message(embed=emb('Tic-Tac-Toe',f'It is a draw. Both players were refunded **{money(self.amount)} points**.'),view=None)
        # Continue game
        self.turn=1-self.turn
        await interaction.response.edit_message(embed=self.board_embed(),view=self)
    async def on_timeout(self):
        if self.message and not self.finished:
            self.finished=True
            loser=self.players[self.turn]; winner=self.players[1-self.turn]; payout=(self.amount*2).quantize(Decimal('.01'))
            # pay winner and record
            await db.balance(winner,payout)
            await db.record(winner,'Tic-Tac-Toe',self.amount,'win',payout)
            await db.record(loser,'Tic-Tac-Toe',self.amount,'loss',Decimal('0'))
            # disable buttons by editing view to None
            await self.message.edit(embed=emb('Tic-Tac-Toe',f'<@{loser}> ran out of time. <@{winner}> wins **{money(payout)} points**.',GREEN),view=None)

# RPSView remains unchanged (uses refunds/payouts as before)

# ... rest of the file unchanged ...

async def main():
    if not TOKEN or not DB_URL: raise RuntimeError('Set DISCORD_TOKEN and DATABASE_URL in Railway Variables.')
    await db.connect()
    async with bot: await bot.start(TOKEN)
if __name__=='__main__': asyncio.run(main())
