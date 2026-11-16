import requests, json, os, time, ccxt, subprocess, traceback
from google import genai
TG_TOKEN=os.environ.get('TG_TOKEN'); TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
DB_FILE="positions.json"; STATE_FILE="tg_state.json"; SETTINGS_FILE="settings.json"; QUEUE_FILE="queue.json"; LEDGER_FILE="profit_ledger.json"
STABLECOINS=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE']; LEV=['3L','3S','5L','5S','10L','10S','2L','2S']; BL=['RPANW','CT','ONE']
exchange=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True})
client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))

def tg(m):
    try: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id":TG_CHAT_ID,"text":m,"parse_mode":"Markdown"}, timeout=10)
    except: pass

def tg_ai(m):
    try: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id":TG_CHAT_ID,"text":m,"parse_mode":"HTML"}, timeout=10)
    except: pass

def load(f, d={}):
    try: return json.load(open(f)) if os.path.exists(f) else d
    except: return d

def save(f, d): json.dump(d, open(f, "w"), indent=4)

def settings():
    d={"rsi_min":42,"rsi_max":48,"stop_loss_pct":15,"sl_sell_percent":0.5,"dca_reserve":2.0,"auto_buy_enabled":True,"auto_buy_amount_usdt":5.75,"max_tokens_before_consolidation":3}
    s=load(SETTINGS_FILE, d)
    for k,v in d.items(): s.setdefault(k,v)
    return s

def data(sym):
    try:
        o=exchange.fetch_ohlcv(sym.replace("-","/"),'1h',limit=50); c=[float(k[4]) for k in o[:-1]]
        g=[max(0,c[i]-c[i-1]) for i in range(1,len(c))]; l=[max(0,c[i-1]-c[i]) for i in range(1,len(c))]
        ag=sum(g[-14:])/14; al=sum(l[-14:])/14; r=100.0 if al==0 else round(100-(100/(1+(ag/al))),2)
        return c[-1], c[-2], r, round(sum(c[-5:])/5,6), round(sum(c[-10:])/10,6)
    except: return None,None,None,None,None

def ask_ai(prompt):
    try:
        db = load(DB_FILE); q = load(QUEUE_FILE); s = settings()
        bal = exchange.fetch_balance()['free'].get('USDT', 0)
        btc_p, _, btc_rsi, _, _ = data("BTC-USDT")
        context = f"System Data:\n- USDT Balance: {bal}\n- Auto-Buy Size per Trade: {s['auto_buy_amount_usdt']}\n- BTC RSI: {btc_rsi}\n- Active Trades: {db}\n- Queue: {q}\n- Bot Rules: RSI {s['rsi_min']}-{s['rsi_max']}, SL {s['stop_loss_pct']}%\n\n"
        ai_prompt = f"You are an expert crypto trading analyst. Use this live data to answer the user. Format your response using HTML tags (<b>, <i>, <br>) instead of markdown.\n{context}\nUser Question: {prompt}"
        response = client.models.generate_content(model='gemini-3.8-flash', contents=ai_prompt)
        tg_ai(f"🧠 <b>AI ANALYST:</b>\n\n{response.text}")
    except Exception as e: tg(f"❌ AI Error: {e}")

def hard_sl(sym, qty, pr):
    try:
        try:
            for o in exchange.fetch_open_orders(sym.replace("-","/")):
                if o['side']=='sell': exchange.cancel_order(o['id'], sym.replace("-","/"))
        except: pass
        if qty > 0:
            exchange.create_order(sym.replace("-","/"), 'market', 'sell', qty, params={'stopPrice':pr,'triggerPrice':pr,'stopLossPrice':pr})
        return True
    except: return False

def sell(sym, pct, reason):
    try:
        cs=sym.replace("-","/"); coin=sym.split("-")[0]; fb=exchange.fetch_balance()['free'].get(coin,0)
        if fb>0:
            p=exchange.fetch_ticker(cs)['last']; qty=fb*pct
            if qty*p<1.02 and pct<1.0: qty=fb; reason+=" (<$1.02)"
            qty=float(exchange.amount_to_precision(cs, qty)); exchange.create_market_sell_order(cs, qty)
            db=load(DB_FILE)
            if sym in db:
                pnl=(p-db[sym]["entry_price"])*qty; L=load(LEDGER_FILE)
                L['total_realized_usdt']=L.get('total_realized_usdt',0.0)+pnl; L['trade_count']=L.get('trade_count',0)+1
                save(LEDGER_FILE,L); tg(f"✅ *SOLD ({reason})*\n🪙 {qty} {sym}\n{'🟢' if pnl>=0 else '🔴'} PnL: ${pnl:.2f}")
            return True
    except Exception as e: tg(f"❌ *SELL FAILED:* {e}")
    return False

