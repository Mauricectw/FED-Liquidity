# Liquidity Dashboard

- `index.html` — 純靜態dashboard（原本內嵌在 Python 裡的那段 HTML/CSS/JS），只會 `fetch` 同目錄的 `data.json`、`history.json`
- `fetch_liquidity.py` — 抓資料的部分，寫出 `data.json` / `history.json` / `qqq_so_history.json`。
- `.github/workflows/update.yml` — GitHub Actions，排程執行 `fetch_liquidity.py` 並把更新後的 json 檔 commit 回 repo。

Action 驗證**：repo → Actions → 選 "Update liquidity data" → Run workflow（右上角按鈕）。跑完後確認 repo 裡的 `data.json`、`history.json` 有被更新（commit 記錄會出現 `auto: update liquidity data ...`）。

- 排程時間：改 `.github/workflows/update.yml` 裡的 `cron`（UTC 時間）。
- 想改成每天都更新（含週末）：把 `1-5` 改成 `*`。
- Yahoo Finance 的 QQQ 流通股數歷史（`qqq_so_history.json`）需要跨週比較，第一次執行後會持續累積，這個檔案也會被 Action commit 回去，不要手動刪除。

## 市場五儀表（`gauges/`）

- `gauges/index.html` — VIX / 美元指數 / 原油 / 黃金 / 10 年期殖利率的紅黃綠燈儀表板，開啟時 fetch 同目錄的 `data.json`，開著時每 10 分鐘重讀。
- `gauges/fetch_gauges.py` — 從 Yahoo Finance 抓報價（VIX、原油、10Y 失敗時改用 FRED 免 Key CSV），另從 FRED 抓 2Y、10Y−2Y、10 年期 TIPS 實質利率，寫出 `gauges/data.json`。只用標準函式庫。
- `.github/workflows/update-gauges.yml` — 美股交易日每小時跑一次，另在收盤後與亞洲早盤各跑一次；可在 Actions → "Update market gauges" → Run workflow 手動測試。
- `gauges/notes.md` — 總經傳導筆記，`gauges/notes.html` 會讀取並顯示；指標卡的「為什麼」、今天應驗的傳導與傳導地圖都連到這裡。要改筆記直接編輯這個檔案。
- 網址：GitHub Pages 啟用後為 `https://mauricectw.github.io/FED-Liquidity/gauges/`。
