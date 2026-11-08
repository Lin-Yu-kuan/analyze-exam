# 安裝與工具使用

大量題庫預設採 [精簡考點分析](compact-analysis.md)，另有純標準函式庫的 `scripts/compact_analysis.py` 提供 prepare／packet／status／merge／summary。AI 不重複填寫來源欄位，統計與分析快取在本機處理；原有 inventory／extract／render 介面保持相容。

## 安裝

整個 `analyze-exam-subject` 資料夾可直接攜帶，不匯入爬蟲程式碼。從原始套件複製，保留 scripts 與 references 相對結構；不要只複製 SKILL.md。安裝前檢查目標是否已有同名版本，勿盲目覆寫。

- Antigravity 專案範圍：`<專案>/.agents/skills/analyze-exam-subject/`。官方目前預設 `.agents/skills`，並相容舊 `.agent/skills`。參考 https://antigravity.google/docs/skills?app=antigravity-ide （2026-11-02 查核）。
- Codex 個人範圍：`$CODEX_HOME/skills/analyze-exam-subject/`；未設定 CODEX_HOME 時使用 `~/.codex/skills/analyze-exam-subject/`。

本套件不自動安裝或修改全域設定。安裝後依宿主方式重新載入技能，確認清單可見；也可明確要求宿主讀取 SKILL.md。兩個宿主的對話選取介面可能不同，文字路徑及編號選取是共同替代方式。

## 環境

Python 3.10+；盤點只用標準函式庫。PDF 擷取需要 pypdf。缺少時，先提示使用者在選定環境執行 `python -m pip install pypdf`，不靜默安裝。無 OCR 引擎或宿主視覺能力時，掃描頁只能回報待視覺檢查。所有命令中的路徑請加引號；以下以技能資料夾為目前目錄。完整依賴見 [`requirements.txt`](../requirements.txt)：`pypdf>=6,<7`、`pypdfium2>=5,<6`、`Pillow>=10.1`。

逐頁圖片功能需要 `pypdfium2` 與 `Pillow`；缺少時提示 `python -m pip install pypdfium2 Pillow`，不自動安裝。圖片產生不需要視覺模型；判讀需要宿主實際提供圖片工具。無視覺時保留文字分析與待檢視圖片，不使用外部 OCR API。參閱 [視覺檢查及 render 指令](visual-review.md)。

## 執行目錄

先建立唯一目錄，避免不同次分析互相覆寫；命令會印出實際路徑，後續輸出都放在該目錄：

```shell
python scripts/exam_files.py run-dir "D:/題庫" --subject "資料結構"
python scripts/exam_files.py run-dir "D:/題庫" --subject "資料結構" --output-root "D:/分析輸出"
```

工具會清理 `<>:"/\|?*`、控制字元、尾端句點與 Windows 保留名稱（`CON`、`NUL`、`COM1` 等），並使用時間戳加隨機碼。未指定 `--output-root` 時輸出根目錄固定在來源的 `_analysis` 下；來源唯讀時用外部可寫入根目錄，工具仍會拒絕連結目錄。

```shell
python scripts/exam_files.py inventory "D:/題庫" --output "D:/題庫/_analysis/盤點/inventory.json"
python scripts/exam_files.py inventory "D:/題庫" --manifest "D:/下載記錄/manifest.sqlite3" --output "D:/題庫/_analysis/盤點/inventory-with-metadata.json"
python scripts/exam_files.py extract --inventory "D:/題庫/_analysis/盤點/inventory.json" --pages 1 --output "D:/題庫/_analysis/盤點/headers.json"
python scripts/exam_files.py extract --inventory "D:/題庫/_analysis/盤點/inventory.json" --selection "D:/題庫/_analysis/資料結構/本次/selection.json" --output "D:/題庫/_analysis/資料結構/本次/extraction.json"
```

`--pages` 為一基頁碼，以逗號分隔，例如 `1,2`；未提供代表全文。未提供 selection 時只允許首頁預覽，防止選取前擷取全部試卷。輸出以暫存檔原子發佈，必須是不存在的新檔，不覆寫來源。`--resume` 接受先前的 extraction.json，重用合格結果但仍輸出到新檔；render 的 `--resume` 接受 render-index.json，同一個 output 只允許相同選取、相同頁碼範圍與相同渲染設定恢復，否則請使用新輸出。渲染逐頁寫 append-only journal，文件結束才折回索引；圖片不覆寫。文字與渲染索引不可混用。

盤點檔案讀取失敗、資料夾存取失敗與未知 manifest 會列入 warnings；PDF 失敗會逐檔回報，其他檔案繼續。命令成功不表示所有 PDF 都成功，應檢查每筆 status。

## 精簡分析

