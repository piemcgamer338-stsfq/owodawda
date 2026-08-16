import asyncpg
from datetime import datetime, timezone
from decimal import Decimal
import os


# ============================================================
# DATABASE SCHEMA
# ============================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,

    balance NUMERIC(20,4) NOT NULL DEFAULT 0,
    wagered NUMERIC(20,4) NOT NULL DEFAULT 0,

    daily_wager NUMERIC(20,4) NOT NULL DEFAULT 0,
    weekly_wager NUMERIC(20,4) NOT NULL DEFAULT 0,
    monthly_wager NUMERIC(20,4) NOT NULL DEFAULT 0,

    rateback_loss NUMERIC(20,4) NOT NULL DEFAULT 0,

    games_played INT NOT NULL DEFAULT 0,
    games_won INT NOT NULL DEFAULT 0,

    bonuses NUMERIC(20,4) NOT NULL DEFAULT 0,

    tips_sent NUMERIC(20,4) NOT NULL DEFAULT 0,
    tips_received NUMERIC(20,4) NOT NULL DEFAULT 0,

    withdrawals NUMERIC(20,4) NOT NULL DEFAULT 0,

    privacy BOOLEAN NOT NULL DEFAULT FALSE,

    deposit_index INT NOT NULL DEFAULT 0,
    deposit_address TEXT,

    deposited_points NUMERIC(20,4) NOT NULL DEFAULT 0,

    last_daily TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS bets (
    id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,
    game TEXT NOT NULL,

    amount NUMERIC(20,4) NOT NULL,
    outcome TEXT NOT NULL,

    payout NUMERIC(20,4) NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS wallet_state (
    id INT PRIMARY KEY DEFAULT 1,
    next_deposit_index INT NOT NULL DEFAULT 0
);


INSERT INTO wallet_state (
    id,
    next_deposit_index
)
VALUES (
    1,
    0
)
ON CONFLICT (id) DO NOTHING;


CREATE TABLE IF NOT EXISTS withdrawals (
    id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    address TEXT NOT NULL,

    points NUMERIC(20,4) NOT NULL,
    ltc NUMERIC(20,8) NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS deposits (
    id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    txid TEXT NOT NULL UNIQUE,

    address TEXT NOT NULL,

    ltc NUMERIC(20,8) NOT NULL,

    points NUMERIC(20,4) NOT NULL,

    confirmations INT NOT NULL DEFAULT 0,

    status TEXT NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    credited_at TIMESTAMPTZ
);


-- ============================================================
-- MIGRATIONS
-- ============================================================

ALTER TABLE users
ADD COLUMN IF NOT EXISTS deposited_points
NUMERIC(20,4) NOT NULL DEFAULT 0;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS deposit_address
TEXT;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS deposit_index
INT NOT NULL DEFAULT 0;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS rateback_loss
NUMERIC(20,4) NOT NULL DEFAULT 0;


-- ============================================================
-- MAKE GLOBAL DEPOSIT INDEX SAFE
-- ============================================================

UPDATE wallet_state
SET next_deposit_index = GREATEST(
    next_deposit_index,
    COALESCE(
        (
            SELECT MAX(deposit_index) + 1
            FROM users
            WHERE deposit_address IS NOT NULL
        ),
        0
    )
)
WHERE id = 1;
"""


# ============================================================
# POINT CONVERSION
# ============================================================

# $0.0045 USD = 1 point
#
# Example:
#
# $0.10 / $0.0045 = 22.222...
# = 22.22 points
#
USD_PER_POINT = Decimal("0.0045")


# ============================================================
# DATABASE CLASS
# ============================================================

class Database:

    def __init__(self, url):
        self.url = url
        self.pool = None


    # ========================================================
    # CONNECT
    # ========================================================

    async def connect(self):

        self.pool = await asyncpg.create_pool(
            self.url,
            min_size=1,
            max_size=8
        )

        async with self.pool.acquire() as c:
            await c.execute(SCHEMA)


    # ========================================================
    # GET / CREATE USER
    # ========================================================

    async def user(self, uid):

        async with self.pool.acquire() as c:

            await c.execute(
                """
                INSERT INTO users(user_id)
                VALUES($1::BIGINT)
                ON CONFLICT DO NOTHING
                """,
                uid
            )

            return await c.fetchrow(
                """
                SELECT *
                FROM users
                WHERE user_id=$1::BIGINT
                """,
                uid
            )


    # ========================================================
    # BALANCE
    # ========================================================

    async def balance(self, uid, delta):

        async with self.pool.acquire() as c:

            await c.execute(
                """
                INSERT INTO users(user_id)
                VALUES($1::BIGINT)
                ON CONFLICT DO NOTHING
                """,
                uid
            )

            return await c.fetchval(
                """
                UPDATE users
                SET balance = balance + $2::NUMERIC
                WHERE user_id=$1::BIGINT
                RETURNING balance
                """,
                uid,
                delta
            )


    # ========================================================
    # TAKE BET
    # ========================================================

    async def take_bet(self, uid, amount):

        async with self.pool.acquire() as c:

            async with c.transaction():

                await c.execute(
                    """
                    INSERT INTO users(user_id)
                    VALUES($1::BIGINT)
                    ON CONFLICT DO NOTHING
                    """,
                    uid
                )

                result = await c.fetchval(
                    """
                    UPDATE users
                    SET
                        balance = balance - $2::NUMERIC,
                        wagered = wagered + $2::NUMERIC,
                        daily_wager = daily_wager + $2::NUMERIC,
                        weekly_wager = weekly_wager + $2::NUMERIC,
                        monthly_wager = monthly_wager + $2::NUMERIC,
                        games_played = games_played + 1
                    WHERE
                        user_id=$1::BIGINT
                        AND balance >= $2::NUMERIC
                    RETURNING balance
                    """,
                    uid,
                    amount
                )

                return result is not None


    # ========================================================
    # DEBIT
    # ========================================================

    async def debit(self, uid, amount):

        async with self.pool.acquire() as c:

            await c.execute(
                """
                INSERT INTO users(user_id)
                VALUES($1::BIGINT)
                ON CONFLICT DO NOTHING
                """,
                uid
            )

            result = await c.fetchval(
                """
                UPDATE users
                SET balance = balance - $2::NUMERIC
                WHERE
                    user_id=$1::BIGINT
                    AND balance >= $2::NUMERIC
                RETURNING balance
                """,
                uid,
                amount
            )

            return result is not None


    # ========================================================
    # CREDIT DEPOSIT MANUALLY
    # ========================================================

    async def credit_deposit(self, uid, amount):

        async with self.pool.acquire() as c:

            await c.execute(
                """
                INSERT INTO users(user_id)
                VALUES($1::BIGINT)
                ON CONFLICT DO NOTHING
                """,
                uid
            )

            return await c.fetchval(
                """
                UPDATE users
                SET
                    balance = balance + $2::NUMERIC,
                    deposited_points =
                        deposited_points + $2::NUMERIC
                WHERE user_id=$1::BIGINT
                RETURNING balance
                """,
                uid,
                amount
            )


    # ========================================================
    # GET NEXT GLOBAL DEPOSIT INDEX
    # ========================================================

    async def get_next_deposit_index(self):

        async with self.pool.acquire() as c:

            async with c.transaction():

                index = await c.fetchval(
                    """
                    SELECT next_deposit_index
                    FROM wallet_state
                    WHERE id=1
                    FOR UPDATE
                    """
                )

                if index is None:

                    await c.execute(
                        """
                        INSERT INTO wallet_state(
                            id,
                            next_deposit_index
                        )
                        VALUES(1, 0)
                        ON CONFLICT(id) DO NOTHING
                        """
                    )

                    index = await c.fetchval(
                        """
                        SELECT next_deposit_index
                        FROM wallet_state
                        WHERE id=1
                        FOR UPDATE
                        """
                    )

                await c.execute(
                    """
                    UPDATE wallet_state
                    SET next_deposit_index =
                        next_deposit_index + 1
                    WHERE id=1
                    """
                )

                return int(index)


    # ========================================================
    # SAVE DEPOSIT ADDRESS
    # ========================================================

    async def save_deposit_address(
        self,
        uid,
        index,
        address
    ):

        async with self.pool.acquire() as c:

            await c.execute(
                """
                UPDATE users
                SET
                    deposit_index=$2::INT,
                    deposit_address=$3::TEXT
                WHERE user_id=$1::BIGINT
                """,
                uid,
                index,
                address
            )


    # ========================================================
    # RECORD GAME
    # ========================================================

    async def record(
        self,
        uid,
        game,
        amount,
        outcome,
        payout
    ):

        async with self.pool.acquire() as c:

            await c.execute(
                """
                INSERT INTO bets(
                    user_id,
                    game,
                    amount,
                    outcome,
                    payout
                )
                VALUES(
                    $1::BIGINT,
                    $2::TEXT,
                    $3::NUMERIC,
                    $4::TEXT,
                    $5::NUMERIC
                )
                """,
                uid,
                game,
                amount,
                outcome,
                payout
            )

            if outcome == "loss":

                await c.execute(
                    """
                    UPDATE users
                    SET rateback_loss =
                        rateback_loss + $2::NUMERIC
                    WHERE user_id=$1::BIGINT
                    """,
                    uid,
                    amount
                )

            if payout:

                await c.execute(
                    """
                    UPDATE users
                    SET
                        balance = balance + $2::NUMERIC,
                        games_won = games_won + 1
                    WHERE user_id=$1::BIGINT
                    """,
                    uid,
                    payout
                )


    # ========================================================
    # CLAIM RATEBACK
    # ========================================================

    async def claim_rateback(self, uid):

        async with self.pool.acquire() as c:

            async with c.transaction():

                loss = await c.fetchval(
                    """
                    SELECT rateback_loss
                    FROM users
                    WHERE user_id=$1::BIGINT
                    FOR UPDATE
                    """,
                    uid
                )

                loss = Decimal(
                    str(loss or 0)
                )

                rb = (
                    loss * Decimal("0.03")
                ).quantize(
                    Decimal("0.01")
                )

                if rb <= 0:
                    return Decimal("0")

                await c.execute(
                    """
                    UPDATE users
                    SET
                        rateback_loss=0,
                        balance=balance+$2::NUMERIC
                    WHERE user_id=$1::BIGINT
                    """,
                    uid,
                    rb
                )

                return rb


    # ========================================================
    # FROZEN
    # ========================================================

    async def frozen(self):

        async with self.pool.acquire() as c:

            value = await c.fetchval(
                """
                SELECT value
                FROM settings
                WHERE key='frozen'
                """
            )

            return value == "1"


    # ========================================================
    # SET FROZEN
    # ========================================================

    async def set_frozen(self, yes):

        async with self.pool.acquire() as c:

            await c.execute(
                """
                INSERT INTO settings(
                    key,
                    value
                )
                VALUES(
                    'frozen',
                    $1::TEXT
                )
                ON CONFLICT(key)
                DO UPDATE SET value=$1::TEXT
                """,
                "1" if yes else "0"
            )


    # ========================================================
    # RECORD DEPOSIT
    # ========================================================

    async def record_deposit(
        self,
        user_id: int,
        txid: str,
        address: str,
        ltc_amount,
        confirmations: int = 0,
        ltc_usd_price=None
    ):

        required = int(
            os.getenv(
                "LTC_CONFIRMATIONS",
                "6"
            )
        )

        ltc = Decimal(
            str(ltc_amount)
        )

        if ltc <= 0:
            raise ValueError(
                "LTC amount must be greater than zero"
            )

        if ltc_usd_price is None:
            raise ValueError(
                "LTC/USD price is required"
            )

        ltc_usd_price = Decimal(
            str(ltc_usd_price)
        )

        if ltc_usd_price <= 0:
            raise ValueError(
                "LTC/USD price must be greater than zero"
            )

        # ====================================================
        # LTC -> USD
        # ====================================================

        usd_value = (
            ltc * ltc_usd_price
        )

        # ====================================================
        # USD -> POINTS
        #
        # $0.0045 = 1 point
        #
        # Example:
        #
        # 0.10 USD / 0.0045
        # = 22.222...
        # = 22.22 points
        #
        # ====================================================

        points = (
            usd_value / USD_PER_POINT
        ).quantize(
            Decimal("0.01")
        )

        async with self.pool.acquire() as c:

            async with c.transaction():

                # =================================================
                # CHECK EXISTING TRANSACTION
                # =================================================

                existing = await c.fetchrow(
                    """
                    SELECT
                        status,
                        confirmations,
                        points,
                        ltc
                    FROM deposits
                    WHERE txid=$1::TEXT
                    FOR UPDATE
                    """,
                    txid
                )

                # =================================================
                # EXISTING DEPOSIT
                # =================================================

                if existing:

                    # ---------------------------------------------
                    # Already credited
                    # ---------------------------------------------

                    if existing["status"] == "confirmed":

                        return {
                            "inserted": False,
                            "credited": False,
                            "already_credited": True,
                            "points": existing["points"]
                        }

                    # ---------------------------------------------
                    # Keep the original points for a pending
                    # deposit.
                    #
                    # This prevents the user's points changing
                    # because LTC/USD moved while waiting for
                    # confirmations.
                    # ---------------------------------------------

                    stored_points = existing["points"]

                    # ---------------------------------------------
                    # Update confirmations only
                    # ---------------------------------------------

                    await c.execute(
                        """
                        UPDATE deposits
                        SET confirmations=$2::INT
                        WHERE txid=$1::TEXT
                        """,
                        txid,
                        confirmations
                    )

                    # ---------------------------------------------
                    # Still pending
                    # ---------------------------------------------

                    if confirmations < required:

                        return {
                            "inserted": False,
                            "credited": False,
                            "already_credited": False,
                            "points": stored_points
                        }

                    # ---------------------------------------------
                    # CONFIRM EXISTING DEPOSIT
                    # ---------------------------------------------

                    updated = await c.fetchrow(
                        """
                        UPDATE deposits
                        SET
                            confirmations=$2::INT,
                            status='confirmed',
                            credited_at=$3::TIMESTAMPTZ
                        WHERE
                            txid=$1::TEXT
                            AND status!='confirmed'
                        RETURNING points
                        """,
                        txid,
