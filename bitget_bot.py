import requests,json,os,time,ccxt,subprocess,traceback,re
from google import genai
TG_TOKEN=os.environ.get('TG_TOKEN');TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
DB="positions.json";ST="tg_state.json";CFG="settings.json";QF="queue.json";LF="profit_ledger.json";CD=".cmd_cooldown.json"
STABLE=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE'];LEV=['3L','3S','5L','5S','10L','10S','2L','2S'];BL=['RPANW','CT','ONE']
ex=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True})
client=genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
_C={};_T=20
def _send(m,mode):
    try:
        r=requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json={"chat_id":TG_CHAT_ID,"text":m,"parse_mode":mode},timeout=10)
        if not r.ok:
            c=re.sub(r'<[^>]+>','',m) if mode=="HTML" else m
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json={"chat_id":TG_CHAT_ID,"text":c},timeout=10)
    except:pass
def tg(m):_send(m,"Markdown")
def tg_ai(m):_send(m,"HTML")
def load(f,d=None):
    d={} if d is None else d
    try:
        if os.path.exists(f):
            with open(f) as fh:return json.load(fh)
    except:pass
    return d
def save(f,d):
    try:
        with open(f,"w") as fh:json.dump(d,fh,indent=4)
    except Exception as e:print(f"save {f}:{e}")
def cfg():
    d={"rsi_min":42,"rsi_max":48,"stop_loss_pct":15,"sl_sell_percent":0.5,"dca_reserve":2.0,"auto_buy_enabled":True,"auto_buy_amount_usdt":5.75,"max_tokens_before_consolidation":3}
    s=load(CFG,d)
    for k,v in d.items():s.setdefault(k,v)
    return s
def in_cd(k,s=30):return (time.time()-load(CD,{}).get(k,0))<s
def mk_cd(k):d=load(CD,{});d[k]=time.time();save(CD,d)
def cd(k,s=30):
    if in_cd(k,s):return True
    mk_cd(k);return False
def _calc(sym):
    try:
        o=ex.fetch_ohlcv(sym.replace("-","/"),'1h',limit=50);c=[float(k[4]) for k in o[:-1]]
        g=[max(0,c[i]-c[i-1]) for i in range(1,len(c))];l=[max(0,c[i-1]-c[i]) for i in range(1,len(c))]
        ag=sum(g[-14:])/14;al=sum(l[-14:])/14;r=100.0 if al==0 else round(100-(100/(1+(ag/al))),2)
        return c[-1],c[-2],r,round(sum(c[-5:])/5,6),round(sum(c[-10:])/10,6)
    except:return None,None,None,None,None
def data(sym):
    h=_C.get(sym)
    if h and (time.time()-h[0])<_T:return h[1]
    r=_calc(sym);_C[sym]=(time.time(),r);return r
def live(sym):
    try:return ex.fetch_ticker(sym.replace("-","/"))['last']
    except:return None
def clr():_C.clear()
def cancel_sells(sym):
    try:
        for o in ex.fetch_open_orders(sym.replace("-","/")):
            if o['side']=='sell':ex.cancel_order(o['id'],sym.replace("-","/"))
    except:pass
def m_buy(cs,usdt):
    p=live(cs.replace("/","-"))
    if not p:raise Exception(f"No price {cs}")
    q=float(ex.amount_to_precision(cs,usdt/p));o=ex.create_market_buy_order(cs,q)
    f,a=o.get('filled'),o.get('average')
    if not f or not a:
        time.sleep(1)
        try:o2=ex.fetch_order(o['id'],cs);f=o2.get('filled',q);a=o2.get('average',p)
        except:f,a=q,p
    return float(f),float(a)
def hard_sl(sym,qty,pr):
    cs=sym.replace("-","/")
    try:
        cancel_sells(sym)
        if qty<=0:return True
        ex.create_order(cs,'market','sell',qty,None,{'triggerPrice':pr,'triggerType':'market_price','stopLossPrice':pr});return True
    except Exception as e:tg(f"⚠️ *SL FAILED* {sym}@{pr}:`{e}`");return False