def manage():
    db=load(DB_FILE); s=settings()
    for sym,d in list(db.items()):
        p,pr,r,ma5,ma10=data(sym)
        if not p: continue
        inc=((p-d["entry_price"])/d["entry_price"])*100
        
        if p <= d["stop_loss"]:
            if not d.get("sl_hit"):
                pct_to_sell = s.get('sl_sell_percent', 0.5)
                coin_bal = exchange.fetch_balance()['free'].get(sym.split('-')[0], 0)
                if (coin_bal * pct_to_sell * p) < 1.02: pct_to_sell = 1.0 
                    
                if sell(sym, pct_to_sell, "PARTIAL STOP LOSS"):
                    db[sym]["sl_hit"] = True
                    new_sl = round(p * (1 - s['stop_loss_pct']/100), 6)
                    db[sym]["stop_loss"] = new_sl
                    save(DB_FILE, db)
                    
                    rem_qty = exchange.fetch_balance()['free'].get(sym.split('-')[0], 0)
                    if rem_qty > 0: hard_sl(sym, rem_qty, new_sl)
                    tg(f"⚠️ *PARTIAL STOP LOSS!*\nSold {pct_to_sell*100}% of {sym} to protect capital.\nRemaining {100-(pct_to_sell*100)}% is protected at a new SL of {new_sl}.")
            else:
                if sell(sym, 1.0, "FINAL STOP LOSS"): 
                    del db[sym]; save(DB_FILE, db)
            continue
            
        elif r>=75: 
            if sell(sym,1.0,"RSI 75"): del db[sym]; save(DB_FILE,db)
        elif r>=70 and not d.get("took_profit_50"): 
            if sell(sym,0.5,"RSI 70"): db[sym]["took_profit_50"]=True; save(DB_FILE,db)
        elif inc>=10.0 and not d.get("took_profit_10"): 
            if sell(sym,0.25,"+10%"): db[sym]["took_profit_10"]=True; save(DB_FILE,db)

def consolidate():
    db=load(DB_FILE)
    if len(db)<=2: return
    tg("⚖️ *CONSOLIDATING...*"); rk=[]
    for s,d in db.items():
        p,_,_,_,_=data(s)
        if p and "entry_price" in d: rk.append({"s":s,"pnl":((p-d['entry_price'])/d['entry_price'])*100})
    rk.sort(key=lambda x:x["pnl"], reverse=True)
    for t in rk[2:]:
        if sell(t['s'],1.0,"Consolidation"): del db[t['s']]; save(DB_FILE,db)

