import requests,json,os,time,ccxt,subprocess,traceback,re,datetime,sys
from google import genai
try:import fcntl
except ImportError:fcntl=None
TK=os.environ.get('TG_TOKEN');CHAT=str(os.environ.get('TG_CHAT_ID',''));TZ=float(os.environ.get('TZ_OFFSET_HOURS','1'));API=f"https://api.telegram.org/bot{TK}"
DB,ST,CFG,QF,LF,CD,SC,HIST,LOCK="positions.json","tg_state.json","settings.json","queue.json","profit_ledger.json",".cmd_cooldown.json","scan_state.json","trade_history.json",".bot.lock"
FILES=[DB,ST,CFG,QF,LF,CD,SC,HIST]
STABLE=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE'];LEV=['3L','3S','5L','5S','10L','10S','2L','2S'];BL=['RPANW','CT','ONE']
ex=ccxt.bitget({'apiKey':os.environ.get('BITGET_API_KEY'),'secret':os.environ.get('BITGET_API_SECRET'),'password':os.environ.get('BITGET_API_PASSPHRASE'),'enableRateLimit':True});ex.options['createMarketBuyOrderRequiresPrice']=False
client=genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
GEMINI_MODELS=[m for m in [os.environ.get('GEMINI_MODEL'),'gemini-3.8-flash','gemini-3.6-flash','gemini-2.5-flash'] if m]
_C={};KB={"keyboard":[["📊 Menu","▶️ Resume","🚨 PANIC"]],"resize_keyboard":True,"is_persistent":True}
S=lambda s:s.replace("-","/")
TR=lambda t:{'trigger':True} if t else {}
B=lambda k,c:ex.fetch_balance()[k].get(c,0)
skip=lambda b:b in STABLE or b in BL or ((b.startswith('X') or b.startswith('R')) and len(b)<=6) or any(b.upper().endswith(x) for x in LEV)
NP=lambda e,sl:{"entry_price":e,"safe_zone":e,"stop_loss":sl,"peak_price":e,"last_price":e,"opened_at":time.time()}
STOP_OK="🛡️ Exchange stop-loss: Active\n📈 Bot-side trailing: Active"
STOP_NO="⚠️ Exchange stop NOT placed.\n📈 Bot-side stop + trailing still protect it"
def _send(m,mode,btn=None):
    try:
        rm=btn or KB;p={"chat_id":CHAT,"text":m,"reply_markup":rm}
        if mode:p["parse_mode"]=mode
        if not requests.post(API+"/sendMessage",json=p,timeout=10).ok:
            p.pop("parse_mode",None);p["text"]=re.sub(r'<[^>]+>','',m) if mode=="HTML" else m
            r=requests.post(API+"/sendMessage",json=p,timeout=10)
            if not r.ok:print("TG SEND ERROR:",r.text)
    except Exception as e:print("TG EXCEPTION:",e)
tg=lambda m:_send(m,"Markdown");tg_ai=lambda m:_send(m,"HTML")
def menu():
    s=cfg();b=lambda t,c:{"text":t,"callback_data":c};tb=b("📈 Trail: ON","trail_off") if s.get('trail_pct',8.0)>0 else b("📉 Trail: OFF","trail_on")
    _send("🕹️ *MAIN MENU*\nSelect an option below:","Markdown",{"inline_keyboard":[[b("▶️ Resume","/resume"),b("⏸ Pause","/pause")],[b("🔄 Sync","/sync"),b("🛠️ Audit","/audit")],[b("🟢 Status","/status"),b("💰 Balance","/balance")],[b("📊 Summary","/summary"),tb],[b("🚀 Gainers","/gainers"),b("🩸 Losers","/losers")],[b("🛒 Buy Info","buy_info"),b("💸 Sell Info","sell_info")],[b("🧠 Ask AI","ai_info")]]})
def panic_prompt():_send("🚨 *CONFIRM PANIC*\nThis sells ALL positions at market and pauses auto-buy.\nConfirm within 2 minutes.","Markdown",{"inline_keyboard":[[{"text":"✅ YES, SELL EVERYTHING","callback_data":"panic_confirm"},{"text":"❌ Cancel","callback_data":"panic_cancel"}]]})
def load(f,d=None):
    d=[] if f==HIST else ({} if d is None else d)
    try:
        if os.path.exists(f):
            with open(f) as h:return json.load(h)
    except Exception as e:print(f"LOAD ERR {f}: {e}")
    return d
def save(f,d):
    try:
        with open(f+".tmp","w") as h:json.dump(d,h,indent=4)
        os.replace(f+".tmp",f)
    except Exception as e:print(f"SAVE ERR {f}: {e}")
def cfg():
    d={"rsi_min":42,"rsi_max":48,"stop_loss_pct":12,"auto_buy_enabled":True,"auto_buy_amount_usdt":10.0,"max_tokens_before_consolidation":3,"max_hold_hours":48,"trail_pct":8.0,"crash_guard_pct":18.0,"rsi_exit":75}
    s=load(CFG,d)
    for k,v in d.items():s.setdefault(k,v)
    return s
def in_cd(k,s=5):return time.time()-load(CD,{}).get(k,0)<s
def mk_cd(k):d=load(CD,{});d[k]=time.time();save(CD,d)
def cd(k,s=5):
    if in_cd(k,s):return True
    mk_cd(k);return False
def _rsi(c,n=14):
    if len(c)<n+1:return None
    d=[c[i]-c[i-1] for i in range(1,len(c))];ag=sum(max(0,x) for x in d[:n])/n;al=sum(max(0,-x) for x in d[:n])/n
    for x in d[n:]:
        ag=(ag*(n-1)+max(0,x))/n;al=(al*(n-1)+max(0,-x))/n
    return 100.0 if al==0 else round(100-100/(1+ag/al),2)
def _calc(sym):
    try:
        o=ex.fetch_ohlcv(S(sym),'1h',limit=100)
        if len(o)<50:return (None,)*5
        c=[float(k[4]) for k in o[:-1]]
        return float(o[-1]