def sell(sym,pct,reason):
    try:
        cs=sym.replace("-","/");coin=sym.split("-")[0];cancel_sells(sym);time.sleep(0.5)
        fb=ex.fetch_balance()['free'].get(coin,0)
        if fb<=0:return True
        p=live(sym)
        if not p:return False
        q=fb*pct
        if q*p<1.02 and pct<1.0:q=fb;reason+=" (dust→full)"
        q=float(ex.amount_to_precision(cs,q))
        if q<=0:return True
        ex.create_market_sell_order(cs,q);db=load(DB)
        if sym in db:
            pnl=(p-db[sym]["entry_price"])*q;L=load(LF)
            L['total_realized_usdt']=L.get('total_realized_usdt',0.0)+pnl;L['trade_count']=L.get('trade_count',0)+1;save(LF,L)
            tg(f"✅ *SOLD ({reason})*\n🪙 {q} {sym}\n{'🟢' if pnl>=0 else '🔴'} PnL: ${pnl:.2f}")
        return True
    except Exception as e:tg(f"❌ *SELL FAILED* {sym}:`{e}`");return False
def ask_ai(p):
    try:
        db=load(DB);q=load(QF);s=cfg();bal=ex.fetch_balance()['free'].get('USDT',0);_,_,br,_,_=data("BTC-USDT")
        pos=[f"  {sym}:entry={d['entry_price']},pnl={((live(sym) or d['entry_price'])-d['entry_price'])/d['entry_price']*100:+.1f}%" for sym,d in list(db.items())[:5]] or ["  (none)"]
        ctx=f"USDT:{bal:.2f}|AutoBuy:${s['auto_buy_amount_usdt']}|BTC RSI:{br}|Pos({len(db)}):\n"+"\n".join(pos)+f"\nQueue:{len(q)}|RSI {s['rsi_min']}-{s['rsi_max']},SL {s['stop_loss_pct']}%\n\n"
        r=client.models.generate_content(model='gemini-2.5-flash',contents=f"Expert crypto analyst. Use Telegram HTML (<b>,<i>), newlines NOT <br>.\n{ctx}Q:{p}")
        tg_ai(f"🧠 <b>AI:</b>\n\n{r.text or '(empty)'}")
    except Exception as e:tg(f"❌ AI Error:{e}")
def manage():
    db=load(DB);s=cfg()
    for sym,d in list(db.items()):
        _,_,r,_,_=data(sym);lv=live(sym)
        if not lv:continue
        inc=((lv-d["entry_price"])/d["entry_price"])*100;coin=sym.split('-')[0]
        if lv<=d["stop_loss"]:
            if not d.get("sl_hit"):
                pct=s.get('sl_sell_percent',0.5);tot=ex.fetch_balance()['total'].get(coin,0)
                if (tot*pct*lv)<1.02:pct=1.0
                if sell(sym,pct,"PARTIAL SL"):
                    rem=ex.fetch_balance()['total'].get(coin,0)
                    if rem<=0 or (rem*lv)<1.02:db.pop(sym,None);save(DB,db);continue
                    db[sym]["sl_hit"]=True;ns=round(lv*(1-s['stop_loss_pct']/100),6);db[sym]["stop_loss"]=ns
                    save(DB,db);hard_sl(sym,rem,ns);tg(f"⚠️ *PARTIAL SL!*\nSold {pct*100:.0f}% {sym}. Rem {rem:.6f}@{ns}.")
            else:
                if sell(sym,1.0,"FINAL SL"):db.pop(sym,None);save(DB,db)
            continue
        if r and r>=75:
            if sell(sym,1.0,"RSI 75"):db.pop(sym,None);save(DB,db)
        elif r and r>=70 and not d.get("took_profit_50"):
            if sell(sym,0.5,"RSI 70") and sym in db:db[sym]["took_profit_50"]=True;save(DB,db)
        elif inc>=10.0 and not d.get("took_profit_10"):
            if sell(sym,0.25,"+10%") and sym in db:db[sym]["took_profit_10"]=True;save(DB,db)
def consolidate():
    db=load(DB)
    if len(db)<=2:return
    tg("⚖️ *CONSOLIDATING...*")
    rk=[{"s":sym,"pnl":((p-d['entry_price'])/d['entry_price'])*100} for sym,d in db.items() if (p:=live(sym)) and "entry_price" in d]
    if not rk:return
    rk.sort(key=lambda x:x["pnl"],reverse=True)
    for t in rk[2:]:
        if sell(t['s'],1.0,"Consolidation"):db.pop(t['s'],None);save(DB,db)
