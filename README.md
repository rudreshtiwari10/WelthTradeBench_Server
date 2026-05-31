# WelthTradeBench — Server

Python/FastAPI backend for the WelthTradeBench chart app. Proxies Upstox
historical candles (REST) and relays the live market feed over a local
WebSocket. **Falls back to realistic mock/replay data when no Upstox
credentials are configured**, so it runs without an account.

- **Stack:** Python 3.11 + FastAPI + uvicorn, `httpx` for REST, the official
  `upstox-python-sdk` (`MarketDataStreamerV3`) for the live feed.
- Pairs with the frontend ([WelthTradeBench_Client](https://github.com/rudreshtiwari10/WelthTradeBench_Client)).

## Prerequisites
- **Python 3.11+**

## Setup & run
```bash
# 1. create a virtual environment + install deps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. (optional) configure live Upstox data — otherwise mock data is used
cp .env.example .env          # then fill in your Upstox API key/secret

# 3. run the API (http://localhost:8000)
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```
On Windows, use `.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000`.

## Real-time data (Upstox) — optional
1. Create an app at https://account.upstox.com/developer/apps
2. Put the credentials in `.env` (see `.env.example`). The **redirect URI must
   exactly match** what you registered — the backend's callback is
   `http://localhost:8000/auth/callback`.
3. Start the frontend, then click **"Connect Upstox"** (top-right) to log in.

Notes:
- **Upstox access tokens expire daily** — re-login each day.
- Without credentials, everything runs on a built-in mock candle + tick
  generator.

## Endpoints
- `GET /api/health`, `GET /api/auth/status`
- `GET /api/search`, `GET /api/history`, `GET /api/quote`
- `GET /auth/login`, `GET /auth/callback`
- `WS  /ws` — live tick relay

## Security
- `.env` (your real credentials) and `.token.json` (the daily access token) are
  git-ignored and must **never** be committed. Only `.env.example` (blank
  template) is in the repo.

## Deployment on Render
To deploy this server on [Render](https://render.com/):

1. **Deploy with Blueprint:** Connect your GitHub repository to Render and use the provided `render.yaml` configuration, OR create a **Web Service** manually.
2. **Manual Configuration (if not using blueprint):**
   * **Runtime:** `Python`
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:$PORT`
3. **Environment Variables:** Define the following variables under **Environment** in the Render dashboard:
   * `PORT`: `8000` (or leave default, Gunicorn will automatically bind to whatever port Render assigns)
   * `CLIENT_URL`: The URL of your deployed frontend client (e.g., `https://welthchart.onrender.com`)
   * `UPSTOX_API_KEY`: Your Upstox App Key
   * `UPSTOX_API_SECRET`: Your Upstox App Secret
   * `UPSTOX_REDIRECT_URI`: Your production redirect URI (e.g., `https://welthchart-api.onrender.com/auth/callback`)
   * `UPSTOX_SANDBOX`: `true` or `false`
