"""Robot de trading simple, Binance TESTNET uniquement.

Stratégie : croisement de deux moyennes mobiles sur bougies de 15 minutes.
- Achat quand la moyenne rapide passe au-dessus de la lente.
- Vente quand elle repasse en dessous, ou si le prix perd STOP_LOSS depuis l'achat.
Le robot est lancé toutes les 15 minutes par GitHub Actions.
"""
import hashlib
import hmac
import math
import os
import sys
import time
from urllib.parse import urlencode

import requests

BASE = "https://testnet.binance.vision"
SYMBOL = "BTCUSDT"
BASE_ASSET = "BTC"
INTERVAL = "15m"
TRADE_USDT = 100      # montant (fictif) par achat
FAST, SLOW = 9, 21    # moyennes mobiles
STOP_LOSS = 0.02      # vente si -2 % depuis l'achat

KEY = os.environ["BINANCE_API_KEY"]
SECRET = os.environ["BINANCE_API_SECRET"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def tg(text):
    """Envoie un message Telegram. Trouve le chat tout seul si besoin."""
    global TG_CHAT
    print(text)
    try:
        if not TG_CHAT:
            r = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates", timeout=15
            ).json()
            for u in reversed(r.get("result", [])):
                m = u.get("message")
                if m:
                    TG_CHAT = str(m["chat"]["id"])
                    break
        if TG_CHAT:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data={"chat_id": TG_CHAT, "text": text},
                timeout=15,
            )
        else:
            print("Chat Telegram introuvable : envoie /start à ton bot.")
    except Exception as e:  # ne jamais planter à cause de Telegram
        print("Erreur Telegram:", e)


def signed(method, path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    q = urlencode(p)
    sig = hmac.new(SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    r = requests.request(
        method,
        f"{BASE}{path}?{q}&signature={sig}",
        headers={"X-MBX-APIKEY": KEY},
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Binance {r.status_code}: {r.text}")
    return r.json()


def sma(values, n):
    return sum(values[-n:]) / n


def main():
    kl = requests.get(
        f"{BASE}/api/v3/klines",
        params={"symbol": SYMBOL, "interval": INTERVAL, "limit": 100},
        timeout=15,
    ).json()
    closes = [float(k[4]) for k in kl[:-1]]  # bougies terminées seulement
    fast, slow = sma(closes, FAST), sma(closes, SLOW)
    pfast, pslow = sma(closes[:-1], FAST), sma(closes[:-1], SLOW)

    price = float(
        requests.get(
            f"{BASE}/api/v3/ticker/price", params={"symbol": SYMBOL}, timeout=15
        ).json()["price"]
    )

    acc = signed("GET", "/api/v3/account")
    bal = {b["asset"]: float(b["free"]) for b in acc["balances"]}
    btc = bal.get(BASE_ASSET, 0.0)
    usdt = bal.get("USDT", 0.0)
    in_position = btc * price >= 10

    action = "aucune"
    if in_position:
        trades = signed("GET", "/api/v3/myTrades", {"symbol": SYMBOL, "limit": 50})
        buys = [t for t in trades if t["isBuyer"]]
        entry = float(buys[-1]["price"]) if buys else None
        stop_hit = entry is not None and price <= entry * (1 - STOP_LOSS)
        if stop_hit or fast < slow:
            qty = math.floor(btc * 1e5) / 1e5
            signed(
                "POST",
                "/api/v3/order",
                {
                    "symbol": SYMBOL,
                    "side": "SELL",
                    "type": "MARKET",
                    "quantity": f"{qty:.5f}",
                },
            )
            why = "stop-loss" if stop_hit else "croisement baissier"
            action = f"VENTE {qty:.5f} {BASE_ASSET} à ~{price:.2f} ({why})"
    else:
        crossed_up = pfast <= pslow and fast > slow
        if crossed_up and usdt >= TRADE_USDT:
            signed(
                "POST",
                "/api/v3/order",
                {
                    "symbol": SYMBOL,
                    "side": "BUY",
                    "type": "MARKET",
                    "quoteOrderQty": str(TRADE_USDT),
                },
            )
            action = f"ACHAT {TRADE_USDT} USDT de {BASE_ASSET} à ~{price:.2f}"

    if action != "aucune":
        tg(f"[TESTNET] {action}")
    elif os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        tg(
            f"[TESTNET] Robot OK. Prix {price:.2f}, "
            f"rapide {fast:.2f}, lente {slow:.2f}, "
            f"position: {'oui' if in_position else 'non'}. Aucune action."
        )
    else:
        print("Aucune action.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        tg(f"[TESTNET] Erreur du robot : {e}")
        sys.exit(1)
