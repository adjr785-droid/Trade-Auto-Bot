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
PENDING_FILE = "pending_trade.json"

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
    defaults = {"rsi_min": 42, "rsi_max": 48, "stop_loss_pct": 15, "auto_buy_enabled": True, "auto_buy_amount_usdt": 5.75, "max_tokens_before_consolidation": 3}
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

def run_diagnostics():
    try:
        exchange.fetch_balance()
    except Exception as e:
        return False, f"Bitget API Connection Failed: {e}"

    pending = load_json(PENDING_FILE)
    current_time = int(time.time())
    stuck_cleared = 0
    for sym, data in list(pending.items()):
        if current_time - data["timestamp"] > 86400:
            del pending[sym]
            stuck_cleared += 1
    
    if stuck_cleared > 0:
        save_json(PENDING_FILE, pending)
        send_telegram(f"🔧 *AUTO-FIX:* Cleared {stuck_cleared} stuck pending trades older than 24h.")
        
    return True, "System Nominal"

def perform_audit():
    msg = "🛠️ *SYSTEM AUDIT REPORT*\n\n"
    try:
        bal = exchange.fetch_balance()
        free = bal['free']
        msg += "✅ *Bitget API:* Connected\n"
    except Exception as e:
        send_telegram(f"❌ *Bitget API:* Failed (`{e}`)")
        return

    db = load_json(DB_FILE)
    settings = load_settings()
    ngn_rate = get_ngn_rate()
    
    usdt_bal = free.get('USDT', 0.0)
    usdt_ngn = f" (₦{usdt_bal * ngn_rate:,.2f})" if ngn_rate else ""
    
    msg += f"💵 *Balance:* `{usdt_bal:.2f}` USDT{usdt_ngn}\n"
    msg += f"✅ *Active Trades:* {len(db)}\n"
    msg += f"⚙️ *Rules:* RSI {settings['rsi_min']}-{settings['rsi_max']} | SL {settings['stop_loss_pct']}% | Auto-Buy ${settings['auto_buy_amount_usdt']}\n"
    msg += f"⚖️ *Auto-Consolidate at:* {settings['max_tokens_before_consolidation']} tokens\n"
    msg += f"🟢 *Status:* HEALTHY"
    send_telegram(msg)

def check_6_hour_audit():
    state = load_json(STATE_FILE)
    last_audit = state.get("last_periodic_audit", 0)
    current_time = int(time.time())
    
    if current_time - last_audit >= 21600:
        perform_audit()
        state["last_periodic_audit"] = current_time
        save_json(STATE_FILE, state)

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
            ticker = exchange.fetch_ticker(ccxt_symbol)
            current_price = ticker['last']
            
            qty_to_sell = free_balance * percentage
            value_to_sell = qty_to_sell * current_price
            total_value = free_balance * current_price
            
            if total_value < 1.02:
                return False
                
            if value_to_sell < 1.02:
                qty_to_sell = free_balance
                reason += " (Sold 100% - Partial < $1.02)"
                
            qty = float(exchange.amount_to_precision(ccxt_symbol, qty_to_sell))
            exchange.create_market_sell_order(ccxt_symbol, qty)
            send_telegram(f"✅ *AUTO-SOLD ({reason})*\n🪙 Sold {qty} of {symbol}.\n💰 Value: ~${(qty * current_price):.2f} USDT")
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
                
                if "message" in update and "date" in update["message"]:
                    if int(time.time()) - update["message"]["date"] > 900: continue
                
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
                        send_telegram(f"⚙️ *SETTINGS*\nAuto-Buy: `{s['auto_buy_enabled']}`\nSize: `${s['auto_buy_amount_usdt']}`\nRSI: `{s['rsi_min']}-{s['rsi_max']}`\nSL: `{s['stop_loss_pct']}%`\nConsolidate At: `{s['max_tokens_before_consolidation']}` tokens")
                    
                    elif text.startswith("/set "):
                        parts = text.split()
                        if len(parts) == 3:
                            k, v = parts[1], parts[2]
                            s = load_settings()
                            if k in s:
                                try:
                                    if k in ["rsi_min", "rsi_max", "max_tokens_before_consolidation"]: s[k] = int(v)
                                    elif k in ["stop_loss_pct", "auto_buy_amount_usdt"]: s[k] = float(v)
                                    elif k == "auto_buy_enabled": s[k] = (v.lower() == 'true')
                                    
                                    save_json(SETTINGS_FILE, s)
                                    send_telegram(f"✅ *SETTING SAVED!*\n\n{s}")
                                except ValueError:
                                    send_telegram("❌ Invalid value format.")
                            else:
                                send_telegram("❌ Invalid key.")
                        else:
                            send_telegram("❌ Format: `/set [key] [value]`")
                                
                    elif text == "/clear":
                        save_json(DB_FILE, {})
                        save_json(SYNC_FILE, {})
                        send_telegram("🧹 *MEMORY CLEARED!*")

                    elif text == "/consolidate":
                        consolidate_portfolio()

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

