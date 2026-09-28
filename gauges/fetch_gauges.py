#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_gauges.py — 市場五儀表的資料抓取（GitHub Actions 排程執行）

抓 VIX / 美元指數 / WTI 原油 / 黃金 / 美國 10 年期殖利率（另附 S&P 500 參考），
寫出同目錄的 data.json，由 workflow commit 回 repo；gauges/index.html 開啟時直接 fetch。

資料來源：
  1. Yahoo Finance v8 chart（免 Key）：^VIX, DX-Y.NYB, CL=F, GC=F, ^TNX, ^GSPC
  2. FRED（有 FRED_API_KEY 用官方 API，沒有則用 fredgraph.csv）：DGS2（2 年期）、T10Y2Y（10Y−2Y 利差）、DFII10（10 年期 TIPS 實質利率）；
     另作為 VIXCLS, DCOILWTICO, DGS10 的備援
  某項抓不到時沿用上一版 data.json 的數值，並標記 stale。

僅用標準函式庫，無需 pip install。
"""

import csv, io, json, os, ssl, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

OUT_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
MACRO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro.json")
TIMEOUT  = 30
CTX      = ssl.create_default_context()
UA       = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0.0.0 Safari/537.36"}

# id: (Yahoo 代碼, FRED 備援代碼)
SERIES = {
    "vix":  ("^VIX",     "VIXCLS"),
    "dxy":  ("DX-Y.NYB", None),
    "oil":  ("CL=F",     "DCOILWTICO"),
    "gold": ("GC=F",     None),
    "y10":  ("^TNX",     "DGS10"),
    "spx":  ("^GSPC",    None),
    "y2":     (None, "DGS2"),
    "curve":  (None, "T10Y2Y"),
    "real10": (None, "DFII10"),
}
BP_KEYS = {"y10", "y2", "curve", "real10"}   # 利率類用 bp 表示變化
MONTH, WEEK, SPARK = 21, 5, 66   # 交易日：約一個月、一週、三個月走勢

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")   # 與流動性儀表板共用同一個 GitHub Secret

def http_get(url, retries=3, timeout=TIMEOUT):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last

def yahoo_series(symbol):
    """回傳 [(YYYY-MM-DD, close)]，最後一筆可能是盤中價"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?range=6mo&interval=1d")
    res = json.loads(http_get(url))["chart"]["result"][0]
    out = []
    for ts, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if c is not None:
            out.append((datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"), float(c)))
    return out

def fred_api(series_id, **params):
    """FRED 官方 API（需 FRED_API_KEY），GitHub Actions 上比 fredgraph.csv 穩定"""
    url = ("https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(
        {"series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json", **params}))
    obs = json.loads(http_get(url, retries=2, timeout=20))["observations"]
    return [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "")]

def fred_csv(series_id, extra=""):
    """備援：fredgraph.csv（免 Key），在部分雲端主機上會很慢，所以只試一次"""
    txt = http_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + series_id + extra, retries=1, timeout=15)
    rows = list(csv.reader(io.StringIO(txt)))[1:]
    return [(d, float(v)) for d, v in rows if v not in (".", "")]

def fred_series(series_id):
    if FRED_API_KEY:
        return fred_api(series_id, sort_order="desc", limit=200)[::-1]
    return fred_csv(series_id)[-200:]

# 筆記頁的歷史圖（FRED 月資料）。mode：level＝原值、yoy＝年增率 %、diff＝月增減
MACRO = {
    "UNRATE":   ("失業率", "%", "level"),
    "CPIAUCSL": ("CPI 年增率", "%", "yoy"),
    "PPIFIS":   ("PPI（最終需求）年增率", "%", "yoy"),
    "PCEPILFE": ("核心 PCE 年增率", "%", "yoy"),
    "PAYEMS":   ("非農新增就業", "千人", "diff"),
    "FEDFUNDS": ("有效聯邦基金利率", "%", "level"),
    "UMCSENT":  ("密大消費者信心指數", "", "level"),
    "DFII10":   ("10 年期 TIPS 實質利率", "%", "level"),
    "T10Y2Y":   ("10Y−2Y 利差", "%", "level"),
}
MACRO_START = "2014-01-01"

