import requests
import json
import os
import time
import ccxt
import subprocess
import traceback

# Load from GitHub Secrets
TG_TOKEN = os.environ.get('TG_TOKEN')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID')
DB_FILE = "positions.json"
STATE_FILE = "tg_state.json"
SYNC_FILE = "sync_state.json"
SETTINGS_FILE = "settings.json"

STABLECOINS = ['USDC', 'USDT', 'DAI', 'FDUSD', 'TUSD', 'USDD', 'USDE', 'ONE', 'CT']

exchange = ccxt.bitget({
    'apiKey': os.environ.get('BITGET_API_KEY'),
    'secret': os.environ.get('BITGET_API_SECRET'),
    'password': os.environ.get('BITGET_API_PASSPHRASE'),
    'enableRateLimit': True,
})

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def load_json(file, default={}):
    if not os.path.exists(file): return default
    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return default

def save_json(file, data):
    with open(file, "w") as f: json.dump(data, f, indent=4)

def load_settings():
    defaults = {"rsi_min": 42, "rsi_max": 48, "stop_loss_pct": 15, "auto_buy_enabled": True, "auto_buy_amount_usdt": 5.75}
    s = load_json(SETTINGS_FILE, {})
    for k, v in defaults.items():
        if k not in s: s[k] = v
    return s

def get_ngn_rate():
    try:
        res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10).json()
        return res['rates']['NGN']
    except: return None

