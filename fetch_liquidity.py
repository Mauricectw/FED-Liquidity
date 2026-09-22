#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_liquidity.py — 【GitHub Actions 版】
本檔只負責抓資料，寫出 data.json / history.json，交給 GitHub Actions 排程執行、
再由 workflow commit 回 repo；瀏覽器端的 index.html 只是純靜態頁面，直接 fetch
同目錄下的 data.json / history.json，不需要任何本機伺服器或 Python 執行。

資料來源：
  1. FRED API（免費 Key）: WRESBAL(準備金) / RRPONTSYD(RRP) / SOFR / IORB / BAMLH0A0HYM2(HY OAS)
  2. 財政部 FiscalData（免 Key）: TGA 每日收盤餘額
  3. Stooq（免 Key）: NDX（那斯達克100）日線 → 計算相對 50 日均線乖離 %
  4. CBOE 每日市場統計: 股權 Put/Call Ratio（半自動：多來源嘗試，失敗則保留手動）\n  5. Yahoo Finance: QQQ 流通股數 → 週變化 × 價格 = 被動 ETF 週流入代理（全自動，需累積一週歷史）

警報推送（擇一或並用，留空即停用）：
  - LINE Messaging API（LINE Notify 已於 2025/3/31 終止服務，改用此法）
      LINE_CHANNEL_TOKEN：LINE Developers 建立 Messaging API channel 後的 Channel access token
      使用 broadcast 端點推送給所有加此官方帳號為好友的人 —— 只要自己加好友即可，
      免填 userId。免費額度 200 則/月，每日警報足夠。
  - WEBHOOK_URL：Teams / Slack / Discord Incoming Webhook（POST {"text": ...}）

排程範例：
  Windows: schtasks /Create /TN "FedLiquidity" /TR "python C:\\dashboard\\fetch_liquidity.py" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00
  Linux  : 0 6 * * 1-5  cd /opt/dashboard && python3 fetch_liquidity.py >> fetch.log 2>&1

