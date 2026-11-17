import requests,json,os,time,ccxt,subprocess,traceback,re,datetime
from google import genai
TG_TOKEN=os.environ.get('TG_TOKEN');TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
DB,ST,CFG,QF,LF,CD,SC,HIST="positions.json","tg_state.json","settings.json","queue.json","profit_ledger.json",".cmd_cooldown.json","scan_state.json","trade_history.json"
STABLE=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE'];LEV=['3L','3S','5L','5S','10L','10S','2L','2S'];BL=['RPANW','CT','ONE']
ex=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True})
ex.options['createMarketBuyOrderRequiresPrice']=False
client=genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
_C={};_T=20;_TC={};_TC_T=30
_SH=["Asset","Price","24H%","RSI","MA5","MA10","Sentiment"];_SW=[12,10,7,5,10,10,9]

def rkb():return {"keyboard":[[{"text":"📊 Status"},{"text":"💰 Balance"},{"text":"📋 Queue"}],[{"text":"🔍 Scan"},{"text":"🤖 AI"},{"text":"📈 Summary"}],[{"text":"🛒 BUY"},{"text":"🔴 SELL"},{"text":"🚨 PANIC"}],[{"text":"▶️ Resume"},{"text":"⏸ Pause"},{"text":"⚙️ Audit"}]],"resize_keyboard":True,"is_persistent":True}
_BTN={"📊 status":"/status","💰 balance":"/balance","📋 queue":"/queue","🔍 scan":"/scan","🤖 ai":"/ai_prompt","📈 summary":"/summary","🛒 buy":"/buy_prompt","🔴 sell":"/sell_prompt","🚨 panic":"/panic_confirm","▶️ resume":"/resume","⏸ pause":"/pause","⚙️ audit":"/audit"}

def _send(m,mode,buttons=None,kb=False):
    try:
        p={"chat_id":TG_CHAT_ID,"text":m,"parse_mode":mode}
        if buttons:p["reply_markup"]=buttons
        elif kb:p["reply_markup"]=rkb()
        r=requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json=p,timeout=10)
        if not r.ok:p["text"]=re.sub(r'<[^>]+>','',m) if mode=="HTML" else re.sub(r'[*_`]','',m);requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json=p,timeout=10)
    except:pass
def tg(m,kb=False):_send(m,"Markdown",kb=kb)
def tg_ai(m):_send(m,"HTML")
def tg_menu():_send("🕹️ <b>MAIN MENU</b>\nUse buttons or type a command.","HTML",kb=True)

def sentiment(r,p,ma5,ma10,chg):
    if r is None:return "N/A"
    if r<=28 and chg>0:return "OVERSOLD"
    if r<=35:return "WEAK"
    if r>=72:return "OVERBOUGHT"
    if r>=60 and p and ma5 and ma10 and p>ma5>ma10:return "BULLISH"
    if r<=42:return "BEARISH"
    if p and ma5 and ma10 and p>ma5>ma10:return "UPTREND"
    if p and ma5 and ma10 and p<ma5<ma10:return "DOWNTREND"
    return "NEUTRAL"
def tickers():
    if _TC and (time.time()-_TC.get('t',0))<_TC_T:return _TC['d']
    try:d=ex.fetch_tickers()
    except:d=_TC.get('d',{})
    _TC.update({'t':time.time(),'d':d});return d
def _row(sym,chg=None,amt=None):
    t=tickers().get(sym.replace("-","/"),{})
    if chg is None:chg=t.get('percentage') or 0
    p,pr,r,ma5,ma10=data(sym);lv=t.get('last') or p;a=sym.split('-')[0]
    if amt is not None:a=f"{a}({amt:.2f})"
    return [a,fp(lv),f"{chg:+.1f}%",f"{r:.0f}" if r is not None else "N/A",fp(ma5),fp(ma10),sentiment(r,lv,ma5,ma10,chg)]
def std_tbl(rows):return tbl(_SH,rows,_SW)

def load(f,d=None):
    d=[] if f==HIST else ({} if d is None else d)
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
    d={"rsi_min":42,"rsi_max":48,"stop_loss_pct":8,"auto_buy_enabled":True,"auto_buy_amount_usdt":5.75,"max_tokens_before_consolidation":3,"max_hold_hours":48,"trail_pct":5.0,"crash_guard_pct":18.0}
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
def clr():_C.clear();_TC.clear()
def cancel_sells(sym):
    try:
        for o in ex.fetch_open_orders(sym.replace("-","/")):
            if o['side']=='sell':
                try:ex.cancel_order(o['id'],sym.replace("-","/"))
                except:pass
    except:pass

