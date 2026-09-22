# Fed Liquidity Dashboard（GitHub Pages 版）

- `index.html` — 純靜態儀表盤（原本內嵌在 Python 裡的那段 HTML/CSS/JS），只會 `fetch` 同目錄的 `data.json`、`history.json`，本身不執行任何 Python。
- `fetch_liquidity.py` — 抓資料的部分，寫出 `data.json` / `history.json` / `qqq_so_history.json`。
- `.github/workflows/update.yml` — GitHub Actions，排程執行 `fetch_liquidity.py` 並把更新後的 json 檔 commit 回 repo。

**手動跑一次 Action 驗證**：repo → Actions → 選 "Update liquidity data" → Run workflow（右上角按鈕）。跑完後確認 repo 裡的 `data.json`、`history.json` 有被更新（commit 記錄會出現 `auto: update liquidity data ...`）。

## 之後想調整
- 排程時間：改 `.github/workflows/update.yml` 裡的 `cron`（UTC 時間）。
- 想改成每天都更新（含週末）：把 `1-5` 改成 `*`。
- Yahoo Finance 的 QQQ 流通股數歷史（`qqq_so_history.json`）需要跨週比較，第一次執行後會持續累積，這個檔案也會被 Action commit 回去，不要手動刪除。