僅用標準函式庫，無需 pip install。
"""

import json, os, re, ssl, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import date, datetime, timedelta

# ── 設定 ─────────────────────────────────────────────────────────
# 注意：os.environ.get("名稱", "預設值") —— 第一個參數是環境變數「名稱」，不要動它！
#       要寫死 key 就貼在【第二個參數】的引號裡。
FRED_API_KEY       = os.environ.get("FRED_API_KEY", "")        # 只從環境變數/GitHub Secrets 讀取，程式碼中不存放金鑰
LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")  # 只從環境變數讀取，留空 = 不推 LINE
WEBHOOK_URL        = os.environ.get("WEBHOOK_URL", "")          # 留空 = 不推 Webhook

# 占位字串防呆：占位符未替換時視為未設定
if "貼" in LINE_CHANNEL_TOKEN or "填" in LINE_CHANNEL_TOKEN:
    LINE_CHANNEL_TOKEN = ""
if "填" in FRED_API_KEY:
    FRED_API_KEY = ""
if not FRED_API_KEY:
    print("[提示] 未設定 FRED_API_KEY 環境變數，FRED 系列指標將無法抓取。\n"
          "       設定方式: setx FRED_API_KEY \"你的key\"（設完重開終端機）", file=sys.stderr)
OUT_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
TIMEOUT   = 30
CTX       = ssl.create_default_context()
UA        = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36",
             "Accept": "application/json, text/csv, text/html;q=0.9, */*;q=0.8"}

def http_get(url, headers=None, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last

def http_json(url):
    return json.loads(http_get(url))

# ── 1. FRED ─────────────────────────────────────────────────────
def fred_latest(series_id):
    url = ("https://api.stlouisfed.org/fred/series/observations?" +
           urllib.parse.urlencode({"series_id": series_id, "api_key": FRED_API_KEY,
                                   "file_type": "json", "sort_order": "desc", "limit": 5}))
    try:
        for obs in http_json(url).get("observations", []):
            if obs.get("value") not in (".", "", None):
                return float(obs["value"]), obs["date"]
    except Exception as e:
        print(f"[WARN] FRED {series_id}: {e}", file=sys.stderr)
    return None, None

def _fred_range(series_id, years=5):
    start = (date.today() - timedelta(days=365 * years)).isoformat()
    url = ("https://api.stlouisfed.org/fred/series/observations?" +
           urllib.parse.urlencode({"series_id": series_id, "api_key": FRED_API_KEY,
                                   "file_type": "json", "observation_start": start,
                                   "sort_order": "asc", "limit": 100000}))
    return [(o["date"], float(o["value"])) for o in http_json(url).get("observations", [])
            if o.get("value") not in (".", "", None)]

def _align(series, dates):
    """把日頻序列對齊到給定日期清單（取當日或之前最近一筆）"""
    out, i, last = [], 0, None
    for d in dates:
        while i < len(series) and series[i][0] <= d:
            last = series[i][1]; i += 1
        out.append(last)
    return out

def fred_history(years=5):
    """{dates, values(準備金兆), sp500/nasdaq(指數化起點=100), sp_raw/nq_raw(原始點位)}"""
    res = _fred_range("WRESBAL", years)
    dates  = [d for d, _ in res]
    values = [round(v / 1_000_000, 3) for _, v in res]
    out = {"dates": dates, "values": values, "netliq": None,
           "sp500": None, "nasdaq": None, "sp_raw": None, "nq_raw": None}
    try:
        wal = dict(_fred_range("WALCL", years))
        tga = dict(_fred_range("WTREGEN", years))
        rrp = dict(_fred_range("WLRRAL", years))
        common = sorted(set(wal) & set(tga) & set(rrp))
        nls = [(d, (wal[d] - tga[d] - rrp[d]) / 1e6) for d in common]
        out["netliq"] = [round(v, 2) if v else None for v in _align(nls, dates)]
    except Exception as e:
        out["netliq"] = None
        print(f"[WARN] 淨流動性歷史: {e}", file=sys.stderr)
    for sid, key, raw_key in (("SP500", "sp500", "sp_raw"), ("NASDAQCOM", "nasdaq", "nq_raw")):
        try:
            aligned = _align(_fred_range(sid, years), dates)
            base = next((v for v in aligned if v), None)
            if base:
                out[key]     = [round(v / base * 100, 1) if v else None for v in aligned]
                out[raw_key] = [round(v, 0) if v else None for v in aligned]
        except Exception as e:
            print(f"[WARN] FRED {sid} 歷史: {e}", file=sys.stderr)
    return out

# ── 2. TGA（FiscalData） ────────────────────────────────────────
def tga_latest():
    url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
           "/v1/accounting/dts/operating_cash_balance"
           "?sort=-record_date&page[size]=6&fields=record_date,account_type,open_today_bal")
    try:
        for row in http_json(url).get("data", []):
            if "TGA" in row.get("account_type", "") or "Federal Reserve" in row.get("account_type", ""):
                return round(float(row["open_today_bal"]) / 1000.0), row["record_date"]
    except Exception as e:
        print(f"[WARN] FiscalData TGA: {e}", file=sys.stderr)
    return None, None

# ── 3. NDX 50 日均線乖離（Stooq 免費日線 CSV） ──────────────────
def fred_series(series_id, limit=90):
    """取序列近 N 筆有效觀測值（由舊到新）"""
    url = ("https://api.stlouisfed.org/fred/series/observations?" +
           urllib.parse.urlencode({"series_id": series_id, "api_key": FRED_API_KEY,
                                   "file_type": "json", "sort_order": "desc", "limit": limit}))
    obs = [(o["date"], float(o["value"])) for o in http_json(url).get("observations", [])
           if o.get("value") not in (".", "", None)]
    obs.reverse()
    return obs

def ndx_deviation():
    # 主來源：FRED NASDAQ100 日線（與其他序列共用同一把 key，最穩定）
    try:
        obs = fred_series("NASDAQ100", 90)
        if len(obs) >= 50:
            closes = [v for _, v in obs]
            last, ma50 = closes[-1], sum(closes[-50:]) / 50
            return round((last / ma50 - 1) * 100, 1), obs[-1][0]
    except Exception as e:
        print(f"[WARN] FRED NASDAQ100: {e}", file=sys.stderr)
    # 備援：Stooq CSV（先驗證回應確實是 CSV，防爬蟲頁直接略過）
    try:
        csv = http_get("https://stooq.com/q/d/l/?s=%5Endx&i=d")
        if not csv.lstrip().lower().startswith("date,"):
            raise ValueError("回應非 CSV（疑似防爬蟲頁），略過")
        rows = [r.split(",") for r in csv.strip().splitlines()[1:] if r.count(",") >= 4]
        closes = [float(r[4]) for r in rows if r[4] not in ("", "N/A")]
        if len(closes) >= 50:
            last, ma50 = closes[-1], sum(closes[-50:]) / 50
            return round((last / ma50 - 1) * 100, 1), rows[-1][0]
    except Exception as e:
        print(f"[WARN] Stooq NDX: {e}", file=sys.stderr)
    return None, None

# ── 4. 股權 Put/Call（CBOE，多來源嘗試 → 失敗則 None 保留手動） ─
def cboe_equity_pc():
    # 來源 A：CBOE 每日市場統計頁（HTML 內含各類 Put/Call Ratio）
    try:
        html = http_get("https://www.cboe.com/us/options/market_statistics/daily/")
        m = re.search(r"EQUITY\s+PUT/CALL\s+RATIO[^0-9]{0,80}([01]\.\d{1,3})", html, re.I | re.S)
        if m:
            return float(m.group(1)), "cboe-daily-page"
    except Exception as e:
        print(f"[WARN] CBOE daily page: {e}", file=sys.stderr)
    # 來源 B：CBOE 歷史 archive CSV（僅在檔案仍在更新時採用：30 天內）
    try:
        csv = http_get("https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypcarchive.csv")
        rows = [r for r in csv.strip().splitlines() if re.match(r"\d{1,2}/\d{1,2}/\d{4},", r)]
        if rows:
            last = rows[-1].split(",")
            d = datetime.strptime(last[0], "%m/%d/%Y").date()
            if date.today() - d <= timedelta(days=30):
                return float(last[-1]), f"cboe-archive({d})"
            print(f"[WARN] CBOE archive 已停更（最後 {d}），忽略", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] CBOE archive: {e}", file=sys.stderr)
    return None, None   # 兩來源皆失敗 → 儀表盤保留手動輸入

# ── 5. 被動 ETF 資金流代理：QQQ 流通股數週變化 × 價格（Yahoo，全自動） ─
SO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qqq_so_history.json")

def _yahoo_session():
    """Yahoo API 需要 cookie + crumb 才放行 quoteSummary"""
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=CTX))
    opener.addheaders = list(UA.items())
    try:
        opener.open("https://fc.yahoo.com", timeout=TIMEOUT)      # 取 cookie
    except urllib.error.HTTPError:
        pass   # fc.yahoo.com 固定回 404（"Not Found on Accelerator"），cookie 已種下，屬正常現象
    crumb = opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb",
                        timeout=TIMEOUT).read().decode()
    return opener, crumb

def qqq_weekly_flow():
    """回傳 (週流入 十億美元, 說明)；歷史不足 5 天時回 (None, 'accumulating')"""
    so = px = None
    try:
        opener, crumb = _yahoo_session()
        # 主來源：v7 quote（對 ETF 穩定提供 sharesOutstanding）
        url = ("https://query1.finance.yahoo.com/v7/finance/quote"
               "?symbols=QQQ&crumb=" + urllib.parse.quote(crumb))
        j = json.loads(opener.open(url, timeout=TIMEOUT).read().decode())
        r = (j.get("quoteResponse", {}).get("result") or [{}])[0]
        so, px = r.get("sharesOutstanding"), r.get("regularMarketPrice")
        if not (so and px):
            # 備援：quoteSummary（部分帳號區域 v7 缺欄位時）
            url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/QQQ"
                   "?modules=defaultKeyStatistics,price&crumb=" + urllib.parse.quote(crumb))
            j = json.loads(opener.open(url, timeout=TIMEOUT).read().decode())
            r = (j.get("quoteSummary", {}).get("result") or [{}])[0]
            so = so or r.get("defaultKeyStatistics", {}).get("sharesOutstanding", {}).get("raw")
            px = px or r.get("price", {}).get("regularMarketPrice", {}).get("raw")
        if not (so and px):
            raise ValueError(f"兩端點皆未提供股數/價格（v7 keys: {sorted(r.keys())[:8]}…）")
    except Exception as e:
        print(f"[WARN] Yahoo QQQ SO: {e}", file=sys.stderr)
        return None, None

    # 讀寫本地歷史（每天一筆，保留 60 天）
    try:
        hist = json.load(open(SO_FILE, encoding="utf-8"))
    except Exception:
        hist = []
    today = date.today().isoformat()
    hist = [h for h in hist if h["date"] != today]
    hist.append({"date": today, "so": so, "px": px})
    hist = sorted(hist, key=lambda h: h["date"])[-60:]
    with open(SO_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f)

    # 找 5–9 天前最近的一筆當比較基準（涵蓋週末）
    base = None
    for h in reversed(hist[:-1]):
        gap = (date.today() - date.fromisoformat(h["date"])).days
        if 5 <= gap <= 9:
            base = h
            break
    if base is None:
        print(f"[INFO] QQQ 股數歷史累積中（{len(hist)} 天），滿一週後開始輸出流量", file=sys.stderr)
        return None, "accumulating"
    flow_b = (so - base["so"]) * px / 1e9
    return round(flow_b, 1), f"QQQ SO {base['date']}→{today}"

# ── 6. 交易台進階指標 ───────────────────────────────────────────
def _yahoo_chart(symbol, rng="6mo"):
    """Yahoo v8 chart（免 crumb）→ 收盤價序列"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?range=" + rng + "&interval=1d")
    res = http_json(url)["chart"]["result"][0]
    return [c for c in res["indicators"]["quote"][0]["close"] if c is not None]

