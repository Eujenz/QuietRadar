# 重構 QuietRadar 摘要生成 Prompt 與專欄編輯風格優化計劃

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 QuietRadar 產出的科技商業週報/日報從「刻板刻薄、虛構數字、情緒化口號」徹底重構為「《天下雜誌》、《彭博商業周刊》、《日經商業》資深主編」的冷靜、沉穩、洞察深刻的高密度專欄筆觸，同時優化 Bark 行動端推播的排版呼吸感與資產系統對齊。

**Architecture:** 
1. 採用 Single Source of Truth 原則，將最新規格之【核心編輯部 System Prompt】建立於 `prompts/editorial.md`，並在 `pipeline.py` 中定義安全 fallback 常數 `EDITORIAL_SYSTEM_PROMPT`。
2. 更新 `sources.yaml` 中的讀者關注維度（Profile），徹底移除導致模型產生「Micro-TAM 試算」與「這週末驗證道具」幻覺八股的設定。
3. 在 `pipeline.py` 的生成階段與格式化階段強化負向約束（禁用驚嘆號、反問句、情緒化 Emoji 與自嗨詞）、落實全形標點洗滌，並確保 `overview` 與 `signals` 的 JSON Contract 與 Provenance Isolation 保持 100% 穩定。
4. 調整 Bark 推播排版結構，移除冗贅幾何符號，讓手機推播在 30 秒內具備極高的高管閱讀掃描感。

**Tech Stack:** Python 3.11+, pytest, httpx, YAML, Markdown, Bark REST API.

**Spec:** 本計劃基於對 `data/archive` 歷史產出報告的深度評估、其他 Agent 提供的【核心編輯部 System Prompt】規範，以及 QuietRadar v1.1 既有之 Signal Ledger / Provenance 安全合約進行整合演進。

## Global Constraints
- 嚴格維持與 `signal_ledger.py` 及安全來源代碼 `[S01]` (Provenance Isolation) 的 100% 相容性，嚴禁在 LLM 輸出中直接外洩未清洗 URL。
- LLM API 呼叫之 `temperature` 鎖定在 `0.2`（兼顧穩定結構與低發散度）。
- 嚴格禁用所有驚嘆號（`！`、`!`）、反問句、空洞總結與情緒性 Emoji（🚀, 🔥, 💡, 🎉, 😱）。
- 現有 `pytest tests/` 47 項單元與整合測試必須全部持續維持 PASS。

---

### Task 1: 建立核心編輯部 Prompt 模板與安全常數

**Files:**
- Create: `prompts/editorial.md`
- Modify: `pipeline.py:500-560`
- Test: `tests/test_production_reliability.py`

**Interfaces:**
- Consumes: 候選文章列表 `articles_payload` (含 `source_id`, `title`, `snippet`)
- Produces: 具備定錨、拆解、二階效應敘事之 `overview` 與結構化 `signals` 之 Prompt 指令

- [ ] **Step 1: 編寫 `prompts/editorial.md` 核心編輯部主主筆模板**
  導入《天下雜誌》、《彭博商業周刊》資深總編輯的角色定義、編輯部鐵律（定錨、拆解、二階效應）、事實與推論界線（Fact / Interpretation / Hypothesis），以及嚴格負向約束清單（禁用驚嘆號、禁止八股 TAM 試算、禁止無依據的周末驗證道具建議、禁止自嗨詞與情緒 Emoji）。

- [ ] **Step 2: 在 `pipeline.py` 建立 `EDITORIAL_SYSTEM_PROMPT` 作為常數與備援**
  若檔案系統缺失模板時，fallback 至此常數，杜絕執行階段檔案遺失之風險。

- [ ] **Step 3: 驗證 prompt 載入邏輯**
  執行 `pytest tests/test_production_reliability.py` 確保 `load_prompt_template` 能正確讀取。

- [ ] **Step 4: Commit**
  ```bash
  git add prompts/editorial.md pipeline.py
  git commit -m "feat(prompt): introduce editorial system prompt template"
  ```

---

### Task 2: 清洗與調校 `sources.yaml` 讀者 Profile 及設定

**Files:**
- Modify: `sources.yaml:1-36`
- Test: `tests/test_production_reliability.py`

**Interfaces:**
- Consumes: `sources.yaml`
- Produces: 現代化政經科技決策者畫像與乾淨之輸出模板標題

