import requests
import json
import os
import time
import ccxt
import subprocess

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
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

# --- SELF-HEALING SYSTEM ---
def self_heal(file_name, default_data, error_msg):
    try:
        with open(file_name, "w") as f:
            json.dump(default_data, f, indent=4)
        send_telegram(f"🚑 *SELF-HEALING ACTIVATED*\n\n❌ **Error:** {error_msg}\n🛠️ **Fix Applied:** Rebuilt `{file_name}`.")
    except Exception as e:
        send_telegram(f"🚨 *CRITICAL ERROR:* Could not heal `{file_name}`. {e}")

def load_json(file, default={}):
    if not os.path.exists(file):
        self_heal(file, default, f"File `{file}` was missing.")
        return default
    try:
        with open(file, "r") as f:
            return json.load(f)
    except Exception as e:
        self_heal(file, default, f"File `{file}` was corrupted: {e}")
        return default

def save_json(file, data):
    try:
        with open(file, "w") as f: json.dump(data, f, indent=4)
    except Exception as e:
        send_telegram(f"⚠️ *SAVE FAILED:* Could not save `{file}`: {e}")

def load_settings():
    defaults = {"rsi_min": 42, "rsi_max": 48, "stop_loss_pct": 15, "auto_buy_enabled": True, "auto_buy_amount_usdt": 5.0}
    return load_json(SETTINGS_FILE, defaults)

def get_ngn_rate():
    try:
        res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10).json()
        return res['rates']['NGN']
    except Exception:
        return None

def git_save():
    try:
        subprocess.run(["git", "pull", "--rebase"], check=True, capture_output=True)
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Auto-saved memory"], capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
    except Exception:
        pass

def perform_audit():
    msg = "🛠️ *SYSTEM AUDIT REPORT*\n\n"
    try:
        exchange.fetch_balance()
        msg += "✅ *Bitget API:* Connected\n"
    except Exception as e:
        send_telegram(f"❌ *Bitget API:* Failed (`{e}`)")
        return

    db = load_json(DB_FILE)
    settings = load_settings()
    
    msg += f"✅ *Active Trades:* {len(db)}\n" # Removed the /3 limit
    msg += f"⚙️ *Rules:* RSI {settings['rsi_min']}-{settings['rsi_max']} | SL {settings['stop_loss_pct']}% | Auto-Buy ${settings['auto_buy_amount_usdt']}\n"
    msg += f"🟢 *Status:* HEALTHY"
    send_telegram(msg)

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
        return closes[-1], closes[-2], rsi, ma5, ma10, min(closes[-5:])
    except Exception:
        return None, None, None, None, None, None

def execute_sell(symbol, percentage, reason):
    try:
        ccxt_symbol = symbol.replace("-", "/")
        base_coin = symbol.split("-")[0]
        balance = exchange.fetch_balance()
        free_balance = balance['free'].get(base_coin, 0)
        
        if free_balance > 0:
            qty = float(exchange.amount_to_precision(ccxt_symbol, free_balance * percentage))
            exchange.create_market_sell_order(ccxt_symbol, qty)
            send_telegram(f"✅ *AUTO-SOLD ({reason})*\n🪙 Sold {percentage*100}% of {symbol}.")
            return True
    except Exception as e:
        send_telegram(f"❌ *AUTO-SELL FAILED ({symbol}):*\n`{e}`")
    return False

def sync_wallet():
    db = load_json(DB_FILE)
    synced = load_json(SYNC_FILE)
    settings = load_settings()
    
    try:
        balance = exchange.fetch_balance()
        free = balance['free']
    except Exception:
        return

    for symbol in list(db.keys()):
        coin = symbol.split("-")[0]
        if free.get(coin, 0) < 0.0001: 
            del db[symbol]
            save_json(DB_FILE, db)

    for coin, amt in free.items():
        if amt > 0 and coin not in STABLECOINS:
            symbol = f"{coin}-USDT"
            if symbol not in db and coin not in synced:
                price, _, _, _, _, _ = get_market_data(symbol)
                if price and (amt * price) >= 1.02: 
                    db[symbol] = {
                        "entry_price": price, "safe_zone": price, 
                        "stop_loss": round(price * (1 - settings['stop_loss_pct']/100), 6),
                        "took_profit_10": False, "took_profit_50": False
                    }
                    synced[coin] = True
                    save_json(DB_FILE, db)
                    save_json(SYNC_FILE, synced)
                    send_telegram(f"🔄 *MANUAL BUY DETECTED*\n🪙 Found {amt} of {coin}.")