def yahoo_last(symbol):
    try:
        closes = _yahoo_chart(symbol, "1mo")
        return round(closes[-1], 2) if closes else None
    except Exception as e:
        print(f"[WARN] Yahoo {symbol}: {e}", file=sys.stderr)
        return None

def netliq_now_and_change():
    """淨流動性 = WALCL − WTREGEN − WLRRAL（皆為 H.4.1 週頻、百萬美元）
       回傳 (目前 兆美元, 13週變化 十億美元)"""
    try:
        wal = dict(_fred_range("WALCL", 2))
        tga = dict(_fred_range("WTREGEN", 2))
        rrp = dict(_fred_range("WLRRAL", 2))
        dates = sorted(set(wal) & set(tga) & set(rrp))
        nl = [(wal[d] - tga[d] - rrp[d]) / 1e6 for d in dates]
        if not nl:
            return None, None
        chg = round((nl[-1] - nl[-14]) * 1000) if len(nl) >= 14 else None
        return round(nl[-1], 2), chg
    except Exception as e:
        print(f"[WARN] 淨流動性: {e}", file=sys.stderr)
        return None, None

def rsp_spy_signal():
    """市場寬度：RSP/SPY 比值。紅 = 比值創3月新低 且 SPY 距3月新高<2%（指數創高士兵陣亡）"""
    try:
        rsp, spy = _yahoo_chart("RSP", "6mo"), _yahoo_chart("SPY", "6mo")
        n = min(len(rsp), len(spy))
        if n < 63:
            return None, None
        rsp, spy = rsp[-n:], spy[-n:]
        ratio = [a / b for a, b in zip(rsp, spy)]
        cur, low3m, spy_hi = ratio[-1], min(ratio[-63:]), max(spy[-63:])
        at_low, near_high = cur <= low3m * 1.002, spy[-1] >= spy_hi * 0.98
        sig = "r" if (at_low and near_high) else ("y" if at_low else "g")
        return round(cur, 3), sig
    except Exception as e:
        print(f"[WARN] RSP/SPY: {e}", file=sys.stderr)
        return None, None