def fred_monthly(series_id):
    if FRED_API_KEY:
        pts = fred_api(series_id, observation_start=MACRO_START, frequency="m", aggregation_method="avg")
    else:
        pts = fred_csv(series_id, "&cosd=" + MACRO_START + "&fq=Monthly&fam=avg")
    return [(d[:7], v) for d, v in pts]

def build_macro():
    try:
        with open(MACRO_FILE, encoding="utf-8") as f:
            prev = json.load(f).get("series", {})
    except Exception:
        prev = {}
    out = {}
    for sid, (name, unit, mode) in MACRO.items():
        try:
            pts = fred_monthly(sid)
            if mode == "yoy":
                pts = [(pts[i][0], round((pts[i][1] / pts[i - 12][1] - 1) * 100, 2)) for i in range(12, len(pts))]
            elif mode == "diff":
                pts = [(pts[i][0], round(pts[i][1] - pts[i - 1][1], 1)) for i in range(1, len(pts))]
            pts = [p for p in pts if p[0] >= "2015-01"]
            out[sid] = {"name": name, "unit": unit, "d": [p[0] for p in pts], "v": [round(p[1], 2) for p in pts]}
        except Exception as e:
            print(f"[WARN] FRED 月資料 {sid}: {e}", file=sys.stderr)
            if sid in prev:
                out[sid] = prev[sid]
    if not out:
        return
    if out == prev:
        return   # 沒有新數據就不改檔，避免多餘的 commit
    with open(MACRO_FILE, "w", encoding="utf-8") as f:
        json.dump({"series": out}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[OK] 筆記歷史圖 {len(out)}/{len(MACRO)} 項 → {MACRO_FILE}")

def summarize(key, pts):
    if key == "y10" and pts and pts[-1][1] > 20:   # 舊版 ^TNX 以 ×10 報價
        pts = [(d, v / 10) for d, v in pts]
    last_d, last = pts[-1]
    m = pts[-1 - MONTH][1] if len(pts) > MONTH else pts[0][1]
    w = pts[-1 - WEEK][1] if len(pts) > WEEK else pts[0][1]
    if key in BP_KEYS:   # 殖利率用 bp
        chg_m, chg_w = round((last - m) * 100, 1), round((last - w) * 100, 1)
    else:
        chg_m, chg_w = round((last / m - 1) * 100, 2), round((last / w - 1) * 100, 2)
    return {"val": round(last, 4), "date": last_d, "chg_1m": chg_m, "chg_1w": chg_w,
            "spark": [round(v, 4) for _, v in pts[-SPARK:]]}

def main():
    try:
        with open(OUT_FILE, encoding="utf-8") as f:
            prev = json.load(f).get("series", {})
    except Exception:
        prev = {}

    series, ok = {}, 0
    for key, (ysym, fid) in SERIES.items():
        item = None
        try:
            if not ysym:
                raise LookupError("無 Yahoo 代碼")
            item = summarize(key, yahoo_series(ysym)); item["source"] = "Yahoo Finance " + ysym
        except Exception as e:
            if ysym:
                print(f"[WARN] Yahoo {ysym}: {e}", file=sys.stderr)
            if fid:
                try:
                    item = summarize(key, fred_series(fid)); item["source"] = "FRED " + fid
                except Exception as e2:
                    print(f"[WARN] FRED {fid}: {e2}", file=sys.stderr)
        if item:
            ok += 1
        elif key in prev:
            item = {**prev[key], "stale": True}
        if item:
            series[key] = item

    if ok == 0:
        print("[ERROR] 所有來源都失敗，保留原本的 data.json", file=sys.stderr)
        sys.exit(1)

    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "series": series}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[OK] {ok}/{len(SERIES)} 項更新 → {OUT_FILE}")
    build_macro()

if __name__ == "__main__":
    main()
