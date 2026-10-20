import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Crypto Paper Trading Bot", page_icon="📈", layout="wide")

API_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINS = {
    "BTC": {"id": "bitcoin", "name": "Bitcoin"},
    "ETH": {"id": "ethereum", "name": "Ethereum"},
    "SOL": {"id": "solana", "name": "Solana"},
}
FEE_RATE = 0.001
STARTING_CASH = 800.0
CHECK_EVERY_SECONDS = 300
FAST_SMA = 9
SLOW_SMA = 21
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04
MAX_ALLOCATION_PCT = 10
MAX_OPEN_POSITIONS = 3

st.title("📈 Crypto Paper Trading Bot")
st.caption("BTC · ETH · SOL | Simulación con dinero virtual | Sin órdenes reales ni conexión a exchanges")

# Streamlit session state is convenient for interactive demo use, but is not durable across restarts.
defaults = {
    "cash": STARTING_CASH,
    "starting_cash": STARTING_CASH,
    "positions": {},
    "trades": [],
    "prices": {},
    "market_df": None,
    "last_update": None,
    "auto_enabled": False,
    "last_signal_time": {},
    "last_error": None,
    "last_cycle": None,
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

def get_prices():
    params = {
        "ids": ",".join(item["id"] for item in COINS.values()),
        "vs_currencies": "eur",
        "include_24hr_change": "true",
    }
    response = requests.get(PRICE_URL, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    rows = []
    prices = {}
    for ticker, coin in COINS.items():
        item = payload.get(coin["id"], {})
        if not isinstance(item.get("eur"), (float, int)):
            raise ValueError(f"No hay un precio EUR válido para {ticker}.")
        prices[ticker] = float(item["eur"])
        change = item.get("eur_24h_change")
        rows.append({
            "Símbolo": ticker,
            "Criptomoneda": coin["name"],
            "Precio (€)": prices[ticker],
            "Cambio 24 h (%)": float(change) if isinstance(change, (float, int)) else None,
        })
    return prices, pd.DataFrame(rows)

def get_ohlc(ticker):
    coin_id = COINS[ticker]["id"]
    # CoinGecko's public OHLC endpoint generally requires an API key on many plans.
    # Treat any API denial/rate limit as unavailable data; never fabricate candles.
    response = requests.get(API_URL.format(coin_id=coin_id), params={"vs_currency": "eur", "days": "1"}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < SLOW_SMA + 2:
        raise ValueError(f"Histórico OHLC insuficiente para {ticker}.")
    df = pd.DataFrame(payload, columns=["timestamp", "open", "high", "low", "close"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"])
    if len(df) < SLOW_SMA + 2:
        raise ValueError(f"Histórico de cierres insuficiente para {ticker}.")
    return df

def trend_signal(ticker):
    candles = get_ohlc(ticker)
    candles["fast"] = candles["close"].rolling(FAST_SMA).mean()
    candles["slow"] = candles["close"].rolling(SLOW_SMA).mean()
    prev = candles.iloc[-2]
    latest = candles.iloc[-1]
    # Only act on a confirmed moving-average crossover, not just a price direction.
    if pd.isna(prev["fast"]) or pd.isna(prev["slow"]) or pd.isna(latest["fast"]) or pd.isna(latest["slow"]):
        return "WAIT", "Medias móviles insuficientes"
    if prev["fast"] <= prev["slow"] and latest["fast"] > latest["slow"]:
        return "LONG", "Cruce alcista SMA 9/21"
    if prev["fast"] >= prev["slow"] and latest["fast"] < latest["slow"]:
        return "SHORT", "Cruce bajista SMA 9/21"
    return "WAIT", "Sin cruce confirmado"

def unrealized(position, price):
    direction = 1 if position["side"] == "LONG" else -1
    return direction * position["qty"] * (price - position["entry"])

def log_trade(ticker, action, price, qty, pnl=0.0, fee=0.0, note=""):
    st.session_state.trades.append({
        "Fecha": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "Activo": ticker, "Acción": action, "Precio (€)": price,
        "Cantidad": qty, "P&L neto (€)": pnl, "Comisión (€)": fee, "Motivo": note,
    })

def open_position(ticker, side, note="Señal manual"):
    if ticker in st.session_state.positions:
        return
    if len(st.session_state.positions) >= MAX_OPEN_POSITIONS:
        return
    price = st.session_state.prices.get(ticker)
    if not price or price <= 0:
        return
    allocation = st.session_state.cash * MAX_ALLOCATION_PCT / 100
    fee = allocation * FEE_RATE
    if allocation + fee > st.session_state.cash or allocation <= 0:
        return
    qty = allocation / price
    st.session_state.cash -= fee
    st.session_state.positions[ticker] = {
        "side": side, "entry": price, "qty": qty, "notional": allocation,
        "entry_fee": fee, "opened": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "stop": price * (1 - STOP_LOSS_PCT if side == "LONG" else 1 + STOP_LOSS_PCT),
        "target": price * (1 + TAKE_PROFIT_PCT if side == "LONG" else 1 - TAKE_PROFIT_PCT),
    }
    log_trade(ticker, f"ABRIR {side}", price, qty, fee=fee, note=note)

def close_position(ticker, price, note="Cierre manual"):
    position = st.session_state.positions.get(ticker)
    if not position or not price:
        return
    gross = unrealized(position, price)
    exit_fee = position["qty"] * price * FEE_RATE
    net = gross - position["entry_fee"] - exit_fee
    # Return allocated notional to cash, then add directional P&L; entry fee was deducted on open.
    st.session_state.cash += position["notional"] + gross - exit_fee
    log_trade(ticker, f"CERRAR {position['side']}", price, position["qty"], pnl=net, fee=exit_fee, note=note)
    del st.session_state.positions[ticker]

def run_cycle():
    try:
        prices, market_df = get_prices()
        st.session_state.prices = prices
        st.session_state.market_df = market_df
        st.session_state.last_update = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        st.session_state.last_error = None
        signals = {}
        signal_notes = {}
        for ticker in COINS:
            try:
                signals[ticker], signal_notes[ticker] = trend_signal(ticker)
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                signals[ticker], signal_notes[ticker] = "NO DATA", str(exc)
        # Manage exits first; if OHLC is unavailable, stop/target still checked against current live price.
        for ticker in list(st.session_state.positions.keys()):
            position = st.session_state.positions[ticker]
            price = prices[ticker]
            side = position["side"]
            hit_stop = price <= position["stop"] if side == "LONG" else price >= position["stop"]
            hit_target = price >= position["target"] if side == "LONG" else price <= position["target"]
            signal = signals.get(ticker)
            if hit_stop:
                close_position(ticker, price, "Stop-loss 2% (simulado)")
            elif hit_target:
                close_position(ticker, price, "Take-profit 4% (simulado)")
            elif (side == "LONG" and signal == "SHORT") or (side == "SHORT" and signal == "LONG"):
                close_position(ticker, price, "Señal de tendencia contraria")
        if st.session_state.auto_enabled:
            for ticker, signal in signals.items():
                if signal in ("LONG", "SHORT") and ticker not in st.session_state.positions:
                    open_position(ticker, signal, signal_notes[ticker])
        st.session_state.last_cycle = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        st.session_state.signals = {t: (signals[t], signal_notes[t]) for t in COINS}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        st.session_state.last_error = f"No se pudo completar el ciclo: {exc}"

with st.sidebar:
    st.header("Configuración de riesgo (demo)")
    st.write(f"Capital inicial: **€{STARTING_CASH:.2f}**")
    st.write(f"Asignación por posición: **{MAX_ALLOCATION_PCT}% del saldo disponible**")
    st.write(f"Stop-loss simulado: **{STOP_LOSS_PCT*100:.0f}%**")
    st.write(f"Take-profit simulado: **{TAKE_PROFIT_PCT*100:.0f}%**")
    st.write("Estrategia: cruce de medias móviles SMA 9/21")
    st.caption("El stop/objetivo se comprueba al ejecutar cada ciclo; no es una orden de exchange ni garantiza el precio de salida.")
    st.session_state.auto_enabled = st.toggle("Automatización demo activada", value=st.session_state.auto_enabled)
    if st.button("Reiniciar simulación", use_container_width=True):
        st.session_state.cash = STARTING_CASH
        st.session_state.positions = {}
        st.session_state.trades = []
        st.session_state.signals = {}
        st.session_state.last_cycle = None
        st.rerun()

st.info("La automatización de esta versión se ejecuta mientras la app está activa y vuelve a consultar cada 5 minutos. El estado vive en la sesión y puede perderse al reiniciar la app. Para ejecución 24/7 y persistencia hace falta un worker/almacenamiento separado.")
col1, col2 = st.columns([1, 1])
with col1:
    if st.button("▶ Ejecutar ciclo ahora", type="primary", use_container_width=True):
        run_cycle()
with col2:
    st.write(f"Último ciclo: {st.session_state.last_cycle or 'Todavía no ejecutado'}")

if st.session_state.auto_enabled:
    # Streamlit reruns the script on a timer while the session is connected.
    # This is session-bound, not a durable server-side scheduler.
    time.sleep(0.1)
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=CHECK_EVERY_SECONDS * 1000, key="auto_refresh_5m")
    except ImportError:
        st.warning("Falta streamlit-autorefresh. Añádelo a requirements.txt y vuelve a desplegar.")
    if st.session_state.last_cycle is None:
        run_cycle()

if st.session_state.last_error:
    st.error(st.session_state.last_error)
if st.session_state.last_update:
    st.caption(f"Última consulta de precios: {st.session_state.last_update}")

if st.session_state.market_df is not None:
    st.subheader("Mercado")
    st.dataframe(st.session_state.market_df.style.format({"Precio (€)": "€{:,.2f}", "Cambio 24 h (%)": "{:+.2f}%"}, na_rep="N/D"),
                 use_container_width=True, hide_index=True)
    st.subheader("Señales de tendencia")
    signal_rows = []
    for ticker in COINS:
        signal, note = st.session_state.get("signals", {}).get(ticker, ("Sin ejecutar", "Pulsa ejecutar ciclo"))
        signal_rows.append({"Activo": ticker, "Señal": signal, "Detalle": note})
    st.dataframe(pd.DataFrame(signal_rows), use_container_width=True, hide_index=True)

    st.subheader("Posiciones")
    cols = st.columns(3)
    for idx, ticker in enumerate(COINS):
        with cols[idx]:
            st.markdown(f"**{ticker} · {COINS[ticker]['name']}**")
            price = st.session_state.prices.get(ticker)
            if price:
                st.write(f"Precio: €{price:,.2f}")
            position = st.session_state.positions.get(ticker)
            if position and price:
                pnl = unrealized(position, price)
                st.write(f"{position['side']} · P&L abierto: {pnl:+.2f} €")
                st.write(f"Stop: €{position['stop']:,.2f} · Objetivo: €{position['target']:,.2f}")
                st.button("Cerrar posición", key=f"close_{ticker}", on_click=close_position, args=(ticker, price, "Cierre manual"))
            else:
                a, b = st.columns(2)
                a.button("LONG", key=f"long_{ticker}", on_click=open_position, args=(ticker, "LONG"))
                b.button("SHORT", key=f"short_{ticker}", on_click=open_position, args=(ticker, "SHORT"))

    st.subheader("Cartera virtual")
    open_pnl = 0.0
    reserved = 0.0
    for ticker, position in st.session_state.positions.items():
        price = st.session_state.prices.get(ticker, position["entry"])
        open_pnl += unrealized(position, price)
        reserved += position["notional"]
    equity = st.session_state.cash + reserved + open_pnl
    m1, m2, m3 = st.columns(3)
    m1.metric("Saldo disponible", f"€{st.session_state.cash:,.2f}")
    m2.metric("Equity aprox.", f"€{equity:,.2f}", f"{equity-STARTING_CASH:+.2f} €")
    m3.metric("P&L abierto", f"{open_pnl:+.2f} €")
    if st.session_state.positions:
        rows = []
        for ticker, p in st.session_state.positions.items():
            price = st.session_state.prices.get(ticker, p["entry"])
            rows.append({"Activo": ticker, "Tipo": p["side"], "Entrada (€)": p["entry"],
                         "Actual (€)": price, "Cantidad": p["qty"], "P&L abierto (€)": unrealized(p, price)})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No hay posiciones abiertas.")

st.subheader("Historial")
if st.session_state.trades:
    history = pd.DataFrame(st.session_state.trades)
    st.dataframe(history.iloc[::-1], use_container_width=True, hide_index=True)
    st.download_button("Descargar historial CSV", history.to_csv(index=False).encode("utf-8"),
                       "paper_trading_history.csv", "text/csv")
else:
    st.caption("Aún no hay operaciones.")

st.caption("Simulador educativo: los cruces SMA 9/21 son una regla de ejemplo, no una estrategia validada. Los datos históricos pueden no estar disponibles en la API pública; si la API devuelve error, se mostrará sin inventar datos. No conecta con exchanges ni ejecuta órdenes reales.")
