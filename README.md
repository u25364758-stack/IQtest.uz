# IQ Test Telegram bot

Python 3.13, aiogram 3, aiohttp and SQLite based Telegram bot with a Telegram Mini App. The bot polls Telegram for updates while aiohttp serves the Mini App and its API from the same process and public HTTPS origin.

## Local development

1. Install Python 3.13.
2. Create a Telegram bot with BotFather and copy its token.
3. Create `.env` next to `main.py` (never commit it):

   ```dotenv
   BOT_TOKEN=your_bot_token
   WEBAPP_URL=https://your-public-render-domain.onrender.com
   ```

4. Install and run:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python main.py
   ```

The application listens on `0.0.0.0` and the `PORT` supplied by its environment (8080 locally by default). `WEBAPP_URL` must be a public HTTPS URL; `http://localhost` and HTTPS localhost are rejected. Telegram's Mini App needs HTTPS, so local UI testing should use the deployed URL or a separate HTTPS development host. Do not put a bot token in frontend files or logs.

## Render deployment (native Python service)

1. Push this project to a private GitHub repository. Ensure `.env` and `iqbot.db` are not committed; `.gitignore` excludes them.
2. In Render, choose **New + → Web Service**, connect the repository, and choose the Python runtime. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
3. Add environment variables in the service's **Environment** page:
   - `BOT_TOKEN` = token from BotFather (secret)
   - `WEBAPP_URL` = `https://<service-name>.onrender.com`
   - `DATABASE_PATH` = `/var/data/iqbot.db` (after attaching a persistent disk at `/var/data`)
4. Attach a Render persistent disk, mount path `/var/data`, with enough capacity for the SQLite database and backups. Deploy. The service should answer `https://<service-name>.onrender.com/health` with `{"status":"ok"}` and `/` with the Mini App.

Render's ephemeral filesystem can be discarded when an instance is replaced or redeployed. SQLite must be placed on a persistent disk to preserve profiles, attempts and each user's question history. Persistent disks are available on eligible paid Render services; check the plan and disk pricing in your Render dashboard. A single SQLite file on an attached disk is suitable for this small, single-instance bot. For multiple instances or higher write concurrency, migrate the database layer in `database.py` to managed PostgreSQL and use a connection pool; do not run several app instances against local SQLite.

If you skip the persistent disk, the app can start with its default `iqbot.db` path, but data may be lost and question history/cycle may reset after a deploy or instance replacement.

## Domain and Telegram Mini App

The `onrender.com` service URL is the initial public domain and already uses HTTPS. Set `WEBAPP_URL` to that exact HTTPS origin, for example `https://iqtest-uz.onrender.com` (no path is needed). If you add a custom domain in Render, configure the DNS records Render displays, wait for its TLS certificate/HTTPS status to become active, then change `WEBAPP_URL` to `https://your-domain.example` and redeploy. The Render domain may continue to work, but the bot button and BotFather settings should point at the chosen canonical domain.

In BotFather, configure the Mini App URL in the Mini App / Main App settings if you want a profile or menu launch button, and use the same `WEBAPP_URL`. This project also sends the URL directly in the `/start` inline keyboard via `WebAppInfo(url=WEBAPP_URL)`, so that button works without a separate BotFather menu configuration. In BotFather, set the bot's commands/description as desired; never paste `BOT_TOKEN` into a URL or frontend.

## App behavior and security

- `GET /health` is a platform health check; `GET /` serves `webapp/index.html`. The three API routes are `POST /api/profile`, `/api/questions`, and `/api/finish`.
- The server validates signed Telegram `initData` and takes the Telegram user ID only from that signed data. It ignores any client-provided Telegram ID. Correct answers remain on the server; the submitted answers are checked against server data.
- Attempts are bound to their owner and can only be completed once. Elapsed time is measured by the server. Difficulty-weighted correct answers and server duration contribute to a score clamped to 70–140; gender does not affect it.
- `questions/iq.json` contains 1,000 unique questions with difficulty. Each attempt reserves 25 random, unused questions per user. Because each cycle contains exactly 40 batches of 25, the next cycle starts only when all 1,000 have been used. Do not remove/renumber questions in a live deployment without considering existing attempt history.
- The displayed score is an entertainment estimate, not a clinical or professional IQ diagnosis. EQ/PQ/Overall scoring and payment processing are not implemented in this project; there is no payment gate to bypass. Do not expose paid results until a server-side payment verification flow is implemented.
- No broad CORS policy is enabled: the frontend and API are served from the same origin.

## Files and environment

`BOT_TOKEN`, `WEBAPP_URL`, `PORT`, `HOST`, `DATABASE_PATH`, and `INIT_DATA_MAX_AGE` can be supplied as process environment variables. `.env` is loaded for local development only and is git-ignored. Render uses service environment variables. Do not store real credentials in source code, README, or frontend assets.
