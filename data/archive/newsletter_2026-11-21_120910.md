> 出刊時間：2026-11-21 12:09 ｜ 本期精選：5 則

---

# 生成已經不是瓶頸，計價點正移往驗證那一端

同一段時間裡，數學圈出現兩份指向黎曼假設的文本。一份出自 Levent Alpöge，他寫道自己與 Claude 合作找到了黎曼假設的證明，方法還能推到廣義黎曼假設，論證也已不帶假設地形式化[¹](https://simonwillison.net/2026/Nov/20/levent-alpoge/)。另一份是 Alessandro Masullo 的廣義黎曼假設草稿，Ravi Vakil 轉述 Brian Conrad 已看了數個月並給出大量回饋，目前看起來不錯，並註明這一切都是人類完成，沒有用到 AI[²](https://simonwillison.net/2026/Nov/20/ravi-vakil/)。兩份文本的可信度都不由作者決定，而取決於能不能被檢查。生成已不是稀缺資源，稀缺的是驗證，價值正沿這條線往後移。

### 一份宣稱與一份草稿，被送進同一道閘門

Alpöge 的文章寫得很有畫面。他說自己相信已找到黎曼假設的證明，方法可以輕易推得更遠，解決廣義黎曼假設，並預期給更多時間也能處理擴展黎曼假設；他說兩人已把論證不帶假設地形式化，又形容在抵達終線前 Claude 大幅超前，飛了起來、跳舞、閃閃發光[¹](https://simonwillison.net/2026/Nov/20/levent-alpoge/)。他也承認這對他像是失去一盞主要的導引燈，但過程中所見讓他相信那是橫跨科學的一道曙光[¹](https://simonwillison.net/2026/Nov/20/levent-alpoge/)。這些都是他的陳述，不是社群的結論。

對照組的姿態剛好相反。Vakil 為 Masullo 的草稿寫了一則他自己稱為倉促的公告，說明這份廣義黎曼假設的證明由 Conrad 仔細看了數個月、給出大量回饋，而回饋正在被納入修改，到目前為止看起來不錯；原本的路線是安排 Masullo 到史丹佛待一年潤飾寫作，等夠多人看過再對外發表，如今判斷先把現有草稿放出去更好[²](https://simonwillison.net/2026/Nov/20/ravi-vakil/)。他並在轉述中直接標了一句：這一切都是人類做的，沒有 AI[²](https://simonwillison.net/2026/Nov/20/ravi-vakil/)。

這兩件事放在一起，說明的是同一個機制。這類領域的瓶頸從來不是提出，而是確認。模型壓低的是提出與形式化的成本，沒有壓低審閱的成本，Conrad 那數個月不會因為工具變快而縮短。這意味著 AI 最先改變的是候選結果的產量，而不是判定期的長度；能被檢查的形式物件，因此比任何宣稱都更接近價值本身。

### slop 之後，正確性被寫進工具鏈

軟體圈的同一場拉扯已經有了名字。Latent Space 把當前狀態稱為對抗 slop 的戰爭，並指出正在浮現的武器是形式驗證：用數學證明軟體正確，過去需要專門人才，現在 AI 與 Lean 正在幫忙把這個過程自動化[³](https://latent.space/p/formal-verification/)。這句話的重點不在 Lean，而在自動化三個字，因為它針對的正是專門人才供不應求這道舊天花板。

生成端的門檻已經低到不需要討論。一位開發者記錄，AI 幫他做了一個炫酷的 3D 大屏，上線三天就卡崩[⁴](https://juejin.cn/post/7708639137272102966)。生成只花了對話的時間，崩潰發生在真實流量進來之後，兩者之間隔著的正是驗證。

**當生成便宜到一句指令，成本就從寫作移到擔保。** 產出量上升，缺陷供給同步上升，審查於是變成總成本裡最大的一項。把正確性寫成合併前必須通過的條件，等於把風險從上線之後挪到上線之前，這是工具鏈而非模型的生意。如果這個方向成立，能收錢的位置會落在願意為正確性保證的一方，而以行數、頁面數或交付速度計價的供給端會持續被壓縮。

### 驗證的另一半是環境，而環境正在被虛擬化

驗證有兩半。一半是程式本身是否正確，另一半是它在真實條件下是否正確，而後者長期卡在需要實體裝置與實測環境。社群專案 BSWorld 的做法是讓 AI 代理在沒有硬體的情況下測試藍牙程式碼[⁵](https://github.com/XiaoMi/bsworld)。它在 Hacker News 上得到的注意相當有限，分數與留言都只有個位數[⁵](https://github.com/XiaoMi/bsworld)，但方向值得記下來。

把條件模擬出來之後，驗證就不再受裝置數量限制，可以交給代理反覆執行。這意味著測試從一次性關卡變成可規模化的運算，而模擬層、可重現的環境與能重複觸發條件的基礎設施，會從成本項變成能力項。相對之下，把實測過當作護城河的供應商，護城河會被搬走一段。

### 產出接近免費之後

三件事的方向是一致的。數學社群用數月的審閱守住判準，軟體圈開始把證明寫進工具鏈，測試則試著把真實世界搬進機器裡。生成能力正在變成公用事業，差異化回到能不能被檢查這個老問題上。**當產出的價格趨近於零，剩下能收費的是願意為正確性承擔後果的那一層。**

---

## 今日其他值得一讀

- [林俊暘新公司估值升至 50 億美元；丁磊：短劇爆發衝擊遊戲產業；華為新品牌星芒釋出首款闊直板手機](http://www.geekpark.net/news/374299) · 極客公園
  DeepSeek 新一輪募資 1000 億至 1055 億元、投後估值約 6155 億元，梁文鋒持股仍約 67.42%，新增股東平臺橫跨上游算力與下游場景。
- [WorkBuddy 日活超過 1000 萬；曝輝達未投企業責任險，或因黃仁勳事件遭鉅額索賠；DeepSeek 再調整 API 計費 | 極客早知道](http://www.geekpark.net/news/374177) · 極客公園
  騰訊前三季毛利率 56.79%、經營現金流年減 33.76%；輝達為高管投保 9.5 億美元卻未投保企業責任險，面對兩起證券詐欺訴訟。
- [Cloudflare Joins the Jevolution and Labels Itself a Neolab](https://latent.space/p/cloudflare/) · Latent Space (AI產品落地)
  Cloudflare 從加速網路轉向自建 System One 模型，在毫秒級做決策與取資料，顯示基礎設施廠正往模型層延伸。

---

1. [Quoting Levent Alpöge](https://simonwillison.net/2026/Nov/20/levent-alpoge/) · Simon Willison (AI實戰筆記)

2. [Quoting Ravi Vakil](https://simonwillison.net/2026/Nov/20/ravi-vakil/) · Simon Willison (AI實戰筆記)

3. [The War Against Slop and an Emerging Weapon to Fight it: Formal Verification](https://latent.space/p/formal-verification/) · Latent Space (AI產品落地)

4. [AI幫我生成了一個炫酷的3D大屏，上線3天就卡崩了 AI幫我生成了一個炫酷的3D大屏，上線3天就卡崩了 <div data-v-012280ec= class=abstract](https://juejin.cn/post/7708639137272102966) · 掘金熱門

5. [Show HN: BSWorld – AI agents can now test Bluetooth code without hardware](https://github.com/XiaoMi/bsworld) · Hacker News (Show HN)

---