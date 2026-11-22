> 出刊時間：2026-11-22 11:23 ｜ 本期精選：6 則

---

同一週內，模型、工具與財報各自出現一個訊號。Gemini 4 新增 xhigh 推理檔位，同一個模型把輸出從 5,311 個 token 拉到 31,477 個，單次成本從 3.99 美分升到 23.61 美分[¹](https://simonwillison.net/2026/Nov/20/llm-gemini/)；一份系統性回顧為 AI 代理的漏洞研究能力建立分級，並指出開放權重模型已追平受限前沿模型[²](https://arxiv.org/abs/2611.26601v1)；騰訊第三季資本開支 814 億元、同比增長 527%，新 AI 業務對 Non-IFRS 經營利潤造成約 152 億元淨影響[³](http://www.geekpark.net/news/374302)。三者指向同一個變化：**AI 的能力正在被切成可反覆定價的量，利潤則往掌握資料與算力的一端沉積。**

### 同一個模型，六倍價格的思考量

Simon Willison 在記錄 llm-gemini 0.38.1 的更新時補上一個先前漏掉的細節：Gemini 4 Carbon 只支援 low、medium、high 三個推理檔位，Hydrogen 與 Neon 還多一檔 xhigh [¹](https://simonwillison.net/2026/Nov/20/llm-gemini/)。他更新比較表後寫下數字，Hydrogen 在 high 用掉 5,311 個輸出 token、花費 3.99 美分，切到 xhigh 則用掉 31,477 個 token、23.61 美分，而產出的結果已經可以與更高階的 Neon 相比 [¹](https://simonwillison.net/2026/Nov/20/llm-gemini/)。

這看起來只是工具程式的修正，實際上說明了模型商品目前的賣法。同一組權重不再對應一個固定價格，而是對應一段可調量的思考，買方支付的差額買到的是推理深度，不是新能力。當最貴的檔位與自家更高階的型號產出相當，產品線內部就出現了自我侵蝕，模型之間的差異也從誰更聰明，退成你買了多少思考。

能力一旦可以被切分與比較，就會被外部拿來排序。一份題為 SoK 的系統性回顧以類似自動駕駛的分級方式，為語言模型代理在軟體漏洞研究上的自主性建立等級，並要求每一項宣稱都標註正確性、可用性與證據強度；其結論是，代理早在 2026 年的受限前沿模型之前，就已在真實軟體中無人介入地發現、驗證並修補漏洞，開放權重模型其後達到相當的自主性 [²](https://arxiv.org/abs/2611.26601v1)。這意味著模型層最難防守的那部分價值，正被公開的權重與公開的評比同時稀釋。

### 180TB 過程資料換成一個控制層

生成式影片的商業化卡在兩個現實上：生成一條滿意的影片往往要反覆抽卡，算力與時間成本都不低，而進入專業影視管線後，今天的模型能承擔的工業級工作仍然有限 [⁴](http://www.geekpark.net/news/374303)。達瓦科技的解法不是把模型做大，而是把控制與渲染分開。這家 2023 年成立於重慶永川、從科技片場轉向 AI 模型的公司，自研控制層基座模型 Latent Stage，把對人物、動作、鏡頭、燈光與場景的理解和控制獨立成一層，再交給渲染層出畫面；對外產品一是面向專業團隊做離線生成的 FilmOS，一是能在單張消費級顯示卡上即時執行的世界模型 Scenes AI，公司今年連續完成 Pre-A 與 A 輪，累計融資約 5 億人民幣 [⁴](http://www.geekpark.net/news/374303)。

支撐這條路線的是一項難以複製的存量。達瓦自 2014 年營運科技影視片場，累積約 180TB 結構化過程資料，記錄的不只是成片畫面，還包含從劇本、機位、燈光到動作捕捉的完整拍攝過程 [⁴](http://www.geekpark.net/news/374303)。通用模型可以生成無數段畫面，卻拿不到某個鏡頭為什麼這樣打光、這個動作如何被捕捉的過程紀錄。把控制層抽出來，等於把價值從畫面好不好看，移到指令能不能被重複下達、結果能不能被回溯。

同一條思路也出現在規模的另一端。Black Forest Labs 的 Andreas Blattmann 與 Dustin Podell 在受訪時的主張，是從畫素、運動與聲音去學習視覺智慧 [⁵](https://latent.space/p/bfl/)。兩者的參數規模與商業模式差距甚遠，卻都把生成能力的來源從模型大小，移向可驗證的結構化資料與可控的介面。如果這個判斷成立，產業鏈上能反覆收費的位置，會是靠近片場與工作流的那一層。

### 814 億資本開支換來的 152 億

11 月 18 日，騰訊公佈 2026 年第三季財報，營收 2073.4 億元、同比增長 8%，Non-IFRS 經營利潤 681 億元、同比下降 6% [³](http://www.geekpark.net/news/374302)。資本開支 814 億元、同比增長 527%，單季支出已超過 2025 年全年；研發投入 323.6 億元、同比增長 42%；在巨額算力採購與預付款之下，自由現金流為負 691 億元，若剔除算力採購預付款則仍有 169 億元 [³](http://www.geekpark.net/news/374302)。

把口徑分開看，這份財報的性質就清楚了。若剔除混元、元寶、CodeBuddy、WorkBuddy、小微等新 AI 產品的收入、成本與開支，本季 Non-IFRS 經營利潤為 833 億元、同比增長 8%，經營利潤率 40%，與去年同期持平 [³](http://www.geekpark.net/news/374302)。老業務的盈利能力沒有出問題，是新 AI 業務在單季對 Non-IFRS 經營利潤造成約 152 億元的淨影響，上一季是 105 億元，整體利潤增速因此被拉進負值區間 [³](http://www.geekpark.net/news/374302)。騰訊把越來越多的當期現金，換成 GPU、模型、Agent 與下一代入口 [³](http://www.geekpark.net/news/374302)。

這種交換能成立，靠的是既有業務仍在產出現金。第三季行銷服務收入 430 億元、同比增長 19%，是三大板塊中增速最快的一項，微信及 WeChat 合併月活 14.49 億 [³](http://www.geekpark.net/news/374302)。成本先發生、收入後到，中間的落差由誰吸收，取決於誰有存量現金流。而在全球尺度上，這筆帳的分佈並不平均：一篇跨國路徑的整理指出，歐洲掌握約 5% 的算力，沙烏地以買卡取得算力，日韓則把力氣放在場景 [⁶](https://juejin.cn/post/7708975511029760051)。承接算力需求與掌握場景的兩端各有收租位置，最難維持溢價的，是夾在中間、能力可被公開權重複製的模型層。

推理檔位、控制層與資本開支出現在三則互不相干的報導裡，卻描述同一個過程：AI 的產出正從一項能力，變成一種可計量、可切分、可被替代的投入品。定價權因此往兩端移動，一端決定單位成本的算力，一端決定單位價值的場景與資料。**當能力不再稀缺，能持續收費的位置就只剩這兩端。**

---

## 今日其他值得一讀

- [Muru](https://simonwillison.net/2026/Nov/21/muru/) · Simon Willison (AI實戰筆記)
  mod_wsgi 作者 Graham Dumpleton 以 AI 輔助打造 Rust 伺服器 Muru，三週內公開十餘篇實作記錄，可看資深維護者如何把代理放進生產級基礎設施。
- [Teaching Vision-Language-Action Models to Stop Safely](https://arxiv.org/abs/2611.26239) · Hugging Face (社群精選論文)
  VLA 模型研究指出，能從初始狀態抑制任務的策略在執行中途接手時仍可能完成任務，機器人安全驗證存在缺口。
- [Show HN: ClawVet – Offline AST scanner and rug-pull diff for AI agent skills](https://clawvet.vercel.app/) · Hacker News (Show HN)
  ClawVet 對 AI 代理技能做離線 AST 掃描與 rug-pull diff，反映代理生態開始需要供應鏈層的檢查工具。

---

1. [llm-gemini 0.38.1](https://simonwillison.net/2026/Nov/20/llm-gemini/) · Simon Willison (AI實戰筆記)

2. [✨ SoK: Assessing the Evolution of LLM Capability in Software Vulnerability Research](https://arxiv.org/abs/2611.26601v1) · arXiv (跨界前沿論文)

3. [資本開支猛增 226%，AI 吞噬百億利潤：騰訊的 AI 故事，進入更重的階段](http://www.geekpark.net/news/374302) · 極客公園

4. [重控制、輕渲染，達瓦科技的 AI 影片工業路線](http://www.geekpark.net/news/374303) · 極客公園

5. [Building Visual Intelligence — Andreas Blattmann & Dustin Podell, Black Forest Labs](https://latent.space/p/bfl/) · Latent Space (AI產品落地)

6. [中美AI競賽之外其他國家在幹什麼：算力5%的歐洲、買卡的沙特與做場景的日韓中美AI競賽之外其他國家在幹什麼：算力5%的歐洲、買卡的沙特與做場景的日韓 <div data-v-012280ec= class=abstract](https://juejin.cn/post/7708975511029760051) · 掘金熱門

---