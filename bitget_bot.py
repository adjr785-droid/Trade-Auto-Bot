import requests, json, os, time, ccxt, subprocess, traceback
import google.generativeai as genai
TG_TOKEN=os.environ.get('TG_TOKEN'); TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
DB_FILE="positions.json"; STATE_FILE="tg_state.json"; SETTINGS_FILE="settings.json"; QUEUE_FILE="queue.json"; LEDGER_FILE="profit_ledger.json"
STABLECOINS=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE']; LEV=['3L','3S','5L','5S','10L','10S','2L','2S']; BL=['RPANW','CT','ONE']
exchange=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True})
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))

def tg(m):
    try: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id":TG_CHAT_ID,"text":m,"parse_mode":"Markdown"}, timeout=10)
    except: pass

def load(f, d={}):
    try: return json.load(open(f)) if os.path.exists(f) else d
    except: return d

def save(f, d): json.dump(d, open(f, "w"), indent=4)

def settings():
    d={"rsi_min":42,"rsi_max":48,"stop_loss_pct":15,"auto_buy_enabled":True,"auto_buy_amount_usdt":5.75,"max_tokens_before_consolidation":3}
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
        context = f"System Data:\n- USDT Balance: {bal}\n- BTC RSI: {btc_rsi}\n- Active Trades: {db}\n- Queue: {q}\n- Bot Rules: RSI {s['rsi_min']}-{s['rsi_max']}, SL {s['stop_loss_pct']}%\n\n"
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(f"You are an expert crypto trading analyst. Use this live data to answer the user.\n{context}\nUser Question: {prompt}")
        tg(f"🧠 *AI ANALYST:*\n\n{response.text}")
    except Exception as e: tg(f"❌ AI Error: {e}")

def hard_sl(sym, qty, pr):
    try:
        try:
            for o in exchange.fetch_open_orders(sym.replace("-","/")):
                if o['side']=='sell': exchange.cancel_order(o['id'], sym.replace("-","/"))
        except: pass
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
    db=load(DB_FILE)
    for sym,d in list(db.items()):
        p,pr,r,ma5,ma10=data(sym)
        if not p: continue
        inc=((p-d["entry_price"])/d["entry_price"])*100
        if p<=d["stop_loss"]: 
            if sell(sym,1.0,"STOP LOSS"): del db[sym]; save(DB_FILE,db)
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
    if len(db)<s["max_tokens_before_consolidation"]:
        try:
            ub=exchange.fetch_balance()['free'].get('USDT',0)
            for sym,d in sq:
                if len(db)>=s["max_tokens_before_consolidation"]: break
                if sym in db: del q[sym]; save(QUEUE_FILE,q); continue
                p,_,_,_,_=data(sym)
                if not p: continue
                sp=min(ub, s['auto_buy_amount_usdt'])
                if sp>=1.02:
                    cs=sym.replace("-","/"); qty=float(exchange.amount_to_precision(cs, sp/p))
                    exchange.create_market_buy_order(cs, qty); sl=d['sl']; hard_sl(sym,qty,sl)
                    db[sym]={"entry_price":p,"safe_zone":p,"stop_loss":sl,"took_profit_10":False,"took_profit_50":False}
                    save(DB_FILE,db); del q[sym]; save(QUEUE_FILE,q); ub-=sp
                    tg(f"🛒 *EXECUTED!*\n{sym} | 💵 {sp:.2f} USDT\n🛑 SL: {sl}")
                    time.sleep(2)
        except: pass

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
        if t['sym'] in db: continue 
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

def tg_check():
    st=load(STATE_FILE); lid=st.get("last_update_id",0)
    try:
        res=requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={lid+1}", timeout=10).json()
        if res.get("ok") and res.get("result"):
            for u in res["result"]:
                lid=u["update_id"]
                if "message" in u and "date" in u["message"] and int(time.time())-u["message"]["date"]>900: continue
                if "message" in u and "text" in u["message"]:
                    txt=u["message"]["text"].strip()
                    low=txt.lower()
                    
                    if low.startswith("/ai"):
                        ask_ai(txt[3:].strip())
                    elif low=="/help":
                        tg("🤖 *COMMANDS*\n`/ai [question]` - Ask AI\n`/analyze SOL`\n`/gainers` / `/losers`\n`/queue` / `/balance`\n`/audit` / `/status`\n`/buy SOL 5.75`\n`/dca SOL 5.75`")
                    elif low=="/balance":
                        b=exchange.fetch_balance()['free']; u=b.get('USDT',0)
                        tg(f"💰 *BALANCE:* {u:.2f} USDT")
                    elif low=="/clear":
                        save(DB_FILE,{}); save(QUEUE_FILE,{}); tg("🧹 *CLEARED!*")
                    elif low=="/queue":
                        q=load(QUEUE_FILE)
                        tg("📥 *QUEUE*\n"+"\n".join([f"🪙 {s} (Score: {d.get('score',0):.1f})" for s,d in q.items()]) if q else "Empty")
                    elif low.startswith("/analyze"):
                        p=txt.split()
                        if len(p)>1:
                            c=p[1].upper(); pr,_,r,ma5,ma10=data(f"{c}-USDT")
                            tg(f"🧠 *{c}*\nPrice: {pr}\nRSI: {r}\nMA5: {ma5}\nMA10: {ma10}")
                    elif low=="/gainers":
                        tk=exchange.fetch_tickers(); v=[(s.split('/')[0],t['percentage']) for s,t in tk.items() if s.endswith('/USDT') and t['percentage']]
                        v.sort(key=lambda x:x[1], reverse=True); tg("🚀 *GAINERS*\n"+"\n".join([f"{i}. {x[0]}: {x[1]:+.1f}%" for i,x in enumerate(v[:10],1)]))
                    elif low=="/losers":
                        tk=exchange.fetch_tickers(); v=[(s.split('/')[0],t['percentage']) for s,t in tk.items() if s.endswith('/USDT') and t['percentage']]
                        v.sort(key=lambda x:x[1]); tg("🩸 *LOSERS*\n"+"\n".join([f"{i}. {x[0]}: {x[1]:+.1f}%" for i,x in enumerate(v[:10],1)]))
                    elif "/status" in low:
                        db=load(DB_FILE)
                        tg("🟢 *ACTIVE*\n"+"\n".join([f"{s}: {d['entry_price']}" for s,d in db.items()]) if db else "None")
                    elif low.startswith("/buy"):
                        p=txt.split()
                        if len(p)==3:
                            c=p[1].upper(); amt=float(p[2]); sym=f"{c}-USDT"; db=load(DB_FILE)
                            if sym not in db:
                                pr=exchange.fetch_ticker(f"{c}/USDT")['last']; qty=float(exchange.amount_to_precision(f"{c}/USDT", amt/pr))
                                exchange.create_market_buy_order(f"{c}/USDT", qty); sl=round(pr*(1-settings()['stop_loss_pct']/100),6)
                                db[sym]={"entry_price":pr,"safe_zone":pr,"stop_loss":sl,"took_profit_10":False,"took_profit_50":False}
                                save(DB_FILE,db); hard_sl(sym, qty, sl); tg(f"✅ *BOUGHT:* {c} | SL: {sl}")
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
