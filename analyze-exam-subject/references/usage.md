# 安裝與工具使用

大量題庫預設採 [精簡考點分析](compact-analysis.md)，另有純標準函式庫的 `scripts/skill.py` 提供 prepare／packet／status／merge／summary。AI 不重複填寫來源欄位，統計與分析快取在本機處理；inventory／extract／render 的參數語意保留，但舊 Python 腳本入口已移除，請改用下列統一入口。

## 安裝

整個 `analyze-exam-subject` 資料夾可直接攜帶，不匯入爬蟲程式碼。從原始套件複製，保留 core、interfaces、plugins（含 report_html/template.html）、scripts 與 references 的相對結構；不要只複製 SKILL.md。安裝前檢查目標是否已有同名版本，勿盲目覆寫。

- Antigravity 專案範圍：`<專案>/.agents/skills/analyze-exam-subject/`。官方目前預設 `.agents/skills`，並相容舊 `.agent/skills`。參考 https://antigravity.google/docs/skills?app=antigravity-ide （2026-11-02 查核）。
- Codex 個人範圍：`$CODEX_HOME/skills/analyze-exam-subject/`；未設定 CODEX_HOME 時使用 `~/.codex/skills/analyze-exam-subject/`。

本套件不自動安裝或修改全域設定。安裝後依宿主方式重新載入技能，確認清單可見；也可明確要求宿主讀取 SKILL.md。兩個宿主的對話選取介面可能不同，文字路徑及編號選取是共同替代方式。

## 環境

Python 3.10+；盤點只用標準函式庫。PDF 擷取需要 pypdf。缺少時，先提示使用者在選定環境執行 `python -m pip install pypdf`，不靜默安裝。無 OCR 引擎或宿主視覺能力時，掃描頁只能回報待視覺檢查。所有命令中的路徑請加引號；以下以技能資料夾為目前目錄；從其他目錄執行時，請將 scripts/skill.py 換成技能內該檔的絕對路徑。完整依賴見 [`requirements.txt`](../requirements.txt)：`pypdf>=6,<7`、`pypdfium2>=5,<6`、`Pillow>=10.1`。

逐頁圖片功能需要 `pypdfium2` 與 `Pillow`；缺少時提示 `python -m pip install pypdfium2 Pillow`，不自動安裝。圖片產生不需要視覺模型；判讀需要宿主實際提供圖片工具。無視覺時保留文字分析與待檢視圖片，不使用外部 OCR API。參閱 [視覺檢查及 render 指令](visual-review.md)。

## 統一入口與分段管線

2.0.0 只保留 `python scripts/skill.py <command> ...`；沒有舊版轉接層。
可用命令：run-dir、inventory、extract、render、prepare、packet、status、merge、summary、report-html、check、clean-images，以及 scan、finalize、revise。
命令啟動時只載入所需插件及依賴，完成或失敗後反向卸載；資料透過明確回傳值傳遞，事件只用於通知。

```shell
python scripts/skill.py scan "D:/題庫" --subject "資料結構"
python scripts/skill.py finalize --run-dir "D:/題庫/_analysis/資料結構/本次" --results result-001.json result-002.json --visual-review visual-review.json --require-complete
python scripts/skill.py report-html --run-dir "D:/題庫/_analysis/資料結構/本次"
python scripts/skill.py check "D:/題庫/_analysis/資料結構/本次/report.html"
```