# ── 7. 逐步撤出參考指數（0–100，加權合成 13 項狀態） ──────────
_EXIT_W = {"res":20,"srf":14,"nlq":12,"s99":8,"hy":8,"sofr_iorb":6,"move":6,
           "vixr":6,"rsp":6,"tga":4,"rrp":4,"ndx":3,"etf":3}

def _state_score(k, d):
    """回傳 0(綠)/0.5(黃)/1(紅)，缺值回 None"""
    v = d.get(k)
    if k == "rsp":
        s = d.get("rsp_sig")
        return {"g":0,"y":0.5,"r":1}.get(s) if s else None
    if v is None: return None
    if k=="res":       return 1 if v<2.5 else 0.5 if v<3.0 else 0
    if k=="srf":       return 1 if v>50 else 0.5 if v>5 else 0
    if k=="nlq":
        c = d.get("nlqd")
        return None if c is None else (1 if c<-300 else 0.5 if c<-100 else 0)
    if k=="s99":       return 1 if v>20 else 0.5 if v>=5 else 0
    if k=="hy":        return 1 if v>450 else 0.5 if v>350 else 0
    if k=="sofr_iorb": return 1 if v>25 else 0.5 if v>=0 else 0
    if k=="move":      return 1 if v>130 else 0.5 if v>=100 else 0
    if k=="vixr":      return 1 if v>1.0 else 0.5 if v>=0.9 else 0
    if k=="tga":       return 1 if v>1000 else 0.5 if v>750 else 0
    if k=="rrp":       return 1 if v<10 else 0.5 if v<100 else 0
    if k=="ndx":
        pc = d.get("pc")
        return 1 if (v>8 and pc is not None and pc<0.55) else 0.5 if v>5 else 0
    if k=="etf":       return 1 if v>25 else 0.5 if v>15 else 0
    return None

