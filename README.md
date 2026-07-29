# 台股每日多空、轉折與極端情緒報告

這是一個不需要常駐伺服器的自動化專案。它在 GitHub Actions 擷取公開市場資料、產生市場狀態與靜態儀表板，部署到 GitHub Pages，並可透過 LINE Messaging API 推播。

## 已實作

- 台北時間每個交易日 `22:00` 收盤報告，以及 `08:15` 盤前海外更新。
- 七模組可解釋分數、強多／轉多／盤整／轉空／強空狀態及三日冷卻機制。
- 上市、上櫃漲跌停家數、有效股票比例、1年／5年百分位、歷史相似日與後續報酬。
- 法人、融資融券、借券、期貨基差、非三大法人代理、Put/Call、VIX、選擇權壓力區的統一資料介面。
- 美國 VIX、費半、TSM ADR、USD/TWD 盤前疊加；免費來源失效時自動回到中性並標示資料延遲。
- 單檔自包含 HTML、`latest.json`、日期歸檔、LINE 去重、JSONL 歷史儲存。
- 純標準函式庫測試與 fixture 端到端模式。
- 漲跌停單日／3日／5日增量回測與2個百分點命中率安全閘門；未通過前只作診斷顯示。

## 快速開始

需要 Python 3.11 以上：

```bash
python -m tw_market_report.cli fixture
python -m http.server 8000 --directory docs
```

瀏覽 `http://localhost:8000`。正式資料模式：

```bash
python -m tw_market_report.cli run --mode close --notify
python -m tw_market_report.cli run --mode premarket --notify
```

首次部署可分段回填官方歷史資料（建議每次一至三個月，避免對官方網站造成壓力）：

```bash
python -m tw_market_report.cli backfill --start 2020-01-01 --end 2020-03-31
```

回填會以證交所指定日期的市場統計，加上櫃買逐股收盤資料和前一交易日公告的次日漲跌停價交叉核對。任何一邊無法核對時會跳過該日，不會用約10%推算。

若未安裝套件，先在專案根目錄執行：

```bash
python -m pip install -e .
```

也可以不安裝，使用 `PYTHONPATH=src` 執行。

## GitHub 設定

1. 建立 GitHub repository 並推送本專案。
2. Repository Settings → Actions → General，將 Workflow permissions 設為 Read and write。
3. Settings → Pages，來源選 GitHub Actions。
4. 若要 LINE 推播，新增 Secrets：
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_USER_ID`
5. 將 `config/report.json` 的 `shadow_mode` 保持 `true` 觀察至少60個交易日；驗收後再改為 `false` 顯示正式曝險區間。

歷史回填可直接在 Actions 頁面手動執行 `Backfill market history`。建議分段選取一至三個月；工作流會先跑測試，再將可核對的日期寫回 `data/history.jsonl`。

排程採 UTC：`00:15` 對應台北 `08:15`，`14:00` 對應台北 `22:00`。程式會再次檢查台北日期與週末，避免時區誤判。

## 資料品質原則

- 來源回傳欄位異動或日期不符時，該模組標示 `partial`，不把缺值當成0。
- 國內核心價格資料缺失時，不產生新市場狀態，只保留最後有效狀態。
- 借券餘額與借券賣出餘額分開；小台／微台只稱「非三大法人部位代理」。
- 選擇權未平倉集中只稱「OI集中壓力區」。
- 漲跌停歷史比較使用有效股票比例，不直接用跨年度絕對家數。
- 即時官方端點若只提供市場摘要，頁面會標成「官方摘要口徑」，且漲跌停不進總分；只有具官方逐股漲跌停價、完成普通股母體核對的資料才標成「逐股核對」。
- GitHub Actions 會把更新後的 `data/history.jsonl` 與推播去重狀態提交回 repository；若分支保護阻擋提交，儀表板仍會部署，但歷史不會跨執行保存。

## 測試

```bash
python -m unittest discover -s tests -v
```

這是市場風險監測工具，不是個人化投資建議，也不執行下單。