def exec_queue():
    q=load(QUEUE_FILE); db=load(DB_FILE); s=settings()
    if not q: return
    sq=sorted(q.items(), key=lambda x:x[1].get('score',0), reverse=True)
    
    try:
        ub=exchange.fetch_balance()['free'].get('USDT',0)
        for sym,d in sq:
            p,_,_,_,_=data(sym)
            if not p: continue
            
            is_dca = sym in db
            
            # 🚨 RULE 2: DCA can use 100%, New Buys must leave $2 Reserve 🚨
            if is_dca:
                spendable_ub = ub 
            else:
                spendable_ub = ub - s.get('dca_reserve', 2.0)
                
            if spendable_ub < 1.02:
                if not is_dca: tg(f"⚠️ *SKIPPING BUY:* Wallet has ${ub:.2f}. Need to keep ${s.get('dca_reserve', 2.0):.2f} for DCA.")
                continue

            sp=min(spendable_ub, s['auto_buy_amount_usdt'])
            cs=sym.replace("-","/")
            
            if is_dca:
                if db[sym].get('dca_count', 0) >= 2:
                    del q[sym]; save(QUEUE_FILE,q); continue
                
                qty=float(exchange.amount_to_precision(cs, sp/p))
                exchange.create_limit_buy_order(cs, qty, p)
                
                bal = exchange.fetch_balance()['free'].get(sym.split('-')[0], 0)
                old_qty = bal - qty
                if old_qty <= 0: old_qty = 0
                
                old_entry = db[sym]['entry_price']
                new_entry = ((old_qty * old_entry) + (qty * p)) / bal if bal > 0 else p
                new_sl = round(new_entry * (1 - s['stop_loss_pct']/100), 6)
                
                db[sym]['entry_price'] = round(new_entry, 6)
                db[sym]['stop_loss'] = new_sl
                db[sym]['took_profit_10'] = False
                db[sym]['took_profit_50'] = False
                db[sym]['dca_count'] = db[sym].get('dca_count', 0) + 1
                db[sym]['sl_hit'] = False 
                save(DB_FILE, db)
                
                sl_qty = bal * s.get('sl_sell_percent', 0.5)
                hard_sl(sym, sl_qty, new_sl)
                tg(f"🛒 *AUTO-DCA EXECUTED!*\n{sym} | 💵 {sp:.2f} USDT\n📉 New Entry: {new_entry:.6f}\n🛑 New SL: {new_sl}")
                
            else:
                if len(db) >= s["max_tokens_before_consolidation"]: continue
                qty=float(exchange.amount_to_precision(cs, sp/p))
                exchange.create_limit_buy_order(cs, qty, p); sl=d['sl']; 
                
                db[sym]={"entry_price":p,"safe_zone":p,"stop_loss":sl,"took_profit_10":False,"took_profit_50":False,"dca_count":0,"sl_hit":False}
                save(DB_FILE,db)
                
                sl_qty = qty * s.get('sl_sell_percent', 0.5)
                hard_sl(sym, sl_qty, sl)
                tg(f"🛒 *EXECUTED!*\n{sym} | 💵 {sp:.2f} USDT\n🛑 SL: {sl}")

            del q[sym]; save(QUEUE_FILE,q); ub-=sp
            time.sleep(2)
    except Exception as e: 
        tg(f"⚠️ *QUEUE BUY FAILED:* `{e}`")

def scan():
    db=load(DB_FILE); q=load(QUEUE_FILE); s=settings()
    if not s["auto_buy_enabled"]: return 
    _,_,br,_,_=data("BTC-USDT")
    if br and br<35: return 
    if len(db)>s["max_tokens_before_consolidation"]: consolidate(); db=load(DB_FILE)
    try: tk=exchange.fetch_tickers(); mk=exchange.load_markets()
    except: return
    v=[]
    for sym,t in tk.items():
        if sym.endswith('/USDT'):
            b=sym.split('/')[0]
            if b in STABLECOINS or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV) or (sym in mk and not mk[sym].get('spot',False)): continue
            vol=t['quoteVolume'] or 0
            if vol>=1000000: v.append({'sym':sym.replace("/","-"),'vol':vol,'chg':t['percentage'] or 0})
    v.sort(key=lambda x:x['vol'], reverse=True); ct=int(time.time()); qu=False
    for sym,d in list(q.items()):
        if ct-d.get('timestamp',0)>86400: del q[sym]; qu=True
        
    for t in v[15:50]:
        p,pr,r,ma5,ma10=data(t['sym'])
        if not p: continue
        setup=None; sl=0
        if t['chg']>1.0 and s['rsi_min']<=r<=s['rsi_max'] and p>ma5>ma10: setup="TREND"; sl=round(ma5*(1-s['stop_loss_pct']/100),6)
        elif t['chg']<-8.0 and r<30 and p>pr: setup="OVERSOLD"; sl=round(p*0.95,6)
        if setup:
            rr=(p*0.10)/(p-sl) if (p-sl)>0 else 0; sc=(t['vol']/1e6)+(rr*10)
            if t['sym'] not in q or q[t['sym']].get('score',0)<sc:
                if t['sym'] not in q: tg(f"📥 *QUEUED:* {t['sym']}\nScore: {sc:.1f}")
                q[t['sym']]={"timestamp":ct,"type":setup,"sl":sl,"score":sc}; qu=True
    if qu: save(QUEUE_FILE,q)

def fmt_price(p):
    if p >= 100: return f"{p:.2f}"
    elif p >= 1: return f"{p:.4f}"
    else: return f"{p:.6f}"

def format_table(headers, rows, widths):
    top = "+" + "+".join(["-" * (w + 2) for w in widths]) + "+"
    header_str = "| " + " | ".join([h.ljust(w) for h, w in zip(headers, widths)]) + " |"
    mid = "+" + "+".join(["-" * (w + 2) for w in widths]) + "+"
    row_strs = ["| " + " | ".join([str(val).ljust(w) for val, w in zip(r, widths)]) + " |" for r in rows]
    bot = "+" + "+".join(["-" * (w + 2) for w in widths]) + "+"
    return "\n".join([top, header_str, mid] + row_strs + [bot])

