import requests,json,os,time,ccxt,subprocess,traceback,re,datetime
from google import genai

TG_TOKEN=os.environ.get('TG_TOKEN')
TG_CHAT_ID=os.environ.get('TG_CHAT_ID')
DB="positions.json";ST="tg_state.json";CFG="settings.json";QF="queue.json";LF="profit_ledger.json";CD=".cmd_cooldown.json";SC="scan_state.json";HIST="trade_history.json"
STABLE=['USDC','USDT','DAI','FDUSD','TUSD','USDD','USDE']
LEV=['3L','3S','5L','5S','10L','10S','2L','2S']
BL=['RPANW','CT','ONE']

ex=ccxt.bitget({'apiKey