- [ ] **Step 1: 重構 `sources.yaml` 中的 `profile.interests` 與 `negative_topics`**
  將引導模型偏向「工讀生替代、週末做 Google 表單」等八股思維的詞彙替換為：
  - 核心關注：產業利潤池轉移（Profit Pool Shifts）、技術商業化摩擦力（Commercialization Friction）、第二層效應（Second-Order Effects）、合規與數據主權、基礎設施大宗商品化（Commoditization of Infrastructure）。
  - 排斥主題：公關買榜通稿、情緒化吹捧、缺乏事實支撐的極端推論、未經證實的八卦。
  - 將 `active_prompt_template` 指向 `editorial`。

- [ ] **Step 2: 調整 `output_template` 預設風格**
  移除標題中廉價的 Emoji（例如 `⚡`、`💡`、`🎯`），改為成熟冷冽的專欄排版（例如 `QuietRadar 商業科技晨訊`、`本期專欄焦點`）。

- [ ] **Step 3: 測試設定載入**
  執行 `pytest tests/test_production_reliability.py`。

- [ ] **Step 4: Commit**
  ```bash
  git add sources.yaml
  git commit -m "refactor(config): recalibrate reader profile to institutional editorial perspective"
  ```

---

### Task 3: 重構 `pipeline.py` 蒸餾生成階段與輸出契約保護

**Files:**
- Modify: `pipeline.py:500-600`
- Modify: `pipeline.py:1080-1210`
- Test: `tests/test_provenance.py`
- Test: `tests/test_signal_ledger.py`

**Interfaces:**
- Consumes: LLM 回應字串
- Produces: 驗證通過之 `{"overview": str, "signals": list}` 結構

- [ ] **Step 1: 優化 `distill_articles()` 的 System / User Prompt 注入機制**
  - 將 `temperature` 確保穩定為 `0.2`。
  - 徹底移除 Prompt 中誘發模型填寫「Micro-TAM 試算」與「這週末驗證道具」的死板指示。
  - 保留對 `signals` 欄位（title, summary, why_it_matters, asset_potential, novelty, source_ids）的明確型態要求，確保資料庫與 Ledger 相容性。

- [ ] **Step 2: 強化輸出洗滌過濾器（Post-processing Sanitization）**
  - 在 `sanitize_taiwan_terms()` 與後處理邏輯中增加對驚嘆號（`！`、`!`）的自動降級清洗（將驚嘆號平緩替換為句號 `。`，避免模型偶發漏網）。
  - 移除多餘的情緒化 Emoji。

- [ ] **Step 3: 執行 Provenance 與 Ledger 測試**
  執行 `pytest tests/test_provenance.py tests/test_signal_ledger.py` 確保資料契約無損。

- [ ] **Step 4: Commit**
  ```bash
  git add pipeline.py
  git commit -m "feat(pipeline): enforce editorial sanitization and remove formulaic constraints"
  ```

---

### Task 4: 優化 Bark 行動端推播排版與閱讀呼吸感

**Files:**
- Modify: `pipeline.py:1360-1490`
- Test: `tests/test_bark_delivery.py`

**Interfaces:**
- Consumes: `body_markdown`
- Produces: 在 2400-byte 安全範圍內、高密度、高可讀性的 Bark 分段 Payload

- [ ] **Step 1: 調整 Bark 推播標題與結構分隔**
  - 第一段標題：`QuietRadar 商業科技晨訊 ({page}/{total})`
  - 移除標題與內文中的冗贅幾何符號，使用全形標點（`｜`、`：`、`——`）維持典雅視覺。
  - 段落間嚴格確保雙換行（`\n\n`），避免 iOS 通知文字擠壓黏連。

- [ ] **Step 2: 運行 Bark 傳送與分段單元測試**
  執行 `pytest tests/test_bark_delivery.py` 確保位元組計算與倒序推送完全正常。

- [ ] **Step 3: Commit**
  ```bash
  git add pipeline.py
  git commit -m "refactor(bark): streamline mobile push layout with editorial typography"
  ```

---

### Task 5: 完整整合測試、歷史對比驗證與文件同步

**Files:**
- Test: `tests/` 全部測試
- Modify: `README.md`
- Update: `prompts/solopreneur.md`（同步或轉為別名）

- [ ] **Step 1: 執行全套 pytest 回歸測試**
  執行 `pytest tests/` 確保全數 47+ 測試通過。

- [ ] **Step 2: 透過模擬資料進行 dry-run 生成驗證**
  使用真實測試資料驗證生成的專欄文字，對比新舊風格（去除八股 TAM 與 Google 表單道具，呈現資深總編輯的三段論洞察）。

- [ ] **Step 3: 更新 README.md 中的風格規範與指令說明**

- [ ] **Step 4: Commit**
  ```bash
  git add README.md
  git commit -m "docs: document executive editorial style guidelines"
  ```
