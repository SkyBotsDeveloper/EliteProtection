# EliteXprotectorBot

EliteXprotectorBot ek Telegram bot hai jo subscribed groups me bot-generated content ko auto-delete karta hai. Project `Python + aiogram 3.x + MongoDB (motor)` par based hai.

## Core Highlights
- DM onboarding + payment/subscription flow (unchanged)
- Admin review + owner approval flow (unchanged)
- Protected groups ke liye high-scale auto-delete engine
- Protected group me sab stickers bhi 35 second baad auto-delete
- Polling + Webhook dono run modes
- Heroku one-click deploy via `app.json`
- Windows local testing friendly setup

## High-Scale Auto-Delete Engine (Naya Upgrade)
Protected groups me bot-generated content ke liye optimized engine use hota hai:
- Primary detect: `message.from_user.is_bot == True`
- Additional detect: `message.via_bot` aur forwarded bot-origin cases jahan detect possible ho
- Content type neutral: text, photo, video, sticker, gif/animation, document, voice, audio, video note, caption messages, media group items, wagaira (jo Telegram delete API allow kare)
- Sticker special rule: protected group me human ya bot kisi ka bhi sticker ho, ~35s baad auto-delete hoga
- Target delete delay: `35s` (env configurable)
- Time-bucket/ring-buffer scheduler (`~200ms` tick)
- Per-chat aggregation + chunk delete (`100` messages/chunk)
- 1000+ bot messages burst handle karne ke liye chunked batch delete
- Duplicate scheduling prevention (`chat_id + message_id` unique in-memory)
- Floodwait/rate-limit aware retries + exponential backoff
- Mongo per-message read avoid kiya gaya (in-memory protected-group cache)
- Optional crash-safe pending-delete persistence with TTL (env flag)
- Runtime metrics logs:
  - scheduled count
  - deleted count
  - failed count
  - average delay drift

## Project Structure
```text
bot/
  config/
  db/
  handlers/
  keyboards/
  services/
    auto_delete_engine.py
    delete_worker.py
    group_cache.py
  utils/
  webhook_app.py
main.py
```

## Windows Local Setup (Recommended First Test)
Ye steps Windows PowerShell ke liye hain.

1. Python `3.11+` verify karo:
   ```powershell
   python --version
   ```
2. Virtual environment banao:
   ```powershell
   python -m venv .venv
   ```
3. Venv activate karo:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
4. Dependencies install karo:
   ```powershell
   pip install -r requirements.txt
   ```
5. Env file create karo:
   ```powershell
   Copy-Item .env.example .env
   ```
6. `.env` me minimum values set karo:
   - `BOT_TOKEN`
   - `MONGO_URI`
   - `MONGO_DB_NAME`
7. Local test ke liye polling mode ensure karo:
   ```env
   BOT_RUN_MODE=polling
   WEBHOOK_MODE=false
   ```
8. Bot run karo:
   ```powershell
   python main.py
   ```

## Linux/macOS Quick Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## VPS Deploy (systemd, webhook or polling)
1. Server par code `/opt/elitexprotector` me rakho aur `.env` configure karo.
2. Dependencies install karo:
   ```bash
   cd /opt/elitexprotector
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. `systemd` unit install karo:
   ```bash
   sudo cp systemd/elitexprotector.service /etc/systemd/system/elitexprotector.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now elitexprotector
   ```
4. Service verify karo:
   ```bash
   sudo systemctl status elitexprotector
   sudo journalctl -u elitexprotector -f
   ```

## Run Modes
- Polling mode:
  - `BOT_RUN_MODE=polling`
  - `WEBHOOK_MODE=false`
  - Local/VPS quick test ke liye best
- Webhook mode:
  - `WEBHOOK_MODE=true` (ye `BOT_RUN_MODE` se upar priority leta hai)
  - `WEBHOOK_BASE_URL` required

## Heroku One-Click Deploy (`app.json`)
Deploy button:
```text
https://www.heroku.com/deploy?template=https://github.com/SkyBotsDeveloper/EliteProtection
```

`app.json` me required env prompts milenge. `WEBHOOK_BASE_URL` ke liye `https://<your-app-name>.herokuapp.com` value do.

## Heroku Manual Webhook Deploy
1. App create:
   ```bash
   heroku login
   heroku create your-app-name
   ```