# --- CONSOLIDATION ENGINE ---
def consolidate_portfolio():
    db = load_json(DB_FILE)
    settings = load_settings()
    
    if len(db) <= 2:
        send_telegram("🛑 *CONSOLIDATION SKIPPED:* You have 2 or fewer tokens. Nothing to consolidate.")
        return

    send_telegram(f"⚖️ *STARTING CONSOLIDATION...*\nYou have {len(db)} tokens. Reducing to the top 2 strongest performers.")

    ranked_tokens = []
    for sym, data in db.items():
        price, _, rsi, _, _, _ = get_market_data(sym)
        if price:
            pnl = ((price - data['entry_price']) / data['entry_price']) * 100
            ranked_tokens.append({"symbol": sym, "pnl": pnl, "price": price})
    
    ranked_tokens.sort(key=lambda x: x["pnl"], reverse=True)

    keep_tokens = ranked_tokens[:2]
    sell_tokens = ranked_tokens[2:]

    keep_names = [t['symbol'] for t in keep_tokens]
    send_telegram(f"🏆 *KEEPING (Top Performers):*\n" + "\n".join([f"🟢 {t['symbol']} ({t['pnl']:+.1f}%)" for t in keep_tokens]))
    send_telegram(f"🩸 *LIQUIDATING (Weakest):*\n" + "\n".join([f"🔴 {t['symbol']} ({t['pnl']:+.1f}%)" for t in sell_tokens]))

    for t in sell_tokens:
        sym = t['symbol']
        if execute_sell(sym, 1.0, "Consolidation"):
            del db[sym]
            save_json(DB_FILE, db)

    time.sleep(5) 
    bal = exchange.fetch_balance()
    available_usdt = bal['free'].get('USDT', 0.0)

    if available_usdt >= 1.02:
        split_amount = available_usdt / 2
        
        if split_amount < 1.02:
            split_amount = available_usdt
            keep_tokens = keep_tokens[:1] 
            
        for t in keep_tokens:
            try:
                ccxt_symbol = t['symbol'].replace("-", "/")
                ticker = exchange.fetch_ticker(ccxt_symbol)
                price = ticker['last']
                
                qty = float(exchange.amount_to_precision(ccxt_symbol, split_amount / price))
                exchange.create_market_buy_order(ccxt_symbol, qty)
                
                send_telegram(f"🛒 *REINVESTED:* Bought {qty} of {t['symbol']} with {split_amount:.2f} USDT.")
                
                db[t['symbol']]['entry_price'] = price
                save_json(DB_FILE, db)
                
            except Exception as e:
                send_telegram(f"❌ *REINVEST FAILED ({t['symbol']}):* {e}")
                
    send_telegram("✅ *CONSOLIDATION COMPLETE!*")

def scan_for_new_trades():
    db = load_json(DB_FILE)
    settings = load_settings()
    
    if not settings.get("auto_buy_enabled", False): return 

    # ==========================================
    # 🚨 AUTO-CONSOLIDATION TRIGGER 🚨
    # ==========================================
    max_tokens = settings.get("max_tokens_before_consolidation", 3)
    if len(db) > max_tokens:
        send_telegram(f"⚠️ *TRIGGERED:* You have {len(db)} tokens (Limit is {max_tokens}). Auto-consolidating now...")
        consolidate_portfolio()
        db = load_json(DB_FILE) # Reload memory after consolidation

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
        
        price, prev_close, rsi, ma5, ma10, recent_low = get_market_data(t['symbol']) 
        if not price: continue
        
        setup_type = None
        sl = 0

        if t['change'] > 1.0 and settings['rsi_min'] <= rsi <= settings['rsi_max'] and price > ma5 and ma5 > ma10:
            setup_type = "TREND PULLBACK"
            sl = round(ma5 * (1 - settings['stop_loss_pct']/100), 6)
        elif t['change'] < -8.0 and rsi < 30 and price > prev_close:
            setup_type = "OVERSOLD BOUNCE"
            sl = round(recent_low * 0.98, 6) 
            
        if setup_type:
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
                    
                    icon = "🏆" if setup_type == "TREND PULLBACK" else "🩸"
                    send_telegram(f"🤖 *AUTONOMOUS BUY EXECUTED!*\n{icon} *{setup_type}*\n🪙 {coin} | 💵 {spend:.2f} USDT\n🛑 SL: {sl}")
                    
                    time.sleep(2) 
            except Exception as e:
                send_telegram(f"❌ *BUY FAILED:* {e}")

if __name__ == "__main__":
    try:
        is_healthy, status_msg = run_diagnostics()
        if not is_healthy:
            send_telegram(f"🚨 *DIAGNOSTIC FAILURE:* {status_msg}\nBot is pausing to protect your funds.")
            exit(0)

        lo