def exec_q():
    q=load(QF);db=load(DB);s=cfg()
    if not q:return
    sq=sorted(q.items(),key=lambda x:x[1].get('score',0),reverse=True)
    try:
        ub=ex.fetch_balance()['free'].get('USDT',0)
        for sym,qd in sq:
            if in_cd(f"fail_{sym}",300):continue
            p=live(sym)
            if not p:continue
            is_dca=sym in db;spend=ub if is_dca else (ub-s.get('dca_reserve',2.0))
            if spend<1.02 and not is_dca and db:
                wk=None;low=999
                for hs,hd in db.items():
                    hp=live(hs)
                    if hp:
                        pnl=((hp-hd['entry_price'])/hd['entry_price'])*100
                        if pnl<low:low=pnl;wk=hs
                if wk:
                    bc=wk.split('-')[0];cancel_sells(wk);time.sleep(0.5)
                    hb=ex.fetch_balance()['free'].get(bc,0);hp=live(wk)
                    if hb>0 and hp:
                        v=min(s['auto_buy_amount_usdt'],hb*hp);qs=float(ex.amount_to_precision(wk.replace("-","/"),v/hp))
                        if qs*hp<(hb*hp)*0.95:
                            ex.create_market_sell_order(wk.replace("-","/"),qs);tg(f"🔄 *PARTIAL SWAP:* Sold {qs} {bc} for {sym}.")
                            time.sleep(2);ub=ex.fetch_balance()['free'].get('USDT',0);spend=ub-s.get('dca_reserve',2.0)
                            rem=ex.fetch_balance()['total'].get(bc,0)
                            if rem>0 and (rem*hp)>1.02:hard_sl(wk,rem,db[wk]['stop_loss'])
                            else:db.pop(wk,None);save(DB,db)
            if spend<1.02:q.pop(sym,None);save(QF,q);continue
            sp=min(spend,s['auto_buy_amount_usdt']);cs=sym.replace("-","/");coin=sym.split('-')[0]
            if is_dca:
                if db[sym].get('dca_count',0)>=2:q.pop(sym,None);save(QF,q);continue
                old=ex.fetch_balance()['total'].get(coin,0)
                try:f,a=m_buy(cs,sp)
                except Exception as e:tg(f"❌ DCA fail {sym}:{e}");mk_cd(f"fail_{sym}");continue
                tot=old+f;oe=db[sym]['entry_price'];ne=((old*oe)+(f*a))/tot if tot>0 else a
                sl=round(ne*(1-s['stop_loss_pct']/100),6)
                db[sym].update({'entry_price':round(ne,6),'stop_loss':sl,'took_profit_10':False,'took_profit_50':False,'dca_count':db[sym].get('dca_count',0)+1,'sl_hit':False})
                save(DB,db);hard_sl(sym,tot*s.get('sl_sell_percent',0.5),sl);tg(f"🛒 *DCA!*\n{sym}|💵{sp:.2f}@{a:.6f}\n📉Entry:{ne:.6f}\n🛑SL:{sl}")
            else:
                if len(db)>=s["max_tokens_before_consolidation"]:continue
                try:f,a=m_buy(cs,sp)
                except Exception as e:tg(f"❌ Buy fail {sym}:{e}");mk_cd(f"fail_{sym}");continue
                maxsl=round(a*(1-s['stop_loss_pct']/100),6);sl=qd.get('sl') or maxsl
                if sl>=a or sl>maxsl:sl=maxsl
                db[sym]={"entry_price":round(a,6),"safe_zone":round(a,6),"stop_loss":sl,"took_profit_10":False,"took_profit_50":False,"dca_count":0,"sl_hit":False}
                save(DB,db);hard_sl(sym,f*s.get('sl_sell_percent',0.5),sl);tg(f"🛒 *EXECUTED!*\n{sym}|💵{sp:.2f}@{a:.6f}\n🛑SL:{sl}")
            q.pop(sym,None);save(QF,q);ub=ex.fetch_balance()['free'].get('USDT',0);time.sleep(2)
    except Exception as e:tg(f"⚠️ *QUEUE FAILED:* `{e}`")
def scan():
    db=load(DB);q=load(QF);s=cfg()
    if not s["auto_buy_enabled"]:return
    _,_,br,_,_=data("BTC-USDT")
    if br and br<35:return
    if len(db)>=s["max_tokens_before_consolidation"]:consolidate();db=load(DB)
    try:tk=ex.fetch_tickers();mk=ex.load_markets()
    except:return
    v=[]
    for sym,t in tk.items():
        if not sym.endswith('/USDT'):continue
        b=sym.split('/')[0]
        if (b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV) or (sym in mk and not mk[sym].get('spot',False))):continue
        vol=t['quoteVolume'] or 0
        if vol>=1000000:v.append({'sym':sym.replace("/","-"),'vol':vol,'chg':t['percentage'] or 0})
    v.sort(key=lambda x:x['vol'],reverse=True);ct=int(time.time());qu=False
    for sym,d in list(q.items()):
        if ct-d.get('timestamp',0)>86400:q.pop(sym,None);qu=True
    for t in v[15:50]:
        p,pr,r,ma5,ma10=data(t['sym'])
        if not p or r is None:continue
        setup=sl=None
        if t['chg']>1.0 and s['rsi_min']<=r<=s['rsi_max'] and p>ma5>ma10:setup="TREND";sl=round(ma5*(1-s['stop_loss_pct']/100),6)
        elif t['chg']<-8.0 and r<30 and p>pr:setup="OVERSOLD";sl=round(p*(1-s['stop_loss_pct']/100),6)
        if setup:
            rr=(p*0.10)/(p-sl) if (p-sl)>0 else 0;sc=(t['vol']/1e6)+(rr*10)
            if t['sym'] not in q or q[t['sym']].get('score',0)<sc:
                if t['sym'] not in q:tg(f"📥 *QUEUED:* {t['sym']}\nScore:{sc:.1f}")
                q[t['sym']]={"timestamp":ct,"type":setup,"sl":sl,"score":sc};qu=True
    if qu:save(QF,q)