def m_buy(cs,usdt):
    p=live(cs.replace("/","-"))
    if not p:raise Exception(f"No price {cs}")
    q=float(ex.amount_to_precision(cs,usdt/p))
    if q<=0:raise Exception("Amount too small")
    try:o=ex.create_market_buy_order(cs,q)
    except:o=ex.create_market_buy_order(cs,q,p)
    f,a=o.get('filled'),o.get('average')
    for _ in range(5):
        if f and a and f>0:break
        time.sleep(1)
        try:
            o2=ex.fetch_order(o['id'],cs);f=o2.get('filled',f);a=o2.get('average',a)
            if o2.get('status')=='closed':break
        except:pass
    if not f or f<=0:raise Exception(f"Order not filled ({o.get('status')})")
    return float(f),float(a or p)

def hard_sl(sym,qty,pr):
    try:
        cancel_sells(sym);time.sleep(0.5)
        if qty<=0:return True
        ex.create_order(sym.replace("-","/"),'market','sell',qty,None,{'triggerPrice':pr,'triggerType':'market_price','stopLossPrice':pr});return True
    except Exception as e:tg(f"⚠️ *SL FAILED* {sym}@{pr}: `{e}`");return False

def sell(sym,pct,reason):
    try:
        cs=sym.replace("-","/");coin=sym.split("-")[0]
        cancel_sells(sym);time.sleep(1)
        bal=ex.fetch_balance();tot=bal.get('total',{}).get(coin,0)
        if tot<=0:return True
        free=bal.get('free',{}).get(coin,0);qty=free if free>0 else tot;p=live(sym)
        if not p:return False
        q=qty*pct
        if q*p<1.02 and pct<1.0:q=qty;reason+=" (dust→full)"
        q=float(ex.amount_to_precision(cs,q))
        if q<=0:return True
        ex.create_market_sell_order(cs,q);db=load(DB)
        if sym in db:
            pnl=(p-db[sym]["entry_price"])*q;L=load(LF)
            L['total_realized_usdt']=L.get('total_realized_usdt',0.0)+pnl;L['trade_count']=L.get('trade_count',0)+1;save(LF,L)
            H=load(HIST);H.append({"sym":sym,"pnl":pnl,"time":time.time(),"reason":reason});save(HIST,H)
            tg(f"✅ *SOLD ({reason})*\n🪙 `{q}` {sym}\n{'🟢' if pnl>=0 else '🔴'} PnL: ${pnl:.2f}")
        return True
    except Exception as e:tg(f"❌ *SELL FAILED* {sym}: `{e}`");return False

def panic_sell():
    db=load(DB)
    if not db:tg("🚨 *PANIC:* No positions.")
    else:
        tg("🚨 *PANIC COMMAND INITIATED!*");ok=[];fail=[]
        for sym in list(db.keys()):
            if sell(sym,1.0,"PANIC"):db.pop(sym,None);save(DB,db);ok.append(sym)
            else:fail.append(sym)
        save(QF,{});msg=f"🛑 *PANIC COMPLETE*\n✅ Sold: {len(ok)}\n❌ Failed: {len(fail)}"
        if fail:msg+="\n"+"\n".join(fail)
        tg(msg)
    s=cfg();s['auto_buy_enabled']=False;save(CFG,s)

def _norm_sym(c):
    c=c.upper().replace("-","").replace("/","")
    if c.endswith("USDT") and len(c)>4:c=c[:-4]
    return c

def get_summary_text():
    H=load(HIST);now=time.time()
    day=[t for t in H if now-t['time']<=86400];week=[t for t in H if now-t['time']<=604800]
    def calc(lst):
        if not lst:return 0.0,0,0.0
        pnl=sum(t['pnl'] for t in lst);wins=len([t for t in lst if t['pnl']>0])
        return pnl,len(lst),(wins/len(lst)*100)
    dp,dc,dw=calc(day);wp,wc,ww=calc(week)
    return f"📊 *PERFORMANCE*\n\n*24H*\nPnL: ${dp:.2f}\nTrades: {dc}\nWin: {dw:.1f}%\n\n*7D*\nPnL: ${wp:.2f}\nTrades: {wc}\nWin: {ww:.1f}%"

def reconcile():
    db=load(DB)
    if not db:return db
    try:bal=ex.fetch_balance()['total']
    except:return db
    ch=False
    for sym in list(db.keys()):
        if bal.get(sym.split('-')[0],0)<=0:tg(f"🧹 Reconciled: {sym} not on exchange, removing");db.pop(sym,None);ch=True
    if ch:save(DB,db)
    return db

