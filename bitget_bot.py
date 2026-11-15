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
SETTINGS_FILE = "settings.json"
QUEUE_FILE = "queue.json"

STABLECOINS = ['USDC', 'USDT', 'DAI', 'FDUSD', 'TUSD', 'USDD', 'USDE']
LEVERAGE_SUFFIXES = ['3L', '3S', '5L', '5S', '10L', '10S', '2L', '2S', '4L', '4S']
BLACKLIST = ['RPANW', 'CT', 'ONE']

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
    defaults = {"rsi_min": 42, "rsi_max": 48, "stop_loss_pct": 15, "auto_buy_enabled": True, "auto_buy_amount_usdt": 5.75, "max_tokens_before_consolidation": 3}
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

# ==========================================
# 🛡️ UPGRADE 1: EXCHANGE HARD STOP-LOSS
# ==========================================
def place_exchange_stop_loss(symbol, qty, trigger_price):
    """Places a real stop-loss order directly on Bitget's servers."""
    try:
        ccxt_symbol = symbol.replace("-", "/")
        exchange.create_order(
            symbol=ccxt_symbol,
            type='market',
            side='sell',
            amount=qty,
            params={'stopPrice': trigger_price, 'triggerPrice': trigger_price, 'stopLossPrice': trigger_price}
        )
        return True
    except Exception as e:
        send_telegram(f"⚠️ *EXCHANGE STOP LOSS FAILED:* Could not place on Bitget. Error: `{e}`\nRelying on software stop loss.")
        return False

# ==========================================
# 🛡️ UPGRADE 3: GLOBAL CIRCUIT BREAKER
# ==========================================
def check_circuit_breaker():
    """Checks total portfolio value. If it drops 10% from its peak, it pauses the bot."""
    settings = load_settings()
    if not settings.get("auto_buy_enabled"): return

    try:
        db = load_json(DB_FILE)
        bal = exchange.fetch_balance()
        free = bal['free']
        
        total_val = free.get('USDT', 0.0) + free.get('USDC', 0.0)
        for symbol in db.keys():
            coin = symbol.split('-')[0]
            amt = free.get(coin, 0)
            if amt > 0:
                price, _, _, _, _ = get_market_data(symbol)
                if price: total_val += (amt * price)
                
        state = load_json(STATE_FILE)
        peak = state.get('portfolio_peak', 0.0)
        
        if total_val > peak:
            state['portfolio_peak'] = total_val
            save_json(STATE_FILE, state)
            
        elif peak > 0 and total_val < (peak * 0.90):
            settings["auto_buy_enabled"] = False
            save_json(SETTINGS_FILE, settings)
            send_telegram(f"🚨 *EMERGENCY CIRCUIT BREAKER!*\n\nYour portfolio dropped >10% from its peak (${peak:.2f} -> ${total_val:.2f}).\n\nThe bot has been paused to protect your funds. Type `/set auto_buy_enabled true` to resume.")
    except Exception as e:
        print(f"Circuit breaker error: {e}")

# ==========================================
# CORE LOGIC
# ==========================================
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

def consolidate_portfolio():
    db = load_json(DB_FILE)
    if len(db) <= 2: return

    send_telegram(f"⚖️ *STARTING CONSOLIDATION...*")
    ranked = []
    for sym, data in db.items():
        price, _, _, _, _ = get_market_data(sym)
        if price and "entry_price" in data:
            pnl = ((price - data['entry_price']) / data['entry_price']) * 100
            ranked.append({"symbol": sym, "pnl": pnl, "price": price})
    
    ranked.sort(key=lambda x: x["pnl"], reverse=True)
    keep = ranked[:2]
    sell = ranked[2:]

    for t in sell:
        sym = t['symbol']
        if execute_sell(sym, 1.0, "Consolidation"):
            del db[sym]
            save_json(DB_FILE, db)

def execute_from_queue():
    queue = load_json(QUEUE_FILE)
    db = load_json(DB_FILE)
    settings = load_settings()
    
    if not queue: return
    
    sorted_queue = sorted(queue.items(), key=lambda x: x[1].get('score', 0), reverse=True)
    
    if len(db) < settings.get("max_tokens_before_consolidation", 3):
        try:
            balance = exchange.fetch_balance()
            usdt_bal = balance['free'].get('USDT', 0)
            
            for sym, data in sorted_queue:
                if len(db) >= settings.get("max_tokens_before_consolidation", 3): break
                
                base = sym.split("-")[0]
                if base in BLACKLIST: continue
                
                price, _, _, _, _ = get_market_data(sym)
                if not price: continue
                
                spend = min(usdt_bal, settings['auto_buy_amount_usdt'])
                if spend >= 1.02:
                    ccxt_symbol = sym.replace("-", "/")
                    qty = float(exchange.amount_to_precision(ccxt_symbol, spend / price))
                    
                    exchange.create_market_buy_order(ccxt_symbol, qty)
                    
                    sl_price = data['sl']
                    sl_placed = place_exchange_stop_loss(sym, qty, sl_price)
                    sl_msg = "🛡️ Exchange SL Placed" if sl_placed else "⚠️ Software SL Only"
                    
                    db[sym] = {
                        "entry_price": price, "safe_zone": price, "stop_loss": sl_price,
                        "took_profit_10": False, "took_profit_50": False
                    }
                    save_json(DB_FILE, db)
                    
                    del queue[sym]
                    save_json(QUEUE_FILE, queue)
                    
                    usdt_bal -= spend
                    send_telegram(f"🛒 *QUEUE EXECUTED!*\n{sym} | 💵 {spend:.2f} USDT\n🛑 SL: {sl_price}\n{sl_msg}")
                    time.sleep(2)
        except Exception as e:
            print(f"Queue execution error: {e}")

