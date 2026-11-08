# 命令管線與插件依賴對照表 (Command Pipeline Matrix)

本文件整理自 2.0.0 插件架構（`scripts/_bootstrap.py`），供快速定位各 CLI 命令的執行管線、插件依賴注入關係與檔案產出契約。

## 一、 複合管線命令 (Multi-Step Pipelines)

| CLI 命令 | 步驟插件執行順序 (`PIPELINES`) | 連帶載入的底層與注入依賴 | 階段產出檔案 (`StageResult.artifacts`) | 主要用途與行為 |
| :--- | :--- | :--- | :--- | :--- |
| **`scan`** | 1. `WorkspacePlugin`<br/>2. `InventoryPlugin` | `cli`<br/>`pipeline`<br/>`workspace`<br/>`manifest` (選用) | `run_dir` 執行目錄<br/>`inventory.json` | 建立唯一分析目錄，盤點 PDF 並計算 SHA-256（支援 SQLite manifest） |
| **`finalize`** | 1. `MergePlugin`<br/>2. `SummaryPlugin` | `cli`<br/>`pipeline`<br/>`workspace` (注入 `storage`) | `question-index.json`<br/>`summary.json` | 合併各批次結果、核對覆蓋率，並計算命題事件去重與配分統計 |
| **`revise`** | 1. `WorkspacePlugin`<br/>2. `ReportHtmlPlugin`<br/>3. `ReportCheckPlugin` | `cli`<br/>`pipeline`<br/>`workspace` (注入 `storage`)<br/>`summary` (注入 `statistics`) | 最新 `report.html`<br/>題庫根目錄同步 HTML<br/>健檢卡片數據 | 快速重建模式：不重新分析題庫，自動定位最新成果、重編 HTML 並健檢 |

---

## 二、 單步子命令 (Single-Step Commands)

| CLI 命令 | 執行插件 (`FACTORIES`) | 建構子注入依賴 | 產出檔案 / 影響資源 | 職責與關鍵特性 |
| :--- | :--- | :--- | :--- | :--- |
| **`run-dir`** | `WorkspacePlugin` | 無 | 唯一執行目錄路徑 | 清理不合法字元，建立防覆寫目錄，提供原子寫檔 API |
| **`inventory`** | `InventoryPlugin` | `storage`: `workspace`<br/>`manifest`: `manifest` (選用) | `inventory.json` | 掃描題庫 PDF，掛載已確認的考試/科目 metadata |
| **`extract`** | `ExtractPlugin` | `storage`: `workspace` | `headers.json`<br/>`extraction.json` | 延後載入 `pypdf` 擷取文字（支援 `--pages` 與 `--resume`） |
| **`render`** | `RenderPlugin` | `storage`: `workspace` | `render-images/*.png`<br/>`render-index.json` | 延後載入 `pypdfium2` 與 `Pillow`，逐頁轉 150 DPI PNG 圖檔（支援斷點續跑） |
| **`prepare`** | `PreparePlugin` | `storage`: `workspace` | `work.json` | 驗證渲染完整度與 `topics.json`，對齊快取並切分分析批次 |
| **`packet`** | `PacketPlugin` | `storage`: `workspace` | `packet-NNN.json` | 從 `work.json` 抽出單批待判讀題目與跨頁前後文 |
| **`status`** | `StatusPlugin` | `storage`: `workspace` | 終端進度狀態 | 檢視 `work.json` 批次完成度、快取命中率與剩餘工作包 |
| **`merge`** | `MergePlugin` | `storage`: `workspace` | `question-index.json` | 合併各批 `result-*.json`、`visual-review.json` 與文字擷取資料 |
| **`summary`** | `SummaryPlugin` | `storage`: `workspace` | `summary.json` | 依目標考試分層統計唯一題數、主題命中數與命題事件 |
| **`report-html`** | `ReportHtmlPlugin` | `storage`: `workspace`<br/>`statistics`: `summary` | `report.html` | 讀取 `report.md` 與模板編譯互動式單頁 HTML 報告 |
| **`check`** | `ReportCheckPlugin` | `storage`: `workspace` | 終端健檢報告 | 檢驗 HTML DOM 結構、內嵌 JSON、題目卡片渲染完整性 |
| **`clean-images`** | `CleanImagesPlugin` | `storage`: `workspace` | 刪除 `render-images/` | 安全清理暫存 PNG 圖檔以釋放磁碟空間 |

---

## 三、 生命週期與全域依賴規則

1. **依賴注入（Dependency Injection）**：
   * 插件之間禁止互相 `import` 具體實作。
   * 跨插件共用能力一律透過 `Protocol` 與建構子注入（例如 `ReportHtmlPlugin` 需要統計補算能力時，由 `_bootstrap.py` 注入 `SummaryPlugin` 實例）。
2. **載入與卸載順序（Load & Reverse-Shutdown）**：
   * 載入順序：底層依賴（`workspace`、`manifest`）→ 上層業務插件 → `pipeline` → `cli`。
   * 卸載順序：完全依載入順序**反向卸載**（`cli` → `pipeline` → 業務插件 → 底層依賴），確保資源與 EventBus 訂閱安全釋放。