def ask_ai(p):
    try:
        db=load(DB);q=load(QF);s=cfg();bal=ex.fetch_balance()['free'].get('USDT',0);_,_,br,_,_=data("BTC-USDT")
        pos=[f"  {sym}:entry={d['entry_price']},pnl={((live(sym) or d['entry_price'])-d['entry_price'])/d['entry_price']*100:+.1f}%" for sym,d in list(db.items())[:5]] or ["  (none)"]
        ctx=f"USDT:{bal:.2f}|AutoBuy:${s['auto_buy_amount_usdt']}|BTC RSI:{br}|Pos({len(db)}):\n"+"\n".join(pos)+f"\nQueue:{len(q)}|RSI {s['rsi_min']}-{s['rsi_max']},SL {s['stop_loss_pct']}%\n\n"
        r=client.models.generate_content(model='gemini-2.5-flash',contents=f"Expert crypto analyst. Use Telegram HTML (<b>,<i>), newlines NOT <br>.\n{ctx}Q:{p}")
        tg_ai(f"🧠 <b>AI:</b>\n\n{r.text or '(empty)'}")
    except Exception as e:tg(f"❌ AI Error: {e}")

def manage():
    db=load(DB);s=cfg()
    for sym,d in list(db.items()):
        if 'opened_at' not in d:d['opened_at']=time.time();db[sym]=d;save(DB,db)
        _,_,r,_,_=data(sym);lv=live(sym)
        if not lv:continue
        inc=((lv-d["entry_price"])/d["entry_price"])*100;coin=sym.split('-')[0]
        tsl=round(d['entry_price']*(1-s['stop_loss_pct']/100),6)
        if d['stop_loss']<tsl:
            if lv<=tsl:
                if sell(sym,1.0,f"SL HIT ({s['stop_loss_pct']}%)"):db.pop(sym,None);save(DB,db)
                continue
            d['stop_loss']=tsl;tot=ex.fetch_balance()['total'].get(coin,0)
            if tot>0:hard_sl(sym,tot,tsl);tg(f"🛡️ *SL TIGHTENED:* {sym} → {tsl}")
        lp=d.get('last_price',0)
        if lp>0 and (lv-lp)/lp*100<=-s.get('crash_guard_pct',18):
            if sell(sym,1.0,"CRASH GUARD"):db.pop(sym,None);save(DB,db);continue
        d['last_price']=lv
        if inc>=5.0 and d['stop_loss']<d['entry_price']:
            d['stop_loss']=d['entry_price'];tot=ex.fetch_balance()['total'].get(coin,0)
            if tot>0:hard_sl(sym,tot,d['stop_loss']);tg(f"🛡️ *BREAKEVEN:* {sym} → {d['stop_loss']}")
        if s.get('trail_pct',0)>0:
            peak=d.get('peak_price',d['entry_price'])
            if lv>peak:
                peak=lv;d['peak_price']=peak;tsl2=round(peak*(1-s.get('trail_pct',5)/100),6)
                if tsl2>d['stop_loss']:
                    d['stop_loss']=tsl2;tot=ex.fetch_balance()['total'].get(coin,0)
                    if tot>0:hard_sl(sym,tot,tsl2);tg(f"📈 *TRAIL UPDATED:* {sym} → {tsl2}")
        hh=(time.time()-d.get('opened_at',time.time()))/3600
        if hh>s.get('max_hold_hours',48):
            if sell(sym,1.0,f"TIME STOP {hh:.0f}h"):db.pop(sym,None);save(DB,db)
            continue
        if lv<=d["stop_loss"]:
            if sell(sym,1.0,"STOP LOSS"):db.pop(sym,None);save(DB,db)
            continue
        if r and r>=75:
            if sell(sym,1.0,"RSI 75"):db.pop(sym,None);save(DB,db)

