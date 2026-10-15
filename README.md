# ⚡ QuietRadar

> **對抗演算法綁架 · 奪回資訊主控權**  
> 專為一人公司（Solopreneur）與獨立開發者打造的「通俗科普 × 商業財經」個人情報雷達。  
> 告別資訊焦慮、拒絕無效刷題、無人值守自動運轉。

---

## 📖 核心哲學

1. **來源優先序反轉**：以精選的優質獨立站點、科技專欄、前沿論文為核心主菜；論壇熱榜僅為可選之輔助。
2. **槓鈴式情報策略（Barbell Strategy）**：70% 專注核心商業技術落地的利基情報，30% 注入生物、農業、經濟等跨界前沿靈感（Serendipity），撞擊反直覺商機。
3. **老嫗能解的通俗科普化**：透過雙階段語言洗滌器，徹底洗去枯燥的學術統計術語與顧問黑話，轉譯為《商業周刊》、《連線 Wired》風格的生活化比喻特稿。
4. **低頻批次、看完即走**：拒絕演算法推播與無限捲動，每日固定時段交付純文字長文特稿與超連結榜單，零心理負擔。

---

## ✨ 關鍵核心特色

### 1. 雙階段語言洗滌系統 (Two-Stage Humanizer)
* **Stage 1 (跨國政經與科技專欄視角蒸餾)**：以具備 20 年資歷的跨國政經科技專欄總編輯視角（曾任職於《天下雜誌》、《彭博商業周刊》與《日經商業》），在喧囂資訊中冷靜定錨，剖析產業鏈利潤池位移與二階效應（Second-order effects），嚴格採用三段論（【定錨】、【拆解】、【折射】），嚴禁空泛讚嘆、情緒性形容詞與廉價八股。
* **Stage 2 (`SpeakHumanCleaner` 確定性洗滌與冷冽語氣)**：落實在地台灣繁體中文（OpenCC s2twp + 科技用語在地化），確定性洗去大陸黑話與浮誇公關虛詞，平緩降級驚嘆號（`！` ➔ `。`）並徹底濾除情緒化亢奮 Emoji（如 🚀, 🔥, 🎉），杜絕公式化 Micro-TAM 與假大空驗證道具。

### 2. 動態論文式文內注釋對齊引擎 (Dynamic Citation Engine)
* **經典上標角注**：正文論述中的關鍵引用自動轉為精緻角注（如 `[¹](url)` ~ `[⁹](url)`），手機端點擊直接跳轉查證原文。
* **100% 嚴格順序對齊**：正文出現的注釋序號與文末精選來源清單嚴格一對一對應，杜絕幻覺與跳號。

### 3. 多元精選來源與前置標題漏斗
* **核心商業與前沿科技**：Latent Space（AI產品落地）、Hacker News (Show HN)、Simon Willison AI 筆記、極客公園、數位時代、商業周刊、經理人月刊、人人都是產品經理等。
* **跨界奇想靈感庫**：arXiv 跨界前沿論文 (`cs.AI`, `econ.TH`, `q-bio.NC`)、Hugging Face 社群精選論文（Daily Papers）、農業科技前瞻等。
* **標題前置過濾漏斗**：命中排斥詞（農場標題、炒幣空投、求職八股等）即刻攔截，省下 90% 以上 LLM Token 與傳輸開銷。

### 4. 極致手機閱讀體驗（多裝置 Bark 倒序推送）
* **多裝置支援**：支援單一或多組 Bark Device Key 同步推播。
* **智慧安全分頁**：依據 UTF-8 位元組（≤ 2,400 bytes）自動切分長文，徹底避免 Apple APNs 4KB 與 Bark Nginx 413 限制。
* **倒序排列機制**：多頁推播時採倒序發送（例如 5 → 4 → 3 → 2 → 1），確保手機 iOS 通知中心依序自頂向下連貫閱讀。

### 5. 現代化視覺控制台 (Web GUI)
* 內建基於輕量高效的原生 Web GUI（`python app.py`），提供完整設定面板、串流日誌檢視、訂閱來源編輯、Prompt 模板熱切換與一鍵推播測試。

---

## 🛠️ 快速開始

### 1. 安裝環境與依賴
本專案基於 Python 3.10+，請先複製專案並安裝必要套件：
```bash
git clone https://github.com/Eujenz/QuietRadar.git
cd QuietRadar
pip install -r requirements.txt
```