- `scan` 依序建立唯一目錄、盤點，stdout 回傳 JSON，含 `run_dir` 與 `inventory`；支援 `--manifest` 與 `--output-root`。尚未確認備考科目時可先用「盤點」作工作目錄名稱。
- 盤點後宿主固定出 3 題與使用者確認 selection（Q1 備考科目名稱、Q2 試卷編號多選、Q3 目標考試／等別／類科；已在指令中指定者略過）；渲染後宿主看圖建立 topics；prepare／packet 產生工作包後，宿主逐批保存 results 與 visual-review。命令成功不代表 AI 已完成判讀。
- `finalize` 依序執行 merge／summary，讀取 run-dir 下的 work.json；可用 `--work` 改指定。未明確指定 visual-review 或 extraction 時，僅採用同一 run-dir 已存在的對應檔案。
- results 必須明確列出，不掃描猜測結果檔；全部命中有效快取時才可不提供。保留 `--fast-verify`、`--require-complete`、`--target` 與 `--examples` 語意。
- finalize 固定寫出同目錄的 question-index.json 與 summary.json，開始前先確認兩者都不存在。任一步驟失敗即停止，已完成成果保留，不回滾；例如合併成功但統計失敗，可使用 summary 子命令接續。
- 宿主依 summary 撰寫 report.md；只在使用者要求時呼叫 report-html。revise 只使用既有成果，不重新盤點、看圖或分類。
- 保留來源唯讀、雜湊快取及既有資料 schema；不新增工作流狀態檔或自動 resume 排程。
- 成功回傳 0；一般失敗或參數錯誤回傳 2，check 健檢失敗回傳 1。錯誤寫入 stderr。

### 盤點後固定詢問與使用者回覆範例

`scan` 產出盤點結果後，宿主呈報官方科目分群清單，並固定提出以下 3 題以組裝 `selection.json`（若使用者起始指令已指定則略過相應題目）：

```text
📋 題庫盤點已完成：
[1] 資料結構（108-112 年，共 5 份試卷）
[2] 資料結構與資料庫（110-113 年合科卷，共 4 份試卷）
[3] 科目待確認檔案（2 份試卷，檔名未含明確考科）

請確認以下 3 個設定以建立分析選取（selection）：
Q1. 本次備考科目名稱：（預設建議：資料結構）
Q2. 要納入分析的試卷編號（多選，可附加自訂劃界規則）：（請填編號，例如 1,2 或 1,2,3；若需限定特定領域，可一併在此指定規則，例如「限資安相關題目，純網路題排除」）
Q3. 目標考試／等別／類科（選填）：（例如：高考三級 資訊處理；無特定目標請填「無」）
```

使用者回覆範例：
- 基本回覆：`Q1: 資料結構, Q2: 1, 2, Q3: 高考三級資訊處理` 或簡答 `1. 資料結構 2. 1,2 3. 無`。
- 帶自訂劃界規則之回覆：
  `1. 資訊安全 2. 1, 2（規則：分類主題必須涉入資安，純網路概念不得為主題；若是純網路題完全無關資安，標記為不相關排除） 3. 高考三級`
  此自訂規則會完整保存於 `selection.json` 的 `custom_rules`，參與快取指紋計算並注入各批模型工作包（Packet Rules），確保 AI 判讀與主題建表嚴格遵循。
宿主隨即保存 `selection.json` 並接續執行 `render`。

## 執行目錄

先建立唯一目錄，避免不同次分析互相覆寫；命令會印出實際路徑，後續輸出都放在該目錄：

```shell
python scripts/skill.py run-dir "D:/題庫" --subject "資料結構"
python scripts/skill.py run-dir "D:/題庫" --subject "資料結構" --output-root "D:/分析輸出"
```

工具會清理 `<>:"/\|?*`、控制字元、尾端句點與 Windows 保留名稱（`CON`、`NUL`、`COM1` 等），並使用時間戳加隨機碼。未指定 `--output-root` 時輸出根目錄固定在來源的 `_analysis` 下；來源唯讀時用外部可寫入根目錄，工具仍會拒絕連結目錄。

```shell
python scripts/skill.py inventory "D:/題庫" --output "D:/題庫/_analysis/盤點/inventory.json"
python scripts/skill.py inventory "D:/題庫" --manifest "D:/下載記錄/manifest.sqlite3" --output "D:/題庫/_analysis/盤點/inventory-with-metadata.json"
python scripts/skill.py extract --inventory "D:/題庫/_analysis/盤點/inventory.json" --pages 1 --output "D:/題庫/_analysis/盤點/headers.json"
python scripts/skill.py extract --inventory "D:/題庫/_analysis/盤點/inventory.json" --selection "D:/題庫/_analysis/資料結構/本次/selection.json" --output "D:/題庫/_analysis/資料結構/本次/extraction.json"
```