def consolidate():
    db=load(DB)
    if len(db)<=2:return
    tg("⚖️ *CONSOLIDATING...*")
    rk=[{"s":k,"pnl":((p-d['entry_price'])/d['entry_price'])*100} for k,d in db.items() if (p:=live(k)) and "entry_price" in d]
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
            if sym in db:q.pop(sym,None);save(QF,q);continue
            if not live(sym):continue
            spend=max(0.0,ub-2.00)
            if spend<1.02 and db:
                wk=None;low=999
                for hs,hd in db.items():
                    hp=live(hs)
                    if hp:
                        pnl=((hp-hd['entry_price'])/hd['entry_price'])*100
                        if pnl<low:low=pnl;wk=hs
                if wk:
                    nsc=qd.get('score',0)
                    if nsc>25 or low<0:
                        tg(f"🔄 *ROTATION:* {wk} → {sym}")
                        if sell(wk,1.0,"ROTATION"):db.pop(wk,None);save(DB,db);time.sleep(2);ub=ex.fetch_balance()['free'].get('USDT',0);spend=max(0.0,ub-2.00)
                    else:
                        cancel_sells(wk);time.sleep(0.5);bc=wk.split('-')[0]
                        hb=ex.fetch_balance()['free'].get(bc,0);hp=live(wk)
                        if hb>0 and hp:
                            v=min(s['auto_buy_amount_usdt'],hb*hp);qs=float(ex.amount_to_precision(wk.replace("-","/"),v/hp))
                            if qs*hp<(hb*hp)*0.95:
                                ex.create_market_sell_order(wk.replace("-","/"),qs);tg(f"🔄 *PARTIAL SWAP:* {qs} {bc} → {sym}")
                                time.sleep(2);ub=ex.fetch_balance()['free'].get('USDT',0);spend=max(0.0,ub-2.00)
                                rem=ex.fetch_balance()['total'].get(bc,0)
                                if rem>0 and (rem*hp)>1.02:hard_sl(wk,rem,db[wk]['stop_loss'])
                                else:db.pop(wk,None);save(DB,db)
            if spend<1.02:q.pop(sym,None);save(QF,q);continue
            sp=min(spend,s['auto_buy_amount_usdt'])
            if len(db)>=s["max_tokens_before_consolidation"]:continue
            try:f,a=m_buy(sym.replace("-","/"),sp)
            except Exception as e:tg(f"❌ Buy fail {sym}: {e}");mk_cd(f"fail_{sym}");continue
            maxsl=round(a*(1-s['stop_loss_pct']/100),6);sl=qd.get('sl') or maxsl
            if sl>=a or sl>maxsl:sl=maxsl
            db[sym]={"entry_price":round(a,6),"safe_zone":round(a,6),"stop_loss":sl,"took_profit_10":False,"took_profit_50":False,"sl_hit":False,"opened_at":time.time(),"peak_price":a,"last_price":a}
            save(DB,db);hard_sl(sym,f,sl)
            try:tg(f"🛒 *EXECUTED*\n```\n{std_tbl([_row(sym)])}\n```\nSpent ${sp:.2f} | SL {sl}")
            except:tg(f"🛒 *EXECUTED:* {sym} @ {a}")
            q.pop(sym,None);save(QF,q);ub=ex.fetch_balance()['free'].get('USDT',0);time.sleep(2)
    except Exception as e:tg(f"⚠️ *QUEUE FAILED:* `{e}`")

def scan():
    st=load(SC)
    if time.time()-st.get('last',0)<3540:return
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
        if (t['quoteVolume'] or 0)>=1000000:v.append({'sym':sym.replace("/","-"),'vol':t['quoteVolume'],'chg':t['percentage'] or 0})
    v.sort(key=lambda x:x['vol'],reverse=True);ct=int(time.time());qu=False
    for sym,d in list(q.items()):
        if ct-d.get('timestamp',0)>86400:q.pop(sym,None);qu=True
    for t in v[15:100]:
        p,pr,r,ma5,ma10=data(t['sym'])
        if not p or r is None:continue
        setup=sl=None
        if t['chg']>1.0 and s['rsi_min']<=r<=s['rsi_max'] and p>ma5>ma10:setup="TREND";sl=round(ma5*(1-s['stop_loss_pct']/100),6)
        elif t['chg']<-8.0 and r<30 and p>pr:setup="OVERSOLD";sl=round(p*(1-s['stop_loss_pct']/100),6)
        if setup:
            rr=(p*0.10)/(p-sl) if (p-sl)>0 else 0;sc=(t['vol']/1e6)+(rr*10)
            if t['sym'] not in q or q[t['sym']].get('score',0)<sc:
                if t['sym'] not in q:
                    try:row=_row(t['sym'])
                    except:row=["-"]*7
                    tg(f"📥 *QUEUED* {setup}\n```\n{std_tbl([row])}\n```\nScore {sc:.1f}")
                q[t['sym']]={"timestamp":ct,"type":setup,"sl":sl,"score":sc};qu=True
    if qu:save(QF,q)
    st['last']=time.time();save(SC,st)

def check_daily_summary():
    st=load(SC);now=datetime.datetime.now();today=now.strftime('%Y-%m-%d')
    if now.hour>=20 and st.get('last_summary_day')!=today:tg(get_summary_text());st['last_summary_day']=today;save(SC,st)

def fp(p):
    if p is None:return "N/A"
    if p>=100:return f"{p:.2f}"
    if p>=1:return f"{p:.4f}"
    if p>=0.001:return f"{p:.6f}"
    return f"{p:.8f}"