先完成 selection 與 render，再依 [精簡考點分析](compact-analysis.md) 跨來源抽樣建立分類表、設定分類版本，才執行 prepare。`prepare` 會檢查每個已選來源都有 render 索引、至少一頁可用；缺頁只會產生 warning 與 `missing_pages`，不會靜默假裝完整。`--no-cache` 可強制重跑，`--prior` 則重用完整快取。

```shell
python scripts/compact_analysis.py prepare --inventory inventory.json --selection selection.json --render-index render-index.json --topics topics.json --output work.json
python scripts/compact_analysis.py status --work work.json
python scripts/compact_analysis.py packet --work work.json --batch-id "S來源短ID-1" --context 1 --output packet-001.json
python scripts/compact_analysis.py merge --work work.json --results result-001.json --visual-review visual-review.json --extraction extraction.json --output question-index.json
python scripts/compact_analysis.py summary --index question-index.json --target "高考" --output summary.json
```

summary 請輸出到與 `report.md` 相同的執行目錄；它會依輸出位置計算相對於報告的考題連結（`link`）。執行目錄在題庫內（預設 `_analysis`）時連結隨題庫一起搬移仍有效；使用外部 `--output-root` 時 `link` 為 null，報告改以題庫相對路徑文字標示。

### 題庫搬移後

1. 對新位置重新執行 `inventory`（root 會更新）。
2. 沿用原 selection；只要每筆有 `relative_path`，舊的絕對 `path` 失效也能還原。舊 selection 只有 `path` 時，從新 inventory 補上 `relative_path`。
3. 重新 `render`（圖片位置可能隨 `_analysis` 改變）、`prepare --prior 舊的 question-index.json`；內容雜湊未變時直接命中快取，不必重跑 AI。
4. 只想讓既有報告連結繼續可用時什麼都不必做：報告連結是相對路徑，題庫與 `_analysis` 一起搬即可。

### 快速重新生成與報告健檢（-revise）

當使用者已完成題庫分析，但修訂了 `report.md` 講義內容、微調了前端樣式、或更新了編譯器版本時，可使用 `--revise` 旗標快速重新生成 HTML 報告並進行完整度健檢，**無需重新執行任何考古題分析流程**：

```shell
# 傳入 _analysis 目錄（自動解析尋找最新成果目錄並編譯、健檢）
python scripts/build_report_html.py --revise "D:/題庫/_analysis"

# 傳入題庫根目錄亦可自動定位
python scripts/build_report_html.py --revise "D:/題庫"
```

腳本會自動同步最新報告至題庫根目錄（`report.html` 與 `<備考科目>_考點分析報告.html`），並在終端機輸出結構化健檢卡片。

### 空間瘦身（清理 render-images）

分析完成後，`render-images/` 內的逐頁 PNG 圖檔（150 DPI 約每頁 500KB～700KB）不再參與日常閱讀與試題連結。若磁碟空間有限，可執行以下指令安全清理，只刪除暫存圖片，保留所有 JSON 索引與報告：

```shell
python scripts/exam_files.py clean-images "D:/題庫/_analysis/資料結構/本次"
python scripts/exam_files.py clean-images "D:/題庫"
```

由於 PDF 渲染為 100% 確定性（Deterministic），未來若有重新核對細節之需求，再次執行 `render` 即可恢復相同雜湊之圖片。

批次回覆、merge 選項、目標考試切換與 AI 快取條件統一見 [精簡考點分析](compact-analysis.md)；視覺完成判定見 [視覺檢查](visual-review.md)。`question-index` 使用 schema 3，`summary` 使用 schema 2，欄位見 [資料契約](output-contract.md)。

文字擷取恢復只重用完整擷取，且來源內容雜湊、工具版本、pypdf 版本與頁碼範圍皆一致的結果；不符合時重新擷取。渲染恢復與圖片快取條件統一見視覺檢查文件。

## Manifest 邊界

只支援本專案 SQLite schema_version 1／2 的 `meta`、`attachments` 與 `snapshot_records` 已知欄位，使用 SQLite `mode=ro` 及 query_only，不呼叫會初始化／遷移資料庫的爬蟲類別。

metadata 只在附件 SHA-256 與實際檔案相符時掛載，保留各 snapshot／record／asset 的關聯；資料庫 path 不作為讀取任意檔案的指令。未知版本、缺表或讀取錯誤回報 warning 並退回檔名／表頭人工辨識。舊版 attachment_versions 不自動猜測歸屬；若舊檔雜湊未命中，視為 metadata 未確認。

## 驗證

在有 pypdf、pypdfium2 與 Pillow 的環境執行 `python -m unittest discover -s tests -v`。渲染測試會在缺少 pypdfium2 或 Pillow 時 skip，不會讓整個測試模組 ImportError。測試使用自行產生的 PDF，不讀取使用者試卷。`tests/behavior-cases.md` 提供宿主 AI 人工驗收案例；腳本測試不能證明語意分類正確。

目前驗證結果與尚未完成的雙平台測試見 [驗證紀錄](../tests/validation.md)。
