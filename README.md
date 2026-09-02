# ⚡ QuietRadar

> **對抗演算法綁架 · 奪回資訊主控權**
> 極簡、無人職守、零演算法焦慮的個人情報雷達。

---

## 📖 核心哲學

1. **來源優先序反轉**：以精選的優質獨立站點、科技專欄、官方 RSS 為核心主菜；論壇熱榜僅為可選之低權重輔助。
2. **低頻批次交付**：杜絕即時快訊轟炸，一律採批次摘要（每日早/晚固定時段交付）。
3. **意圖降噪而非資訊加量**：透過 LLM 依照個人興趣（Profile）砍掉 80% 無關雜訊，推播嚴格鎖定在 5～10 則精選，不追求資訊全覆蓋。
4. **拒絕無限捲動**：推播純文字超連結榜單，點擊直達原文，看完即走，零心理負擔。

---

## 🛠️ 快速開始

### 1. 安裝依賴
```bash
pip install -r requirements.txt
```

### 2. 環境變數設定
複製 `.env.example` 為 `.env` 並填入您的金鑰：
```ini
# 資料庫 (預設本機 SQLite)
DATABASE_URL=sqlite:///./quietradar.db

# LLM 端點與金鑰 (支援 OpenAI 格式 / NVIDIA NIM)
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL=meta/llama-3.2-11b-vision-instruct

# Bark 推播設定
BARK_SERVER_URL=https://api.day.app
BARK_DEVICE_KEY=your_bark_key_here
```

### 3. 配置訂閱來源與關注領域
編輯 `sources.yaml`：
```yaml
profile:
  interests:
    - "AI Agent、LLM 架構、SaaS 技術實踐與系統設計"
    - "後端架構（Python、FastAPI、PostgreSQL、分散式系統、效能優化）"
  negative_topics:
    - "單純名人八卦、政治口水、加密貨幣炒幣空投、公關買榜新聞"
    - "農場標題文、入門教學"

sources:
  - name: "iThome 新聞"
    url: "https://www.ithome.com.tw/rss"
    category: "curated_rss"
    enabled: true
```

### 4. 執行與管理

- **執行情報雷達管線**：
  ```bash
  python pipeline.py
  ```
- **啟動視覺化控制台 GUI**：
  ```bash
  python app.py
  # 瀏覽器開啟: http://localhost:8765
  ```

---

## 📜 授權協議
本專案採用 [MIT License](LICENSE) 開源授權，100% 乾淨室獨立設計。