def tbl(h,r,w):
    bar="+"+"+".join(["-"*(x+2) for x in w])+"+"
    hd="| "+" | ".join([a.ljust(b) for a,b in zip(h,w)])+" |"
    return "\n".join([bar,hd,bar]+["| "+" | ".join([str(v).ljust(x) for v,x in zip(rw,w)])+" |" for rw in r]+[bar])

def gl():
    t=tickers();v=[]
    for sym,tk in t.items():
        if not sym.endswith('/USDT'):continue
        b=sym.split('/')[0]
        if (b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV)):continue
        if (tk['quoteVolume'] or 0)>=1000000:v.append({'sym':b+'-USDT','chg':tk['percentage'] or 0})
    v.sort(key=lambda x:x['chg'],reverse=True)
    return [_row(x['sym'],chg=x['chg']) for x in v[:8]],[_row(x['sym'],chg=x['chg']) for x in v[-8:][::-1]]

def cmd_buy(txt):
    parts=txt.split()
    if len(parts)!=3:tg("❌ *Usage:* `/buy SYMBOL AMOUNT`");return
    c=_norm_sym(parts[1])
    try:amt=float(parts[2])
    except:tg("❌ Invalid amount.");return
    sym=f"{c}-USDT"
    if cd(f"buy:{sym}",30):tg(f"⏳ Already processing.");return
    db=load(DB)
    if sym in db:tg(f"❌ Active position for {c}.");return
    try:
        if sym.replace("-","/") not in ex.load_markets():tg(f"❌ Symbol `{sym}` not on Bitget.");return
    except:pass
    ub=ex.fetch_balance()['free'].get('USDT',0)
    if ub<amt:tg(f"❌ *BUY FAILED:* Only ${ub:.2f} (need ${amt:.2f}).");return
    try:
        old=ex.fetch_balance()['total'].get(c,0);f,a=m_buy(f"{c}/USDT",amt);tot=old+f
        sl=round(a*(1-cfg()['stop_loss_pct']/100),6)
        db[sym]={"entry_price":round(a,6),"safe_zone":round(a,6),"stop_loss":sl,"took_profit_10":False,"took_profit_50":False,"sl_hit":False,"opened_at":time.time(),"peak_price":a,"last_price":a}
        save(DB,db)
        if tot>0:hard_sl(sym,tot,sl)
        try:tg(f"✅ *BOUGHT*\n```\n{std_tbl([_row(sym)])}\n```\nSpent ${amt:.2f} | Filled {f} | SL {sl}")
        except:tg(f"✅ *BOUGHT:* {c} @ {a:.6f}\nSL {sl}")
    except Exception as e:tg(f"❌ *BUY FAILED:* {e}");mk_cd(f"fail_buy:{sym}")

def cmd_sell(txt):
    parts=txt.split()
    if len(parts)<2:tg("❌ *Usage:* `/sell SYMBOL [PCT]`");return
    c=_norm_sym(parts[1]);sym=f"{c}-USDT";db=load(DB)
    if sym not in db:tg(f"❌ No position for {c}.");return
    s=cfg()
    if len(parts)>=3:
        try:v=float(parts[2]);s['trail_pct']=v if v>0 else s['trail_pct'];save(CFG,s)
        except:pass
    d=db[sym];lv=live(sym)
    if not lv:tg(f"❌ No price for {c}.");return
    tsl=round(lv*(1-s['trail_pct']/100),6)
    d['peak_price']=lv;d['stop_loss']=tsl;db[sym]=d;save(DB,db)
    tot=ex.fetch_balance()['total'].get(c,0)
    if tot>0:hard_sl(sym,tot,tsl)
    try:tg(f"🔴 *TRAILING SELL SET* ({s['trail_pct']}%)\n```\n{std_tbl([_row(sym)])}\n```\nTrail SL {tsl}")
    except:tg(f"🔴 *TRAIL SET:* {c} | SL {tsl}")

def cmd_set(txt):
    parts=txt.split()
    if len(parts)!=3:tg("❌ Usage: `/set <key> <value>`");return
    key,val=parts[1],parts[2];s=cfg()
    if key not in s:tg(f"❌ Unknown key.");return
    try:
        if isinstance(s[key],bool):new=val.lower() in ('true','1','yes','on')
        elif isinstance(s[key],float):new=float(val)
        else:new=int(val)
    except:tg(f"❌ Invalid value.");return
    s[key]=new;save(CFG,s);tg(f"✅ `{key}` = `{new}`")

def tg_check():
    st=load(ST);lid