2. Config vars set karo:
   ```bash
   heroku config:set BOT_TOKEN="<your_bot_token>"
   heroku config:set MONGO_URI="<your_mongo_uri>"
   heroku config:set MONGO_DB_NAME="elitex_protector"
   heroku config:set LOG_LEVEL="INFO"
   heroku config:set OWNER_USER_ID="8088623806"
   heroku config:set OWNER_USERNAME="EliteSid"
   heroku config:set ADMIN_REVIEW_CHAT_ID="-1003761739308"
   heroku config:set PAYMENT_QR_IMAGE_URL="https://files.catbox.moe/0svb7x.jpg"
   heroku config:set BOT_MESSAGE_DELETE_DELAY_SECONDS="35"
   heroku config:set PROTECTED_GROUP_CACHE_REFRESH_SECONDS="20"
   heroku config:set AUTO_DELETE_TICK_INTERVAL_MS="200"
   heroku config:set AUTO_DELETE_CHUNK_SIZE="100"
   heroku config:set AUTO_DELETE_RETRY_ATTEMPTS="5"
   heroku config:set AUTO_DELETE_RETRY_BASE_SECONDS="1.5"
   heroku config:set AUTO_DELETE_RETRY_MAX_SECONDS="35"
   heroku config:set AUTO_DELETE_WORKER_CONCURRENCY="12"
   heroku config:set AUTO_DELETE_METRICS_LOG_INTERVAL_SECONDS="60"
   heroku config:set AUTO_DELETE_PERSISTENCE_ENABLED="false"
   heroku config:set WEBHOOK_MODE="true"
   heroku config:set BOT_RUN_MODE="webhook"
   heroku config:set WEBHOOK_BASE_URL="https://your-app-name.herokuapp.com"
   heroku config:set WEBHOOK_PATH="/webhook/telegram"
   heroku config:set WEBHOOK_SECRET_TOKEN="<long_random_secret>"
   ```
3. Deploy:
   ```bash
   git add .
   git commit -m "deploy: heroku webhook"
   git push heroku main
   ```
4. Verify:
   ```bash
   heroku ps
   heroku logs --tail
   ```

## Commands

### DM User Commands
- `/start`
- `/madad`
- `/meri_subscription`
- `/cancel`

### Group Commands
- `/check`
- `/status`

### Owner-only Commands (Private)
- `/pending`
- `/approve <payment_id>`
- `/deny <payment_id>`
- `/revoke <group_id>`
- `/stats`

## Payment + Subscription Flow (Unchanged)
1. User `/start` karta hai
2. `Subscription Kharido` button click
3. QR payment (`₹100`) + `Done ✅` / `Cancel ❌`
4. `Done` par pending payment request create hota hai
5. Admin review channel me request aata hai
6. Owner approve/deny karta hai
7. Approve ke baad user group chat ID bhejta hai
8. One approved subscription = one group binding

## Group Setup Requirements
Group me bot ke liye:
1. Admin role dena zaroori hai
2. `Delete messages` permission deni zaroori hai
3. Group subscribed/protected hona zaroori hai
4. Protected group me sab stickers bhi 35 second baad auto-delete honge

## Privacy Mode Note
- Privacy mode on ho to bot ko limited visibility mil sakti hai.
- Better detection ke liye privacy mode off recommended hai.

## Important Env Vars (Auto-Delete)
- `BOT_MESSAGE_DELETE_DELAY_SECONDS` (default `35`)
- `AUTO_DELETE_TICK_INTERVAL_MS` (default `200`)
- `AUTO_DELETE_CHUNK_SIZE` (default `100`, max `100`)
- `AUTO_DELETE_RETRY_ATTEMPTS`
- `AUTO_DELETE_RETRY_BASE_SECONDS`
- `AUTO_DELETE_RETRY_MAX_SECONDS`
- `AUTO_DELETE_WORKER_CONCURRENCY` (default `12`)
- `PROTECTED_GROUP_CACHE_REFRESH_SECONDS` (default `20`)
- `AUTO_DELETE_PERSISTENCE_ENABLED` (`false` by default)
- `AUTO_DELETE_PERSISTENCE_TTL_HOURS`
- `AUTO_DELETE_RESTORE_LIMIT`

## Troubleshooting
- Bot start nahi ho raha:
  - `.env` me `BOT_TOKEN`, `MONGO_URI`, `MONGO_DB_NAME` check karo.
  - Syntax verify karo:
    ```bash
    python -m compileall main.py bot
    ```
- Auto-delete nahi chal raha:
  - Group subscribed hai ya nahi `/status` se check karo.
  - Bot admin aur delete permission verify karo.
  - Logs me permission/floodwait errors check karo.
- Webhook issue:
  - `WEBHOOK_MODE=true` + valid public `WEBHOOK_BASE_URL` confirm karo.
- Approve/deny DM fail:
  - User ne bot block kiya ho sakta hai, logs check karo.

## Acceptance Test Checklist
- [ ] Windows local setup se bot polling mode me run hota hai.
- [ ] Protected group cache startup ke baad refresh logs aate hain.
- [ ] Subscribed group me bot ka text message ~35s me delete hota hai.
- [ ] Photo/video/sticker/document/voice/audio/animation/media-group messages bhi ~35s me delete hote hain (Telegram limits ke andar).
- [ ] Human users ke stickers bhi protected group me ~35s baad auto-delete hote hain.
- [ ] `via_bot` case me message schedule hota hai.
- [ ] 1000+ bot messages burst me scheduler crash nahi hota.
- [ ] Batch delete chunking (100 per call) logs/behavior se verify hoti hai.
- [ ] Duplicate schedule attempt par same message double delete queue me nahi aata.
- [ ] Floodwait/temporary API errors par retries lagte hain, group me spam nahi hota.
- [ ] Non-subscribed group me auto-delete trigger nahi hota.
- [ ] Payment/subscription flow pe koi regression nahi aati.

## Security Notes
- `.env` kabhi git me commit mat karo.
- `WEBHOOK_SECRET_TOKEN` strong random rakho.
- Owner-only commands ko public group me use mat karo.