def exit_score(d):
    num = den = 0.0
    for k, w in _EXIT_W.items():
        s = _state_score(k, d)
        if s is not None:
            num += s * w; den += w
    if den == 0: return None
    score = round(num / den * 100)
    if (d.get("res") is not None and d["res"] < 2.5) or (d.get("srf") is not None and d["srf"] > 50):
        score = max(score, 80)   # 硬觸發：死亡線或 SRF 爆量
    return score

# ── 警報推送 ────────────────────────────────────────────────────
def push_line(text):
    if not LINE_CHANNEL_TOKEN:
        return
    # broadcast：推送給所有加此官方帳號為好友的人（僅自己加好友＝只推給自己），免填 userId
    body = json.dumps({"messages": [{"type": "text", "text": text}]}).encode()
    req = urllib.request.Request("https://api.line.me/v2/bot/message/broadcast", data=body,
        headers={**UA, "Content-Type": "application/json",
                 "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}"})
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX)
        print("[LINE] 已推送")
    except Exception as e:
        print(f"[WARN] LINE push: {e}", file=sys.stderr)

def push_webhook(text):
    if not WEBHOOK_URL:
        return
    body = json.dumps({"text": text, "content": text}).encode()  # Teams/Slack 用 text，Discord 用 content
    req = urllib.request.Request(WEBHOOK_URL, data=body,
                                 headers={**UA, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX)
        print("[Webhook] 已推送")
    except Exception as e:
        print(f"[WARN] Webhook: {e}", file=sys.stderr)

def notify(text):
    print(text, file=sys.stderr)
    push_line(text)
    push_webhook(text)

# ── 主流程：抓取 + 警報 ──────────────────────────────────────
def collect_and_alert():
    res_m, res_date = fred_latest("WRESBAL")
    rrp_b, _        = fred_latest("RRPONTSYD")
    sofr, _         = fred_latest("SOFR")
    iorb, _         = fred_latest("IORB")
    hy_pct, _       = fred_latest("BAMLH0A0HYM2")
    tga_b, tga_date = tga_latest()
    ndx_dev, ndx_d  = ndx_deviation()
    pc, pc_src      = cboe_equity_pc()
    etf_b, etf_src  = qqq_weekly_flow()
    srf_b, _        = fred_latest("RPONTSYD")     # SRF 常備回購使用量（十億美元）
    sofr99, _       = fred_latest("SOFR99")       # SOFR 第99百分位（%）
    nlq_t, nlq_d13  = netliq_now_and_change()
    move            = yahoo_last("^MOVE")
    vix, vix3m      = yahoo_last("^VIX"), yahoo_last("^VIX3M")
    vixr            = round(vix / vix3m, 2) if (vix and vix3m) else None
    rsp_ratio, rsp_sig = rsp_spy_signal()

    data = {
        "date":      date.today().isoformat(),
        "res":       round(res_m / 1_000_000, 3) if res_m is not None else None,
        "res_asof":  res_date,
        "tga":       tga_b,  "tga_asof": tga_date,
        "rrp":       round(rrp_b) if rrp_b is not None else None,
        "sofr_iorb": round((sofr - iorb) * 100) if (sofr is not None and iorb is not None) else None,
        "hy":        round(hy_pct * 100) if hy_pct is not None else None,
        "ndx":       ndx_dev, "ndx_asof": ndx_d,
        "pc":        pc,      "pc_src": pc_src,
        "etf":       etf_b,   "etf_src": etf_src,
        "srf":       round(srf_b) if srf_b is not None else None,
        "s99":       round((sofr99 - iorb) * 100) if (sofr99 is not None and iorb is not None) else None,
        "nlq":       nlq_t,   "nlqd": nlq_d13,
        "move":      move,    "vixr": vixr,
        "rsp":       rsp_ratio, "rsp_sig": rsp_sig,
    }
    data["exit"] = exit_score(data)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("更新完成", data["date"], "→", json.dumps(data, ensure_ascii=False))

    alerts = []
    if data["res"] is not None and data["res"] < 2.5:
        alerts.append(f"☠ 終極死亡訊號：準備金 {data['res']}T 跌破 2.5 兆美元！啟動紅線 SOP。")
    elif data["res"] is not None and data["res"] < 3.0:
        alerts.append(f"🟡 準備金 {data['res']}T 位於 2.5–3.0T 黃色警戒區。")
    if data["sofr_iorb"] is not None and data["sofr_iorb"] > 25:
        alerts.append(f"🔴 SOFR−IORB 利差 +{data['sofr_iorb']}bp 尖峰：回購市場搶錢！")
    if data["hy"] is not None and data["hy"] > 450:
        alerts.append(f"🔴 HY OAS {data['hy']}bp 急擴：信用市場先跑。")
    if (data["ndx"] is not None and data["pc"] is not None
            and data["ndx"] > 8 and data["pc"] < 0.55):
        alerts.append(f"🔥 Gamma 軋空融漲點火：NDX 乖離 +{data['ndx']}%、P/C {data['pc']}。末段行情，嚴設移動停利。")
    if data["srf"] is not None and data["srf"] > 50:
        alerts.append(f"🔴 SRF 常備回購爆量 {data['srf']}B：交易商已在敲 Fed 後門，準備金實質不足！")
    if data["s99"] is not None and data["s99"] > 20:
        alerts.append(f"🔴 SOFR P99 利差 +{data['s99']}bp：資金分布尾端刺穿走廊，邊緣借款人搶錢中。")
    if data["vixr"] is not None and data["vixr"] > 1.0:
        alerts.append(f"🔴 VIX 期限結構倒掛（{data['vixr']}）：近月保險費超過遠月，恐慌前置。")
    if data["move"] is not None and data["move"] > 130:
        alerts.append(f"🟠 MOVE {data['move']}：公債波動率飆升，抵押品折價上調、系統性去槓桿風險。")
    if data.get("exit") is not None and data["exit"] >= 75:
        alerts.append(f"🔴 逐步撤出指數 {data['exit']}/100：進入第三階段——核心以外部位撤出、現金短債為主。")
    elif data.get("exit") is not None and data["exit"] >= 50:
        alerts.append(f"🟠 逐步撤出指數 {data['exit']}/100：進入第二階段——倉位減半、建立對沖。")
    if data["etf"] is not None and data["etf"] > 25:
        alerts.append(f"🟠 被動 ETF 極端流入：QQQ 週流入 +{data['etf']}B（機械買盤過熱）。")
    if alerts:
        notify("【 " + data["date"] + "】\n" + "\n".join(alerts))
    return data

# ── 歷史數據（供 index.html 的 10 年圖表使用） ─────────────────
HIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

def write_history(years=10):
    h = fred_history(years)
    if h["dates"]:
        with open(HIST_FILE, "w", encoding="utf-8") as f:
            json.dump(h, f, ensure_ascii=False)
        print("history.json 更新完成，共", len(h["dates"]), "筆")
    else:
        print("[WARN] 歷史數據為空，略過寫入 history.json", file=sys.stderr)

if __name__ == "__main__":
    collect_and_alert()
    write_history(10)