def gl():
    try: tk=exchange.fetch_tickers()
    except: return "❌ Error", "❌ Error"
    v=[]
    for sym,t in tk.items():
        if sym.endswith('/USDT'):
            b=sym.split('/')[0]
            if b in STABLECOINS or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV): continue
            vol=t['quoteVolume'] or 0
            if vol>=1000000: v.append({'sym':b,'chg':t['percentage'] or 0,'price':t['last'] or 0})
    v.sort(key=lambda x:x['chg'], reverse=True)
    
    def build(title, data):
        rows = [[x['sym'], fmt_price(x['price']), f"{x['chg']:+.1f}%"] for x in data]
        table = format_table(["Coin", "Price", "Change"], rows, [8, 12, 8])
        return f"*{title}*\n```\n{table}\n```"

    return build("🚀 GAINERS (24H)", v[:10]), build("🩸 LOSERS (24H)", v[-10:][::-1])

def tg_check():
    st=load(STATE_FILE); lid=st.get("last_update_id",0)
    try:
        res=requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={lid+1}", timeout=10).json()
        if res.get("ok") and res.get("result"):
            for upd in res["result"]:
                lid=upd["update_id"]
                if "message" in upd and "date" in upd["message"] and int(time.time())-upd["message"]["date"]>900: continue
                if "message" in upd and "text" in upd["message"]:
                    txt=upd["message"]["text"].strip()
                    low=txt.lower()
                    
                    if low.startswith("/ai"):
                        ask_ai(txt[3:].strip())
                    elif low=="/help":
                        tg("🤖 *COMMANDS*\n`/ai [question]` - Ask AI\n`/analyze SOL`\n`/gainers` / `/losers`\n`/queue` / `/balance`\n`/audit` / `/status`\n`/buy SOL 5.75`\n`/dca SOL 5.75`\n`/set sl_sell_percent 0.5`\n`/set dca_reserve 2.0`")
                    
                    elif low=="/balance":
                        b=exchange.fetch_balance()['free']; u=b.get('USDT',0); db=load(DB_FILE)
                        rows = [["USDT", f"{u:.2f}", f"${u:.2f}"]]
                        for c,a in b.items():
                            if a>0 and c not in ['USDT','USDC']:
                                p,_,_,_,_=data(f"{c}-USDT")
                                if p and a*p>=0.10: rows.append([c, f"{a:.4f}", f"${a*p:.2f}"])
                        table = format_table(["Asset", "Amount", "Est. Val"], rows, [7, 10, 9])
                        tg(f"💰 *BALANCE*\n```\n{table}\n```")
                        
                    elif low=="/queue":
                        q=load(QUEUE_FILE)
                        if not q: tg("📥 Queue empty.")
                        else:
                            sq=sorted(q.items(), key=lambda x:x[1].get('score',0), reverse=True)
                            rows = [[s, f"{d.get('score',0):.1f}", d.get('type','N/A')] for s,d in sq]
                            table = format_table(["Coin", "Score", "Setup"], rows, [7, 6, 8])
                            tg(f"📥 *PRIORITY QUEUE*\n```\n{table}\n```")
                            
                    elif "/status" in low:
                        db=load(DB_FILE)
                        if not db: tg("🛑 No active trades.")
                        else:
                            rows = []
                            for s,d in db.items():
                                p,_,r,_,_=data(s)
                                if p:
                                    pnl=((p-d['entry_price'])/d['entry_price'])*100
                                    rows.append([s.split('-')[0], fmt_price(d['entry_price']), fmt_price(p), f"{pnl:+.1f}%"])
                            table = format_table(["Coin", "Entry", "Current", "PnL"], rows, [7, 8, 8, 7])
                            tg(f"🟢 *ACTIVE TRADES*\n```\n{table}\n```")
                            
                    elif low=="/audit":
                        b=exchange.fetch_balance()['free']; u=b.get('USDT',0); q=load(QUEUE_FILE); s=settings()
                        rows = [
                            ["API Status", "OK 🟢"],
                            ["USDT Balance", f"{u:.2f}"],
                            ["Active Trades", f"{len(load(DB_FILE))}"],
                            ["Queue Size", f"{len(q)}"],
                            ["RSI Range", f"{s['rsi_min']}-{s['rsi_max']}"],
                            ["Stop Loss", f"{s['stop_loss_pct']}%"],
                            ["SL Sell %", f"{s.get('sl_sell_percent', 0.5)*100}%"],
                            ["DCA Reserve", f"${s.get('dca_reserve', 2.0)}"],
                            ["Auto-Buy", f"${s['auto_buy_amount_usdt']}"]
                        ]
                        table = format_table(["Metric", "Value"], rows, [12, 10])
                        tg(f"🛠️ *SYSTEM AUDIT*\n```\n{table}\n```")
                        
                    elif low=="/pnl":
                        L=load(LEDGER_FILE); p=L.get('total_realized_usdt',0.0); c=L.get('trade_count',0)
                        rows = [
                            ["Total PnL", f"${p:.2f}"],
                            ["Trades Closed", f"{c}"]
                        ]
                        table = format_table(["Metric", "Value"], rows, [12, 10])
                        tg(f"📊 *PROFIT & LOSS*\n```\n{table}\n```")
                        
                    elif low.startswith("/analyze"):
                        p=txt.split()
                        if len(p)>1:
                            c=p[1].upper(); pr,_,r,ma5,ma10=data(f"{c}-USDT")
                            tg(f"🧠 *{c}*\nPrice: {pr}\nRSI: {r}\nMA5: {ma5}\nMA10: {ma10}")
                    elif low=="/gainers":
                        g,_=gl(); tg(g)
                    elif low=="/losers":
                        _,l=gl(); tg(l)
                    elif low.startswith("/buy"):
                        p=txt.split()
                        if len(p)==3:
                            c=p[1].upper(); amt=float(p[2]); sym=f"{c}-USDT"; db=load(DB_FILE)
                            if sym not in db:
                                pr=exchange.fetch_ticker(f"{c}/USDT")['last']; qty=float(exchange.amount_to_precision(f"{c}/USDT", amt/pr))
                                exchange.create_limit_buy_order(f"{c}/USDT", qty, pr); sl=round(pr*(1-settings()['stop_loss_pct']/100),6)
                                db[sym]={"entry_price":pr,"safe_zone":pr,"stop_loss":sl,"took_profit_10":False,"took_profit_50":False,"dca_count":0,"sl_hit":False}
                                save(DB_FILE,db); 
                                sl_qty = qty * settings().get('sl_sell_percent', 0.5)
                                hard_sl(sym, sl_qty, sl); tg(f"✅ *BOUGHT:* {c} | SL: {sl}")
                    elif low.startswith("/dca"):
                        p=txt.split()
                        if len(p)==3:
                            c=p[1].upper(); amt=float(p[2]); sym=f"{c}-USDT"; db=load(DB_FILE)
                            if sym not in db: tg(f"❌ You don't have an active bot trade for {c}.")
                            else:
                                try:
                                    pr=exchange.fetch_ticker(f"{c}/USDT")['last']; aq=float(exchange.amount_to_precision(f"{c}/USDT", amt/pr))
                                    exchange.create_limit_buy_order(f"{c}/USDT", aq, pr); bal=exchange.fetch_balance()['free'].get(c,0); oq=bal-aq
                                    ne=pr if oq<=0 else ((oq*db[sym]['entry_price'])+amt)/bal
                                    sl=round(ne*(1-settings()['stop_loss_pct']/100),6)
                                    db[sym].update({"entry_price":round(ne,6),"stop_loss":sl,"took_profit_10":False,"took_profit_50":False,"dca_count":db[sym].get('dca_count',0)+1,"sl_hit":False})
                                    save(DB_FILE,db); 
                                    sl_qty = bal * settings().get('sl_sell_percent', 0.5)
                                    hard_sl(sym,sl_qty,sl); tg(f"🛒 *DCA:* {c} | {amt} USDT\n📉 New Entry: {ne:.6f}\n🛑 SL: {sl}")
                                except Exception as e: tg(f"❌ *DCA FAILED:* {e}")
                st["last_update_id"]=lid; save(STATE_FILE,st)
    except Exception as e: print(f"TG Err: {e}")

if __name__ == "__main__":
    try:
        tg_check(); manage(); scan(); exec_queue()
        subprocess.run(["git","pull","--rebase"],capture_output=True)
        subprocess.run(["git","add","."],capture_output=True)
        subprocess.run(["git","commit","-m","Auto"],capture_output=True)
        subprocess.run(["git","push"],capture_output=True)
    except Exception as e: tg(f"🚨 *CRASH:* {e}\n`{traceback.format_exc()[-100:]}`")