def fp(p):
    if p is None:return "N/A"
    if p>=100:return f"{p:.2f}"
    if p>=1:return f"{p:.4f}"
    if p>=0.001:return f"{p:.6f}"
    return f"{p:.8f}"
def tbl(h,r,w):
    bar="+"+"+".join(["-"*(x+2) for x in w])+"+"
    hd="| "+" | ".join([a.ljust(b) for a,b in zip(h,w)])+" |"
    bd=["| "+" | ".join([str(v).ljust(x) for v,x in zip(rw,w)])+" |" for rw in r]
    return "\n".join([bar,hd,bar]+bd+[bar])
def gl():
    try:tk=ex.fetch_tickers()
    except:return "❌","❌"
    v=[]
    for sym,t in tk.items():
        if not sym.endswith('/USDT'):continue
        b=sym.split('/')[0]
        if (b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV)):continue
        if (t['quoteVolume'] or 0)>=1000000:v.append({'sym':b,'chg':t['percentage'] or 0,'price':t['last'] or 0})
    v.sort(key=lambda x:x['chg'],reverse=True)
    def bd(title,d):
        rows=[[x['sym'],fp(x['price']),f"{x['chg']:+.1f}%"] for x in d]
        return f"*{title}*\n```\n{tbl(['Coin','Price','Change'],rows,[8,12,8])}\n```"
    return bd("🚀 GAINERS (24H)",v[:10]),bd("🩸 LOSERS (24H)",v[-10:][::-1])
def cmd_buy(txt,is_dca):
    parts=txt.split()
    if len(parts)!=3:return
    c=parts[1].upper()
    try:amt=float(parts[2])
    except:tg("❌ Invalid amount.");return
    sym=f"{c}-USDT";tag="dca" if is_dca else "buy"
    if cd(f"{tag}:{sym}",30):tg(f"⏳ Already processing /{tag} {c}.");return
    db=load(DB)
    if is_dca and sym not in db:tg(f"❌ No active trade for {c}.");return
    if not is_dca and sym in db:tg(f"❌ Active position for {c}.");return
    ub=ex.fetch_balance()['free'].get('USDT',0)
    if ub<amt:tg(f"❌ *{'DCA' if is_dca else 'BUY'} FAILED:* Only ${ub:.2f} USDT.");return
    try:
        old=ex.fetch_balance()['total'].get(c,0);f,a=m_buy(f"{c}/USDT",amt);tot=old+f
        if is_dca:
            oe=db[sym]['entry_price'];ne=((old*oe)+(f*a))/tot if tot>0 else a
            sl=round(ne*(1-cfg()['stop_loss_pct']/100),6)
            db[sym].update({"entry_price":round(ne,6),"stop_loss":sl,"took_profit_10":False,"took_profit_50":False,"dca_count":db[sym].get('dca_count',0)+1,"sl_hit":False})
        else:
            ne=a;sl=round(a*(1-cfg()['stop_loss_pct']/100),6)
            db[sym]={"entry_price":round(a,6),"safe_zone":round(a,6),"stop_loss":sl,"took_profit_10":False,"took_profit_50":False,"dca_count":0,"sl_hit":False}
        save(DB,db);hard_sl(sym,tot*cfg().get('sl_sell_percent',0.5),sl)
        tg(f"{'🛒 *DCA:*' if is_dca else '✅ *BOUGHT:*'} {c} @ {a:.6f}\n📉 Entry:{ne:.6f}\n🛑 SL:{sl}")
    except Exception as e:tg(f"❌ *{'DCA' if is_dca else 'BUY'} FAILED:* {e}")
