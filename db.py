import asyncpg
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY, balance NUMERIC(20,4) NOT NULL DEFAULT 0,
  wagered NUMERIC(20,4) NOT NULL DEFAULT 0, daily_wager NUMERIC(20,4) NOT NULL DEFAULT 0,
  weekly_wager NUMERIC(20,4) NOT NULL DEFAULT 0, monthly_wager NUMERIC(20,4) NOT NULL DEFAULT 0,
  games_played INT NOT NULL DEFAULT 0, games_won INT NOT NULL DEFAULT 0,
  bonuses NUMERIC(20,4) NOT NULL DEFAULT 0, tips_sent NUMERIC(20,4) NOT NULL DEFAULT 0,
  tips_received NUMERIC(20,4) NOT NULL DEFAULT 0, withdrawals NUMERIC(20,4) NOT NULL DEFAULT 0,
  privacy BOOLEAN NOT NULL DEFAULT FALSE, deposit_index INT NOT NULL DEFAULT 0,
  last_daily TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS bets (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, game TEXT NOT NULL, amount NUMERIC(20,4) NOT NULL,
  outcome TEXT NOT NULL, payout NUMERIC(20,4) NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS withdrawals (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, address TEXT NOT NULL, points NUMERIC(20,4) NOT NULL,
  ltc NUMERIC(20,8) NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS deposited_points NUMERIC(20,4) NOT NULL DEFAULT 0;
"""

class Database:
    def __init__(self, url): self.url, self.pool = url, None
    async def connect(self):
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=8)
        async with self.pool.acquire() as c: await c.execute(SCHEMA)
    async def user(self, uid):
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
            return await c.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
    async def balance(self, uid, delta):
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
            return await c.fetchval("UPDATE users SET balance=balance+$2 WHERE user_id=$1 RETURNING balance", uid, delta)
    async def take_bet(self, uid, amount):
        async with self.pool.acquire() as c:
            async with c.transaction():
                await c.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
                ok = await c.fetchval("UPDATE users SET balance=balance-$2, wagered=wagered+$2, daily_wager=daily_wager+$2, weekly_wager=weekly_wager+$2, monthly_wager=monthly_wager+$2, games_played=games_played+1 WHERE user_id=$1 AND balance >= $2 RETURNING balance", uid, amount)
                return ok is not None
    async def debit(self, uid, amount):
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
            return await c.fetchval("UPDATE users SET balance=balance-$2 WHERE user_id=$1 AND balance >= $2 RETURNING balance", uid, amount) is not None
    async def credit_deposit(self, uid, amount):
        """Call only after a confirmed on-chain deposit has been credited."""
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
            return await c.fetchval("UPDATE users SET balance=balance+$2, deposited_points=deposited_points+$2 WHERE user_id=$1 RETURNING balance", uid, amount)
    async def record(self, uid, game, amount, outcome, payout):
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO bets(user_id,game,amount,outcome,payout) VALUES($1,$2,$3,$4,$5)",uid,game,amount,outcome,payout)
            if payout: await c.execute("UPDATE users SET balance=balance+$2, games_won=games_won+1 WHERE user_id=$1",uid,payout)
    async def frozen(self):
        async with self.pool.acquire() as c:
            return (await c.fetchval("SELECT value FROM settings WHERE key='frozen'")) == '1'
    async def set_frozen(self, yes):
        async with self.pool.acquire() as c: await c.execute("INSERT INTO settings(key,value) VALUES('frozen',$1) ON CONFLICT(key) DO UPDATE SET value=$1", '1' if yes else '0')