def check_telegram_commands():
    state = load_json(STATE_FILE)
    last_id = state.get("last_update_id", 0)
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={last_id + 1}"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get("ok") and len(res.get("result", [])) > 0:
            for update in res["result"]:
                last_id = update["update_id"]
                
                # IGNORE OLD MESSAGES
                if "message" in update and "date" in update["message"]:
                    if int(time.time()) - update["message"]["date"] > 300:
                        continue
                
                if "message" in update and "text" in update["message"]:
                    text = update["message"]["text"].lower()
                    
                    if text == "/audit":
                        perform_audit()
                        
                    elif text == "/balance":
                        try:
                            bal = exchange.fetch_balance()
                            free = bal['free']
                            db = load_json(DB_FILE)
                            ngn_rate = get_ngn_rate()
                            msg = f"💰 *BITGET SPOT BALANCE*\n\n"
                            
                            usdt_bal = free.get('USDT', 0.0)
                            usdt_ngn = f" (₦{usdt_bal * ngn_rate:,.2f})" if ngn_rate else ""
                            msg += f"💵 USDT: `{usdt_bal:.2f}`{usdt_ngn}\n\n📦 *Holdings:*\n"
                            
                            found = False
                            for coin, amt in free.items():
                                if amt > 0 and coin not in ['USDT', 'USDC']:
                                    try:
                                        price = exchange.fetch_ticker(f"{coin}/USDT")['last']
                                        val = amt * price
                                        if val < 0.10: continue
                                        
                                        amt_str = f"{amt:.6f}".rstrip('0').rstrip('.')
                                        val_ngn = f" / ₦{val * ngn_rate:,.2f}" if ngn_rate else ""
                                        pnl_str = ""
                                        if f"{coin}-USDT" in db:
                                            entry = db[f"{coin}-USDT"]["entry_price"]
                                            pnl = ((price - entry) / entry) * 100
                                            pnl_str = f" | 🟢 +{pnl:.1f}%" if pnl >= 0 else f" | 🔴 {pnl:.1f}%"
                                        msg += f"🪙 {coin}: `{amt_str}` (~${val:.2f}{val_ngn}){pnl_str}\n"
                                    except: pass
                                    found = True
                            if not found: msg += "No significant tokens held.\n"
                            send_telegram(msg)
                        except Exception as e:
                            send_telegram(f"❌ Error: {e}")

                    elif text == "/settings":
                        s = load_settings()
                        send_telegram(f"⚙️ *SETTINGS*\nAuto-Buy: `{s['auto_buy_enabled']}`\nSize: `${s['auto_buy_amount_usdt']}`\nRSI: `{s['rsi_min']}-{s['rsi_max']}`\nSL: `{s['stop_loss_pct']}%`")
                    
                    elif text.startswith("/set "):
                        parts = text.split()
                        if len(parts) == 3:
                            k, v = parts[1], parts[2]
                            s = load_settings()
                            if k in s:
                                s[k] = float(v) if v.replace('.','',1).isdigit() else v
                                save_json(SETTINGS_FILE, s)
                                send_telegram(f"✅ `{k}` updated to `{v}`.")
                                
                    elif text == "/clear":
                        save_json(DB_FILE, {})
                        save_json(SYNC_FILE, {})
                        send_telegram("🧹 *MEMORY CLEARED!*")
                        
                    elif text.startswith("/buy"):
                        parts = text.split()
                        if len(parts) == 3:
                            coin = parts[1].upper()
                            usdt_amount = float(parts[2])
                            symbol = f"{coin}/USDT"
                            try:
                                ticker = exchange.fetch_ticker(symbol)
                                current_price = ticker['last']
                                qty = float(exchange.amount_to_precision(symbol, usdt_amount / current_price))
                                exchange.create_market_buy_order(symbol, qty)
                                send_telegram(f"✅ *ORDER EXECUTED!*\n🪙 Bought {qty} of {coin}.")
                            except Exception as e:
                                send_telegram(f"❌ *ORDER FAILED:* {e}")

                    elif text.startswith("/sell"):
                        parts = text.split()
                        if len(parts) == 2:
                            coin = parts[1].upper()
                            if execute_sell(f"{coin}-USDT", 1.0, "Manual Override"):
                                db = load_json(DB_FILE)
                                if f"{coin}-USDT" in db:
                                    del db[f"{coin}-USDT"]
                                    save_json(DB_FILE, db)

                    elif "/status" in text:
                        db = load_json(DB_FILE)
                        if not db: send_telegram("🛑 No active trades.")
                        else:
                            for sym, data in db.items():
                                price, _, rsi, _, _, _ = get_market_data(sym)
                                if price:
                                    inc = ((price - data['entry_price']) / data['entry_price']) * 100
                                    send_telegram(f"🟢 *ACTIVE:* {sym}\n💰 Entry: {data['entry_price']}\n📊 Current: {price} ({inc:+.1f}%)\n🎯 RSI: {rsi}")
            
            state["last_update_id"] = last_id
            save_json(STATE_FILE, state)
    except Exception as e:
        print(f"Telegram Error: {e}")