### 2. 環境變數設定 (`.env`)
建立或編輯專案根目錄下的 `.env` 檔案：
```ini
# 資料庫連線（預設使用本機 SQLite）
DATABASE_URL=sqlite:///./quietradar.db

# LLM 端點與模型設定（支援 OpenRouter、NVIDIA NIM、OpenAI 相容端點）
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=minimax/minimax-m3:free
LLM_API_KEY=your_openrouter_or_openai_api_key

# Bark 推播設定（選填，支援以逗號分隔多組 Device Key）
BARK_SERVER_URL=https://api.day.app
BARK_DEVICE_KEY=your_device_key_1, your_device_key_2
```

### 3. 配置訂閱來源與設定 (`sources.yaml`)
您可以在 `sources.yaml` 中自訂關注主題、排斥詞、管線參數與情報來源：
```yaml
pipeline_settings:
  title_filter_enabled: true
  enable_two_stage_humanizer: true
  active_prompt_template: editorial     # 對應 prompts/editorial.md（或可選歷史模板 solopreneur.md）
  max_candidate_pool: 50
  serendipity_ratio: 0.28               # 跨界靈感抽取比例 (28%)
  max_output_items: 15

profile:
  interests:
    - 產業鏈利潤池位移與護城河消長、定價權轉移與商業化阻力
    - 基礎模型大宗商品化後，邊緣運算與自適應中介層架構演進
    - 垂直領域跨界機制創新、二階效應與長期價值捕獲
  negative_topics:
    - 實體庫存、代購代銷、接案外包、炒幣空投、純新手語法教學、企業公關買榜

sources:
  - name: Latent Space (AI產品落地)
    url: https://www.latent.space/feed
    category: curated_rss
    enabled: true
```

---

## 🚀 執行模式

### 1. 執行正式情報雷達 (CLI)
抓取來源、寫入去重指紋庫、生成 `latest_newsletter.md` 並推送至手機：
```bash
python pipeline.py
```

### 2. 測試模式 (免污染資料庫)
忽略重複紀錄並跳過歷史防重寫入，方便快速測試 Prompt 效果與版型：
```bash
python pipeline.py --test --force
```

### 3. 啟動 Web 視覺化管理後台
```bash
python app.py
```
啟動後打開瀏覽器訪問 `http://localhost:8765`，即可即時查看系統狀態、即時日誌、編輯設定與一鍵測試 Bark 推播。

---

## 📁 專案架構

```text
QuietRadar/
├── .github/workflows/
│   └── radar.yml          # GitHub Actions 定時自動化排程 (Asia/Taipei 時區)
├── data/
│   ├── archive/           # 歷史電子報與蒸餾結構化 JSON 自動存檔
│   └── signals/           # 30 天歷史訊號記帳庫 (Signal Ledger)
├── prompts/
│   ├── editorial.md       # 核心專欄 Prompt 模板（天下雜誌、彭博商業周刊資深總編輯風格）
│   └── solopreneur.md     # 歷史相容 Prompt 模板
├── app.py                 # 現代化視覺控制台伺服器 (原生標準函式庫高效實作)
├── pipeline.py            # 核心管線：抓取、漏斗、LLM 蒸餾、雙階段洗滌、注釋對齊與 Bark 推播
├── signal_ledger.py       # 歷史訊號記帳引擎與語義去重
├── sources.yaml           # 訂閱來源清單、使用者畫像與排版模板
├── latest_newsletter.md   # 最新出刊之 Markdown 電子報
├── latest_distilled.json  # 最新蒸餾結構化資料快照
├── requirements.txt       # Python 依賴套件清單
└── README.md              # 專案說明文件
```

---

## ⏰ 自動化與 GitHub Actions

本專案原生支援 GitHub Actions 無人值守排程，工作流程已針對台灣時區（`Asia/Taipei`，UTC+8）調校，於每日離峰時間自動出刊、更新 Repo 存檔並推送至您的手機。

若要在 GitHub 執行，請於 Repository 的 **Settings > Secrets and variables > Actions** 中新增以下 Repository Secrets：
- `LLM_API_KEY`：您的 LLM API 金鑰（如 OpenRouter）。
- `LLM_BASE_URL`：API 端點 URL。
- `LLM_MODEL`：指定使用的模型名稱。
- `BARK_DEVICE_KEY`：您的 Bark 裝置 Key（支援多組以逗號分隔）。
- `BARK_SERVER_URL`：Bark 伺服器網址（預設 `https://api.day.app`）。

---

## 📜 授權協議

本專案採用 [MIT License](LICENSE) 開源授權。
