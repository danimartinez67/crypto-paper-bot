# Crypto Paper Trading Bot (automated demo)

- Assets: BTC, ETH, SOL
- Virtual starting balance: €800
- Strategy example: SMA 9/21 moving-average crossover
- Simulated stop-loss: 2%; take-profit: 4%
- Paper-only; no exchange connection and no real orders.

## Deploy
Upload `app.py`, `requirements.txt`, and `README.md` to the root of your GitHub repository. On Streamlit Community Cloud choose branch `main` and main file `app.py`.

## Important limitations
The five-minute refresh is session-bound and requires the app session to remain active; it is not a guaranteed 24/7 scheduler. Streamlit session state can reset. For robust continuous operation, use a separate worker and persistent database. CoinGecko's public OHLC endpoint may require a key or be rate-limited; the app displays errors and does not fabricate candle data.
