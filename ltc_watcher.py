import asyncio
import os
import aiohttp
from decimal import Decimal


# ============================================================
# LITECOIN WATCHER
# ============================================================

CHECK_INTERVAL = int(
    os.getenv("LTC_WATCH_INTERVAL", "30")
)

REQUIRED_CONFIRMATIONS = int(
    os.getenv("LTC_CONFIRMATIONS", "6")
)

BASE_URL = "https://litecoinspace.org/api"

# CoinGecko LTC/USD price API
PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=litecoin&vs_currencies=usd"
)


# ============================================================
# GET CURRENT LTC/USD PRICE
# ============================================================

async def get_ltc_usd_price(session):

    try:

        async with session.get(
            PRICE_URL,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as response:

            if response.status != 200:

                print(
                    "[LTC WATCHER] "
                    f"LTC price API error: {response.status}"
                )

                return None

            data = await response.json()

            price = (
                data
                .get("litecoin", {})
                .get("usd")
            )

            if price is None:

                print(
                    "[LTC WATCHER] "
                    "LTC/USD price missing"
                )

                return None

            price = Decimal(str(price))

            if price <= 0:

                print(
                    "[LTC WATCHER] "
                    f"Invalid LTC/USD price: {price}"
                )

                return None

            return price

    except Exception as e:

        print(
            "[LTC WATCHER] "
            f"LTC price error: {e}"
        )

        return None


# ============================================================
# GET CURRENT BLOCK HEIGHT
# ============================================================

async def get_tip_height(session):

    url = f"{BASE_URL}/blocks/tip/height"

    try:

        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as response:

            if response.status != 200:

                print(
                    "[LTC WATCHER] "
                    f"Tip API error: {response.status}"
                )

                return None

            text = await response.text()

            return int(text.strip())

    except Exception as e:

        print(
            "[LTC WATCHER] "
            f"Tip height error: {e}"
        )

        return None


# ============================================================
# GET ADDRESS TRANSACTIONS
# ============================================================

async def get_address_transactions(
    session,
    address
):

    url = (
        f"{BASE_URL}/address/"
        f"{address}/txs"
    )

    try:

        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as response:

            if response.status != 200:

                print(
                    "[LTC WATCHER] "
                    f"Address API error "
                    f"{response.status} "
                    f"for {address}"
                )

                return []

            data = await response.json()

            if not isinstance(data, list):

                return []

            return data

    except Exception as e:

        print(
            "[LTC WATCHER] "
            f"Address request error "
            f"{address}: {e}"
        )

        return []


# ============================================================
# CALCULATE LTC RECEIVED BY ADDRESS
# ============================================================

def get_received_ltc(
    transaction,
    address
):

    received = Decimal("0")

    for output in transaction.get(
        "vout",
        []
    ):

        output_address = (
            output.get(
                "scriptpubkey_address"
            )
        )

        if output_address != address:

            continue

        value = output.get(
            "value",
            0
        )

        # Litecoin Space returns litoshis.
        received += (
            Decimal(str(value))
            / Decimal("100000000")
        )

    return received


# ============================================================
# CALCULATE CONFIRMATIONS
# ============================================================

def get_confirmations(
    transaction,
    tip_height
):

    status = transaction.get(
        "status",
        {}
    )

    if not status.get("confirmed"):

        return 0

    block_height = status.get(
        "block_height"
    )

    if not block_height:

        return 0

    confirmations = (
        tip_height
        - int(block_height)
        + 1
    )

    return max(
        0,
        confirmations
    )


# ============================================================
# CHECK ONE USER ADDRESS
# ============================================================

async def check_address(
    session,
    db,
    user_id,
    address,
    tip_height,
    ltc_usd_price
):

    transactions = await get_address_transactions(
        session,
        address
    )

    if not transactions:

        return

    for transaction in transactions:

        try:

            txid = transaction.get(
                "txid"
            )

            if not txid:

                continue

            # -----------------------------------------------
            # CONFIRMATIONS
            # -----------------------------------------------

            confirmations = get_confirmations(
                transaction,
                tip_height
            )

            # -----------------------------------------------
            # LTC RECEIVED
            # -----------------------------------------------

            received_ltc = get_received_ltc(
                transaction,
                address
            )

            if received_ltc <= 0:

                continue

            # -----------------------------------------------
            # RECORD / CREDIT DEPOSIT
            # -----------------------------------------------

            result = await db.record_deposit(
                user_id=user_id,
                txid=txid,
                address=address,
                ltc_amount=received_ltc,
                confirmations=confirmations,
                ltc_usd_price=ltc_usd_price
            )

            # -----------------------------------------------
            # NEW DEPOSIT
            # -----------------------------------------------

            if result.get("inserted"):

                points = Decimal(
                    str(
                        result.get(
                            "points",
                            "0"
                        )
                    )
                )

                usd_value = (
                    received_ltc
                    * ltc_usd_price
                ).quantize(
                    Decimal("0.01")
                )

                print(
                    "[LTC WATCHER] "
                    f"Deposit detected | "
                    f"user={user_id} | "
                    f"txid={txid} | "
                    f"amount={received_ltc} LTC | "
                    f"LTC price=${ltc_usd_price} | "
                    f"value=${usd_value} | "
                    f"points={points} | "
                    f"confirmations={confirmations}"
                )

            # -----------------------------------------------
            # DEPOSIT CREDITED
            # -----------------------------------------------

            if result.get("credited"):

                points = Decimal(
                    str(
                        result["points"]
                    )
                )

                usd_value = (
                    received_ltc
                    * ltc_usd_price
                ).quantize(
                    Decimal("0.01")
                )

                print(
                    "[LTC WATCHER] "
                    f"DEPOSIT CREDITED | "
                    f"user={user_id} | "
                    f"txid={txid} | "
                    f"amount={received_ltc} LTC | "
                    f"LTC price=${ltc_usd_price} | "
                    f"value=${usd_value} | "
                    f"points={points} | "
                    f"confirmations={confirmations}"
                )

            # -----------------------------------------------
            # ALREADY CREDITED
            # -----------------------------------------------

            if result.get(
                "already_credited"
            ):

                pass

        except Exception as e:

            print(
                "[LTC WATCHER] "
                f"Transaction error "
                f"for user {user_id}: {e}"
            )


# ============================================================
# MAIN WATCHER
# ============================================================

async def ltc_watcher(db):

    print(
        "========================================"
    )

    print(
        "       LITECOIN DEPOSIT WATCHER"
    )

    print(
        "========================================"
    )

    print(
        "[LTC WATCHER] "
        f"Check interval: "
        f"{CHECK_INTERVAL} seconds"
    )

    print(
        "[LTC WATCHER] "
        f"Required confirmations: "
        f"{REQUIRED_CONFIRMATIONS}"
    )

    print(
        "[LTC WATCHER] "
        "Point rate: $0.0045 = 1 point"
    )

    async with aiohttp.ClientSession() as session:

        while True:

            try:

                # -------------------------------------------
                # GET CURRENT BLOCK HEIGHT
                # -------------------------------------------

                tip_height = await get_tip_height(
                    session
                )

                if tip_height is None:

                    await asyncio.sleep(
                        CHECK_INTERVAL
                    )

                    continue

                # -------------------------------------------
                # GET CURRENT LTC/USD PRICE
                # -------------------------------------------

                ltc_usd_price = (
                    await get_ltc_usd_price(
                        session
                    )
                )

                if ltc_usd_price is None:

                    print(
                        "[LTC WATCHER] "
                        "Could not get LTC/USD price. "
                        "Skipping cycle."
                    )

                    await asyncio.sleep(
                        CHECK_INTERVAL
                    )

                    continue

                print(
                    "[LTC WATCHER] "
                    f"Current LTC price: "
                    f"${ltc_usd_price}"
                )

                # -------------------------------------------
                # GET ALL USER ADDRESSES
                # -------------------------------------------

                addresses = (
                    await db.list_watched_addresses()
                )

                if not addresses:

                    await asyncio.sleep(
                        CHECK_INTERVAL
                    )

                    continue

                print(
                    "[LTC WATCHER] "
                    f"Watching {len(addresses)} "
                    f"deposit address(es)"
                )

                # -------------------------------------------
                # CHECK ADDRESSES
                # -------------------------------------------

                tasks = []

                for row in addresses:

                    user_id = row[
                        "user_id"
                    ]

                    address = row[
                        "deposit_address"
                    ]

                    if not address:

                        continue

                    tasks.append(
                        check_address(
                            session,
                            db,
                            user_id,
                            address,
                            tip_height,
                            ltc_usd_price
                        )
                    )

                if tasks:

                    await asyncio.gather(
                        *tasks,
                        return_exceptions=True
                    )

            except asyncio.CancelledError:

                print(
                    "[LTC WATCHER] "
                    "Watcher stopped."
                )

                raise

            except Exception as e:

                print(
                    "[LTC WATCHER] "
                    f"Main loop error: {e}"
                )

            # -------------------------------------------
            # WAIT BEFORE NEXT CHECK
            # -------------------------------------------

            await asyncio.sleep(
                CHECK_INTERVAL
            )
