import asyncio
import os
import aiohttp
from decimal import Decimal

# ============================================================
# LITECOIN WATCHER
# ============================================================

CHECK_INTERVAL = int(os.getenv("LTC_WATCH_INTERVAL", "30"))
REQUIRED_CONFIRMATIONS = int(os.getenv("LTC_CONFIRMATIONS", "6"))

# Blockchair Litecoin API
API_URL = "https://api.blockchair.com/litecoin/dashboards/address/{}"


async def check_address(session, db, user_id, address):
    try:
        url = API_URL.format(address)

        async with session.get(url, timeout=20) as response:

            if response.status != 200:
                print(
                    f"[LTC WATCHER] API error {response.status} "
                    f"for {address}"
                )
                return

            data = await response.json()

        address_data = data.get("data", {}).get(address)

        if not address_data:
            return

        transactions = address_data.get("transactions", [])

        if not transactions:
            return

        # ----------------------------------------------------
        # CHECK TRANSACTIONS
        # ----------------------------------------------------

        for txid in transactions:

            try:
                tx_url = (
                    f"https://api.blockchair.com/litecoin/"
                    f"dashboards/transaction/{txid}"
                )

                async with session.get(
                    tx_url,
                    timeout=20
                ) as tx_response:

                    if tx_response.status != 200:
                        continue

                    tx_data = await tx_response.json()

                tx_info = (
                    tx_data
                    .get("data", {})
                    .get(txid)
                )

                if not tx_info:
                    continue

                transaction = tx_info.get("transaction", {})

                # ------------------------------------------------
                # CONFIRMATIONS
                # ------------------------------------------------

                confirmations = int(
                    transaction.get("confirmations", 0) or 0
                )

                # ------------------------------------------------
                # FIND LTC SENT TO USER ADDRESS
                # ------------------------------------------------

                outputs = tx_info.get("outputs", [])

                received_ltc = Decimal("0")

                for output in outputs:

                    output_address = output.get("recipient")

                    if output_address != address:
                        continue

                    value = output.get("value", 0)

                    # Blockchair returns atomic LTC units.
                    received_ltc += (
                        Decimal(str(value)) /
                        Decimal("100000000")
                    )

                if received_ltc <= 0:
                    continue

                # ------------------------------------------------
                # SEND TO DATABASE
                # ------------------------------------------------

                result = await db.record_deposit(
                    user_id=user_id,
                    txid=txid,
                    address=address,
                    ltc_amount=received_ltc,
                    confirmations=confirmations
                )

                if result.get("credited"):

                    points = result["points"]

                    print(
                        f"[LTC WATCHER] CREDITED "
                        f"{received_ltc} LTC "
                        f"({points} points) "
                        f"to user {user_id}"
                    )

                elif result.get("inserted"):

                    print(
                        f"[LTC WATCHER] Found deposit "
                        f"{received_ltc} LTC "
                        f"for user {user_id} "
                        f"with {confirmations} confirmations"
                    )

            except Exception as e:

                print(
                    f"[LTC WATCHER] Transaction error "
                    f"{txid}: {e}"
                )

    except Exception as e:

        print(
            f"[LTC WATCHER] Address error "
            f"{address}: {e}"
        )


async def ltc_watcher(db):

    print("========================================")
    print("      LITECOIN DEPOSIT WATCHER")
    print("========================================")
    print(
        f"[LTC WATCHER] Checking every "
        f"{CHECK_INTERVAL} seconds"
    )
    print(
        f"[LTC WATCHER] Required confirmations: "
        f"{REQUIRED_CONFIRMATIONS}"
    )

    async with aiohttp.ClientSession() as session:

        while True:

            try:

                addresses = (
                    await db.list_watched_addresses()
                )

                if addresses:

                    print(
                        f"[LTC WATCHER] Watching "
                        f"{len(addresses)} addresses"
                    )

                    tasks = []

                    for row in addresses:

                        user_id = row["user_id"]
                        address = row["deposit_address"]

                        if not address:
                            continue

                        tasks.append(
                            check_address(
                                session,
                                db,
                                user_id,
                                address
                            )
                        )

                    if tasks:
                        await asyncio.gather(
                            *tasks,
                            return_exceptions=True
                        )

            except Exception as e:

                print(
                    f"[LTC WATCHER] Main loop error: {e}"
                )

            await asyncio.sleep(
                CHECK_INTERVAL
            )