def get_market_data(symbol):
    try:
        ccxt_symbol = symbol.replace("-", "/")
        ohlcv = exchange.fetch_ohlcv(ccxt_symbol, timeframe='1h', limit=50)
        closes = [float(candle[4]) for candle in ohlcv[:-1]] 
        
        gains = [max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
        losses = [max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
        avg_gain = sum(gains[-14:])/14
        avg_loss = sum(losses[-14:])/14
        rsi = 100.0 if avg_loss == 0 else round(100 - (100 / (1 + (avg_gain / avg_loss))), 2)
            
        ma5 = round(sum(closes[-5:]) / 5, 6)
        ma10 = round(sum(closes[-10:]) / 10, 6)
        return closes[-1], closes[-2], rsi, ma5, ma10
    except Exception:
        return None, None, None, None, None

def execute_sell(symbol, percentage, reason):
    try:
        ccxt_symbol = symbol.replace("-", "/")
        base_coin = symbol.split("-")[0]
        balance = exchange.fetch_balance()
        free_balance = balance['free'].get(base_coin, 0)
        
        if free_balance > 0:
            ticker = exchange.fetch_ticker(ccxt_symbol)
            price = ticker['last']
            qty_to_sell = free_balance * percentage
            val = qty_to_sell * price
            
            if val < 1.02 and percentage < 1.0:
                qty_to_sell = free_balance
                reason += " (Sold 100% - Partial < $1.02)"
                
            qty = float(exchange.amount_to_precision(ccxt_symbol, qty_to_sell))
            exchange.create_market_sell_order(ccxt_symbol, qty)
            send_telegram(f"✅ *AUTO-SOLD ({reason})*\n🪙 Sold {qty} of {symbol}.")
            return True
    except Exception as e:
        send_telegram(f"❌ *SELL FAILED ({symbol}):*\n`{e}`")
    return False

def manage_active_trades():
    db = load_json(DB_FILE)
    for symbol, data in list(db.items()):
        price, prev, rsi, ma5, ma10 = get_market_data(symbol)
        if not price: continue
        
        inc = ((price - data["entry_price"]) / data["entry_price"]) * 100
        
        if price <= data["stop_loss"]:
            if execute_sell(symbol, 1.0, "STOP LOSS"): del db[symbol]; save_json(DB_FILE, db)
            continue
        if rsi >= 75:
            if execute_sell(symbol, 1.0, "RSI 75 HIT"): del db[symbol]; save_json(DB_FILE, db)
            continue
        elif rsi >= 70 and not data.get("took_profit_50"):
            if execute_sell(symbol, 0.50, "RSI 70 HIT"): db[symbol]["took_profit_50"] = True; save_json(DB_FILE, db)
        elif inc >= 10.0 and not data.get("took_profit_10"):
            if execute_sell(symbol, 0.25, "+10% TARGET"): db[symbol]["took_profit_10"] = True; save_json(DB_FILE, db)

def scan_for_new_trades():
    db = load_json(DB_FILE)
    settings = load_settings()
    if not settings.get("auto_buy_enabled", False): return 

    try: tickers = exchange.fetch_tickers()
    except Exception: return
        
    valid = []
    for sym, t in tickers.items():
        if sym.endswith('/USDT'):
            base = sym.split('/')[0]
            if base in STABLECOINS or (base.startswith('X') and len(base) <= 6): continue
            vol = t['quoteVolume'] if t['quoteVolume'] else 0
            if vol >= 1000000: 
                valid.append({'symbol': sym.replace("/", "-"), 'vol': vol, 'change': t['percentage'] if t['percentage'] else 0})
                
    valid.sort(key=lambda x: x['vol'], reverse=True)
    
    for t in valid[15:50]:
        if t['symbol'] in db: continue 
        
        price, prev_close, rsi, ma5, ma10 = get_market_data(t['symbol']) 
        if not price: continue
        
        setup = None
        sl = 0

        if t['change'] > 1.0 and settings['rsi_min'] <= rsi <= settings['rsi_max'] and price > ma5 and ma5 > ma10:
            setup = "TREND PULLBACK"
            sl = round(ma5 * (1 - settings['stop_loss_pct']/100), 6)
        elif t['change'] < -8.0 and rsi < 30 and price > prev_close:
            setup = "OVERSOLD BOUNCE"
            sl = round(price * 0.95, 6)
            
        if setup:
            try:
                balance = exchange.fetch_balance()
                spend = min(balance['free'].get('USDT', 0), settings['auto_buy_amount_usdt'])
                
                if spend >= 1.02:
                    coin = t['symbol'].split('-')[0]
                    qty = float(exchange.amount_to_precision(t['symbol'].replace("-", "/"), spend / price))
                    exchange.create_market_buy_order(t['symbol'].replace("-", "/"), qty)
                    
                    db[t['symbol']] = {
                        "entry_price": price, "safe_zone": price, "stop_loss": sl,
                        "took_profit_10": False, "took_profit_50": False
                    }
                    save_json(DB_FILE, db)
                    send_telegram(f"🤖 *AUTO-BUY EXECUTED!*\n{setup}\n🪙 {coin} | 💵 {spend:.2f} USDT\n🛑 SL: {sl}")
                    time.sleep(2) 
            except Exception as e:
                send_telegram(f"❌ *BUY FAILED:* {e}")

def check_telegram():
    state = load_json(STATE_FILE)
    last_id = state.get("last_update_id", 0)
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={last_id + 1}"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get("ok") and len(res.get("result", [])) > 0:
            for update in res["result"]:
                last_id = update["update_id"]
                
                if "message" in update and "date" in update["message"]:
                    if int(time.time()) - update["message"]["date"] > 900: continue
                
                if "message" in update and "text" in update["message"]:
                    text = update["message"]["text"].lower()
                    
                    if text == "/audit":
                        send_telegram("✅ *Bot is online and healthy.*")
                    elif text == "/balance":
                        bal = exchange.fetch_balance()
                        usdt = bal['free'].get('USDT', 0)
                        send_telegram(f"💰 *Balance:* {usdt:.2f} USDT")
                                
                    elif text == "/clear":
                        save_json(DB_FILE, {})
                        send_telegram("🧹 *MEMORY CLEARED!*")

                    elif "/status" in text:
                        db = load_json(DB_FILE)
                        if not db: send_telegram("🛑 No active trades.")
                        else:
                            for sym, data in db.items():
                                price, _, rsi, _, _ = get_market_data(sym)
                                if price:
                                    inc = ((price - data['entry_price']) / data['entry_price']) * 100
                                    send_telegram(f"🟢 *ACTIVE:* {sym}\n💰 Entry: {data['entry_price']}\n📊 Current: {price} ({inc:+.1f}%)\n🎯 RSI: {rsi}")
            
            state["last_update_id"] = last_id
            save_json(STATE_FILE, state)
    except Exception as e:
        print(f"Telegram Error: {e}")

if __name__ == "__main__":
    try:
        check_telegram()
        manage_active_trades()    
        scan_for_new_trades()     
        
        subprocess.run(["git", "pull", "--rebase"], capture_output=True)
        subprocess.run(["git", "add", "."], capture_output=True)
        subprocess.run(["git", "commit", "-m", "Auto-save"], capture_output=True)
        subprocess.run(["git", "push"], capture_output=True)
        
    except Exception as e:
        send_telegram(f"🚨 *CRITICAL FAILURE:* `{e}`\n\n```\n{traceback.format_exc()[-200:]}\n```")