def manage_active_trades():
    db = load_json(DB_FILE)
    for symbol, data in list(db.items()):
        price, prev, rsi, ma5, ma10, _ = get_market_data(symbol)
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
    
    # UNLIMITED SCANNING: It will buy every valid setup it finds
    for t in valid[15:50]:
        if t['symbol'] in db: continue 
        
        price, prev, rsi, ma5, ma10, _ = get_market_data(t['symbol']) 
        if not price: continue
        
        if t['change'] > 1.0 and settings['rsi_min'] <= rsi <= settings['rsi_max'] and price > ma5 and ma5 > ma10:
            safe_entry = ma5
            sl = round(ma5 * (1 - settings['stop_loss_pct']/100), 6) 
            tp1 = round(safe_entry * 1.10, 6) 
            rr = (tp1 - safe_entry) / (safe_entry - sl) if (safe_entry - sl) > 0 else 0
            
            if rr >= 1.8:
                try:
                    balance = exchange.fetch_balance()
                    usdt_bal = balance['free'].get('USDT', 0)
                    spend = min(usdt_bal, settings['auto_buy_amount_usdt'])
                    
                    if spend >= 1.02:
                        coin = t['symbol'].split('-')[0]
                        qty = float(exchange.amount_to_precision(t['symbol'].replace("-", "/"), spend / price))
                        exchange.create_market_buy_order(t['symbol'].replace("-", "/"), qty)
                        
                        db[t['symbol']] = {
                            "entry_price": price, "safe_zone": price, "stop_loss": sl,
                            "took_profit_10": False, "took_profit_50": False
                        }
                        save_json(DB_FILE, db)
                        send_telegram(f"🤖 *AUTONOMOUS BUY EXECUTED!*\n🪙 {coin} | 💵 {spend:.2f} USDT\n🛑 SL: {sl}")
                        
                        # Small sleep to prevent Bitget API rate limits if it buys many at once
                        time.sleep(2) 
                except Exception as e:
                    send_telegram(f"❌ *BUY FAILED:* {e}")

if __name__ == "__main__":
    try:
        load_json(DB_FILE)
        load_json(STATE_FILE)
        load_json(SYNC_FILE)
        load_settings()
        
        check_telegram_commands() 
        sync_wallet() 
        manage_active_trades()    
        scan_for_new_trades()     
        
        git_save() 
    except Exception as e:
        send_telegram(f"🚨 *CRITICAL SYSTEM FAILURE:* The bot crashed. Error: `{e}`")
