# Fed Liquidity Dashboard（GitHub Pages 版）

原本 `fed_dashboard.py` 是「雙擊執行→本機開瀏覽器」的單檔程式。這個版本拆成三塊：

- `index.html` — 純靜態儀表盤（原本內嵌在 Python 裡的那段 HTML/CSS/JS），只會 `fetch` 同目錄的 `data.json`、`history.json`，本身不執行任何 Python。
- `fetch_liquidity.py` — 抓資料的部分，寫出 `data.json` / `history.json` / `qqq_so_history.json`。
- `.github/workflows/update.yml` — GitHub Actions，排程執行 `fetch_liquidity.py` 並把更新後的 json 檔 commit 回 repo。

這樣瀏覽器打開的是純靜態頁面（GitHub Pages），資料更新則交給 GitHub 的伺服器排程處理，你不需要留一台電腦開著。

## ⚠️ 先處理這件事

原始程式碼裡 `FRED_API_KEY` 的預設值寫死了一組看起來是真的 API key。這個版本已經把它拿掉、改成只吃環境變數，**但你原本那組 key 建議去 FRED 官網重新申請一組新的**，並停用舊的那組，因為它曾經明文出現在檔案裡。

## 設定步驟

1. **建立 repo**：在 GitHub 建一個新 repository（可以是 private，Pages 免費方案 private repo 也能用；若要用 GitHub Free 的 public Pages 則設 public）。
2. **上傳這幾個檔案**：把這個資料夾的內容（含 `.github` 目錄）全部放進 repo 根目錄，push 上去。
3. **申請 FRED API Key**（免費）：https://fred.stlouisfed.org/docs/api/api_key.html
4. **設定 GitHub Secret**：repo → Settings → Secrets and variables → Actions → New repository secret
   - `FRED_API_KEY`：貼上你的 key（必填）
   - `LINE_CHANNEL_TOKEN`：如果要 LINE 推播警報才填，不用可留空不建
   - `WEBHOOK_URL`：如果要 Teams/Slack/Discord 推播才填
5. **打開 GitHub Pages**：repo → Settings → Pages → Source 選 `Deploy from a branch` → Branch 選 `main` / `(root)` → Save。幾分鐘後會給你一個網址，例如 `https://你的帳號.github.io/repo名稱/`。
6. **手動跑一次 Action 驗證**：repo → Actions → 選 "Update liquidity data" → Run workflow（右上角按鈕）。跑完後確認 repo 裡的 `data.json`、`history.json` 有被更新（commit 記錄會出現 `auto: update liquidity data ...`）。
7. 之後就會照 `update.yml` 裡的排程（預設週一到週五台灣時間 18:10）自動更新，開啟 Pages 網址即可看到最新資料，不用再本機執行。

## 之後想調整

- 排程時間：改 `.github/workflows/update.yml` 裡的 `cron`（UTC 時間）。
- 想改成每天都更新（含週末）：把 `1-5` 改成 `*`。
- Yahoo Finance 的 QQQ 流通股數歷史（`qqq_so_history.json`）需要跨週比較，第一次執行後會持續累積，這個檔案也會被 Action commit 回去，不要手動刪除。