def scan_and_queue():
    db = load_json(DB_FILE)
    queue = load_json(QUEUE_FILE)
    settings = load_settings()
    
    check_circuit_breaker()
    
    if not settings.get("auto_buy_enabled", False): return 

    # ==========================================
    # 🛡️ UPGRADE 2: BTC CRASH FILTER
    # ==========================================
    btc_price, _, btc_rsi, _, _ = get_market_data("BTC-USDT")
    if btc_rsi and btc_rsi < 35:
        state = load_json(STATE_FILE)
        last_btc_alert = state.get('last_btc_alert', 0)
        if time.time() - last_btc_alert > 3600:
            send_telegram(f"🐋 *BTC CRASH FILTER:* BTC RSI is {btc_rsi}. The market is bleeding. Pausing new buys to protect capital.")
            state['last_btc_alert'] = time.time()
            save_json(STATE_FILE, state)
        return 

    if len(db) > settings.get("max_tokens_before_consolidation", 3):
        send_telegram(f"⚠️ *AUTO-CONSOLIDATING:* You have {len(db)} tokens.")
        consolidate_portfolio()
        db = load_json(DB_FILE) 

    try: 
        tickers = exchange.fetch_tickers()
        markets = exchange.load_markets() 
    except Exception: return
        
    valid = []
    for sym, t in tickers.items():
        if sym.endswith('/USDT'):
            base = sym.split('/')[0]
            
            if base in STABLECOINS: continue
            if base in BLACKLIST: continue 
            if base.startswith('X') and len(base) <= 6: continue
            if any(base.upper().endswith(suffix) for suffix in LEVERAGE_SUFFIXES): continue
            if sym in markets and not markets[sym].get('spot', False): continue

            vol = t['quoteVolume'] if t['quoteVolume'] else 0
            if vol >= 1000000: 
                valid.append({'symbol': sym.replace("/", "-"), 'vol': vol, 'change': t['percentage'] if t['percentage'] else 0})
                
    valid.sort(key=lambda x: x['vol'], reverse=True)
    
    current_time = int(time.time())
    queue_updated = False
    
    for sym, data in list(queue.items()):
        if current_time - data.get('timestamp', 0) > 86400:
            del queue[sym]
            queue_updated = True

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
            rr_ratio = (price * 0.10) / (price - sl) if (price - sl) > 0 else 0
            score = (t['vol'] / 1000000) + (rr_ratio * 10)
            
            if t['symbol'] not in queue or queue[t['symbol']].get('score', 0) < score:
                is_new = t['symbol'] not in queue
                queue[t['symbol']] = {
                    "timestamp": current_time,
                    "type": setup,
                    "sl": sl,
                    "score": score
                }
                queue_updated = True
                
                if is_new:
                    send_telegram(f"📥 *QUEUED:* {t['symbol']}\nSetup: {setup}\nScore: {score:.1f}")
                    
    if queue_updated:
        save_json(QUEUE_FILE, queue)

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
                        try:
                            bal = exchange.fetch_balance()
                            free = bal['free']
                            usdt = free.get('USDT', 0)
                            rate = get_ngn_rate()
                            ngn_str = f" (₦{usdt * rate:,.2f})" if rate else ""
                            queue = load_json(QUEUE_FILE)
                            msg = f"🛠️ *SYSTEM AUDIT*\n\n✅ *API:* Connected\n💵 *Balance:* {usdt:.2f} USDT{ngn_str}\n✅ *Trades:* {len(load_json(DB_FILE))}\n📥 *Queue:* {len(queue)}\n⚙️ *Buy Size:* ${load_settings()['auto_buy_amount_usdt']}"
                            send_telegram(msg)
                        except Exception as e:
                            send_telegram(f"❌ Audit failed: {e}")
                            
                    elif text == "/balance":
                        bal = exchange.fetch_balance()
                        free = bal['free']
                        rate = get_ngn_rate()
                        usdt = free.get('USDT', 0)
                        ngn_str = f" (₦{usdt * rate:,.2f})" if rate else ""
                        send_telegram(f"💰 *Balance:* {usdt:.2f} USDT{ngn_str}")
                                
                    elif text == "/clear":
                        save_json(DB_FILE, {})
                        save_json(QUEUE_FILE, {})
                        send_telegram("🧹 *MEMORY & QUEUE CLEARED!*")

                    elif text == "/queue":
                        queue = load_json(QUEUE_FILE)
                        if not queue: send_telegram("📥 Queue is empty.")
                        else:
                            msg = "📥 *PRIORITY QUEUE*\n\n"
                            sorted_queue = sorted(queue.items(), key=lambda x: x[1].get('score', 0), reverse=True)
                            for sym, data in sorted_queue:
                                msg += f"🪙 {sym} ({data['type']})\n🏆 Score: {data.get('score', 0):.1f}\n\n"
                            send_telegram(msg)

                    elif text == "/consolidate":
                        consolidate_portfolio()

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
                                    send_telegram(f"✅ *SETTING SAVED!* `{k}` is now `{s[k]}`.")
                                except ValueError:
                                    send_telegram("❌ Invalid value format.")
                        else:
                            send_telegram("❌ Format: `/set [key] [value]`")

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
        scan_and_queue()
        execute_from_queue()
        
        subprocess.run(["git", "pull", "--rebase"], capture_output=True)
        subprocess.run(["git", "add", "."], capture_output=True)
        subprocess.run(["git", "commit", "-m", "Auto-save"], capture_output=True)
        subprocess.run(["git", "push"], capture_output=True)
        
    except Exception as e:
        send_telegram(f"🚨 *CRITICAL FAILURE:* `{e}`\n\n```\n{traceback.format_exc()[-200:]}\n```")