def tg_check():
    st=load(ST);lid=st.get("last_update_id",0)
    try:
        res=requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={lid+1}",timeout=10).json()
        if not (res.get("ok") and res.get("result")):return
        for upd in res["result"]:
            lid=upd["update_id"]
            if "message" not in upd or "text" not in upd["message"]:continue
            if "date" in upd["message"] and int(time.time())-upd["message"]["date"]>900:continue
            txt=upd["message"]["text"].strip();low=txt.lower()
            if low.startswith("/ai"):ask_ai(txt[3:].strip())
            elif low=="/help":tg("🤖 *CMDS*\n`/ai [q]` `/analyze SOL`\n`/gainers` `/losers`\n`/queue` `/balance`\n`/audit` `/status` `/pnl`\n`/buy SOL 5.75`\n`/dca SOL 5.75`\n`/set sl_sell_percent 0.5`\n`/set dca_reserve 2.0`")
            elif low=="/balance":
                b=ex.fetch_balance()['free'];u=b.get('USDT',0);rows=[["USDT",f"{u:.2f}",f"${u:.2f}"]]
                for c,a in b.items():
                    if a>0 and c not in ['USDT','USDC']:
                        p=live(f"{c}-USDT")
                        if p and a*p>=0.10:rows.append([c,f"{a:.4f}",f"${a*p:.2f}"])
                tg(f"💰 *BALANCE*\n```\n{tbl(['Asset','Amount','Est.Val'],rows,[7,10,9])}\n```")
            elif low=="/queue":
                q=load(QF)
                if not q:tg("📥 Queue empty.")
                else:
                    sq=sorted(q.items(),key=lambda x:x[1].get('score',0),reverse=True)
                    rows=[[s,f"{d.get('score',0):.1f}",d.get('type','N/A')] for s,d in sq]
                    tg(f"📥 *QUEUE*\n```\n{tbl(['Coin','Score','Setup'],rows,[7,6,8])}\n```")
            elif "/status" in low:
                db=load(DB)
                if not db:tg("🛑 No active trades.")
                else:
                    rows=[[s.split('-')[0],fp(d['entry_price']),fp(p),f"{((p-d['entry_price'])/d['entry_price'])*100:+.1f}%"] for s,d in db.items() if (p:=live(s))]
                    tg(f"🟢 *ACTIVE*\n```\n{tbl(['Coin','Entry','Current','PnL'],rows,[7,8,8,7])}\n```")
            elif low=="/audit":
                u=ex.fetch_balance()['free'].get('USDT',0);q=load(QF);s=cfg()
                rows=[["API","OK 🟢"],["USDT",f"{u:.2f}"],["Active",f"{len(load(DB))}"],["Queue",f"{len(q)}"],["RSI",f"{s['rsi_min']}-{s['rsi_max']}"],["SL",f"{s['stop_loss_pct']}%"],["SL Sell",f"{s.get('sl_sell_percent',0.5)*100}%"],["Reserve",f"${s.get('dca_reserve',2.0)}"],["AutoBuy",f"${s['auto_buy_amount_usdt']}"]]
                tg(f"🛠️ *AUDIT*\n```\n{tbl(['Metric','Value'],rows,[12,10])}\n```")
            elif low=="/pnl":
                L=load(LF);rows=[["Total PnL",f"${L.get('total_realized_usdt',0.0):.2f}"],["Trades",f"{L.get('trade_count',0)}"]]
                tg(f"📊 *P&L*\n```\n{tbl(['Metric','Value'],rows,[12,10])}\n```")
            elif low.startswith("/analyze"):
                parts=txt.split()
                if len(parts)>1:
                    c=parts[1].upper();pr,_,r,ma5,ma10=data(f"{c}-USDT")
                    tg(f"🧠 *{c}*\nPrice:{pr}\nRSI:{r}\nMA5:{ma5}\nMA10:{ma10}")
            elif low=="/gainers":g,_=gl();tg(g)
            elif low=="/losers":_,l=gl();tg(l)
            elif low.startswith("/buy"):cmd_buy(txt,False)
            elif low.startswith("/dca"):cmd_buy(txt,True)
        st["last_update_id"]=lid;save(ST,st)
    except Exception as e:print(f"TG Err:{e}")
if __name__=="__main__":
    try:clr();tg_check();manage();scan();exec_q()
    except Exception as e:
        try:tg(f"🚨 *CRASH:* `{e}`\n`{traceback.format_exc()[-200:]}`")
        except:pass
    for c in [["git","pull","--rebase"],["git","add","."],["git","commit","-m","Auto"],["git","push"]]:
        try:subprocess.run(c,capture_output=True,timeout=30)
        except Exception as e:print(f"git {c[1]}:{e}")