`--pages` 為一基頁碼，以逗號分隔，例如 `1,2`；未提供代表全文。未提供 selection 時只允許首頁預覽，防止選取前擷取全部試卷。輸出以暫存檔原子發佈，必須是不存在的新檔，不覆寫來源。`--resume` 接受先前的 extraction.json，重用合格結果但仍輸出到新檔；render 的 `--resume` 接受 render-index.json，同一個 output 只允許相同選取、相同頁碼範圍與相同渲染設定恢復，否則請使用新輸出。渲染逐頁寫 append-only journal，文件結束才折回索引；圖片不覆寫。文字與渲染索引不可混用。

盤點檔案讀取失敗、資料夾存取失敗與未知 manifest 會列入 warnings；PDF 失敗會逐檔回報，其他檔案繼續。命令成功不表示所有 PDF 都成功，應檢查每筆 status。

## 精簡分析

先完成 selection 與 render，再依 [精簡考點分析](compact-analysis.md) 跨來源抽樣建立分類表、設定分類版本，才執行 prepare。`prepare` 會檢查每個已選來源都有 render 索引、至少一頁可用；缺頁只會產生 warning 與 `missing_pages`，不會靜默假裝完整。`--no-cache` 可強制重跑，`--prior` 則重用完整快取。

```shell
python scripts/skill.py prepare --inventory inventory.json --selection selection.json --render-index render-index.json --topics topics.json --output work.json
python scripts/skill.py status --work work.json
python scripts/skill.py packet --work work.json --batch-id "S來源短ID-1" --context 1 --output packet-001.json
python scripts/skill.py merge --work work.json --results result-001.json --visual-review visual-review.json --extraction extraction.json --output question-index.json
python scripts/skill.py summary --index question-index.json --target "高考" --output summary.json
```

summary 請輸出到與 `report.md` 相同的執行目錄；它會依輸出位置計算相對於報告的考題連結（`link`）。執行目錄在題庫內（預設 `_analysis`）時連結隨題庫一起搬移仍有效；使用外部 `--output-root` 時 `link` 為 null，報告改以題庫相對路徑文字標示。

### 題庫搬移後

1. 對新位置重新執行 `inventory`（root 會更新）。
2. 沿用原 selection；只要每筆有 `relative_path`，舊的絕對 `path` 失效也能還原。舊 selection 只有 `path` 時，從新 inventory 補上 `relative_path`。
3. 重新 `render`（圖片位置可能隨 `_analysis` 改變）、`prepare --prior 舊的 question-index.json`；內容雜湊未變時直接命中快取，不必重跑 AI。
4. 只想讓既有報告連結繼續可用時什麼都不必做：報告連結是相對路徑，題庫與 `_analysis` 一起搬即可。

### 快速重新生成與報告健檢（-revise）

當使用者已完成題庫分析，但修訂了 `report.md` 講義內容、微調了前端樣式、或更新了編譯器版本時，可使用 Skill 的 `-revise` 模式（內部對應 `revise` 子命令）快速重新生成 HTML 報告並進行完整度健檢，**無需重新執行任何考古題分析流程**：

```shell
# 傳入 _analysis 目錄（自動解析尋找最新成果目錄並編譯、健檢）
python scripts/skill.py revise "D:/題庫/_analysis"

# 傳入題庫根目錄亦可自動定位
python scripts/skill.py revise "D:/題庫"
```

腳本會自動同步最新報告至題庫根目錄（`report.html` 與 `<備考科目>_考點分析報告.html`），並在終端機輸出結構化健檢卡片。

### 空間瘦身（清理 render-images）

分析完成後，`render-images/` 內的逐頁 PNG 圖檔（150 DPI 約每頁 500KB～700KB）不再參與日常閱讀與試題連結。若磁碟空間有限，可執行以下指令安全清理，只刪除暫存圖片，保留所有 JSON 索引與報告：

```shell
python scripts/skill.py clean-images "D:/題庫/_analysis/資料結構/本次"
python scripts/skill.py clean-images "D:/題庫"
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
