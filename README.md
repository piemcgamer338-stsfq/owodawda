# LiteBet

Discord casino bot built with Python, PostgreSQL, image cards, buttons, and provably-fair game seeds. It is ready for Railway.

## Railway variables

Set these in Railway's **Variables** tab (do not commit them):

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `DATABASE_URL` | Yes | Railway PostgreSQL connection URL |
| `LTC_XPUB` | No | Watch-only Litecoin extended public key |
| `LTC_EXPLORER_URL` | No | Optional address API URL template, with `{address}` |
| `LOG_CHANNEL_ID` | No | Private staff channel for withdrawal requests |
| `OPENAI_API_KEY` | No | Optional future AI moderation integration |

Invite the bot with the **Administrator** permission as agreed. The command prefix is `.`.

## Start locally

```powershell
pip install -r requirements.txt
$env:DISCORD_TOKEN="..."
$env:DATABASE_URL="postgresql://..."
python bot.py
```

## Important wallet note

`deposit` derives a unique watch-only address from `LTC_XPUB`. A deposit is only credited after its transaction is seen by the configured explorer integration. `withdraw` creates a staff-only manual payout request in `LOG_CHANNEL_ID`; it never signs or broadcasts a transaction. Use a separate, secured wallet process for payments.
