> 出刊時間：2026-11-26 13:17 ｜ 本期精選：6 則

---

# 證明產出的速度快過閱讀速度之後，價值往複核層移動

11 月 24 日，OpenAI 研究人員發布預印本，宣稱確立方陣乘法複雜度指數為四分之九，並在所有數域上給出匹配的上界與下界[¹](https://www.pingwest.com/w/319911)。Latent Space 隔天以證明抵達速度快過閱讀速度為題，從 Erdős 問題寫到矩陣乘法與傅立葉變換的新界限[²](https://latent.space/p/ainews-math-proofs-are-landing-faster-than-we-can-read-them/)。同日，瑞士楚格一個團隊公開了為 AI 撰寫重新設計的程式語言與編譯器[³](https://lightscale.ai/)。三件事分屬不同領域，卻指向同一個位移：生成已不是限制，能不能被複核與擔保才是。產業價值正從生成端移往驗證端。

### 一個指數被寫死之後，複核工作才開始

這份名為《矩陣乘法指數為四分之九》的預印本，把方陣乘法計算複雜度的精確指數定在 2.25，並聲稱給出在所有數域上彼此匹配的上界與下界，若成立，等於收掉理論電腦科學延續數十年的核心問題[¹](https://www.pingwest.com/w/319911)。宣稱的形式比數字更值得注意。上界與下界同時到位，一個結果才可能被當成定論而不是候選，這意味著這類工作的價值已經有一部分落在「可被他人重跑與逐行確認」上。

Scott Aaronson 的評論由 Simon Willison 引述。他說我們會得到的不是這題或那題的一次性解答，而是一整片新的數學真理大陸，過去只能隔著霧隱約看見；他把這一刻比作 van Leeuwenhoek 第一次把顯微鏡對準池水、Galileo 第一次把望遠鏡對準星空，我們對複雜度理論宇宙與更遠的數學真理宇宙的解析度就在此時提高[⁴](https://simonwillison.net/2026/Nov/25/scott-aaronson/)。解析度提高的直接後果是待讀的東西變多。

Latent Space 在 11 月 25 日的整理裡，把 Erdős 問題、矩陣乘法與傅立葉變換的新界限放進同一份清單，並用抵達速度快過閱讀速度概括現狀[²](https://latent.space/p/ainews-math-proofs-are-landing-faster-than-we-can-read-them/)。瓶頸的位置因此換了。當宣稱的產出速度超過社群消化與複核的速度，稀缺項就從有沒有人能算出來，變成有沒有人能確認它對；如果確認必須靠可重跑的證明、可重現的建構與能交給社群複檢的模組，那麼提供這套能力的工具，會比再一個更強的生成器更難被取代。

### 審閱者不夠用，堆疊開始為機器重寫

Lightscale AI 的共同創辦人 Luc 有 ETH Zurich 的程式語言背景，團隊規模很小，駐在瑞士楚格。他們的起點是一個實際觀察：越來越多程式碼由 LLM 寫出，而少數人還有時間審閱生成物[³](https://lightscale.ai/)。這不是模型能力的問題，是審閱產能的問題。

他們的解法是把 LLM 底下的整層堆疊換掉。程式語言、編譯器與執行期系統全部從零設計，設計物件是 AI 的作者身分，而不是人類的可讀性；某些錯誤類別由抽象直接移除，因為 AI 永遠不會寫那段程式碼，改由編譯器與執行期完整實作，其餘則靠嚴格的編譯器檢查與嚴格語言規則壓低，代價是人類寫起來不好玩；速度來自砍掉古典語言的複雜度與歷史包袱，以減少 token 消耗[³](https://lightscale.ai/)。

OpenAI 的 Thariq Shihipar 在 AI Engineer CODE 上把同一件事講成兩種未來可以並存：AI 讓軟體便宜到可以一次量產上千份，同時仍留給人時間把其中一件做到剛好[⁵](https://latent.space/p/the_revolution_vs_the_renaissance_thariq_shihipar_openai/)。**如果生成接近免費而複核仍然昂貴，工具供應商的競爭點就會從幫你寫得更快，移到幫你擔保它不會出事。** 擔保這種東西無法由更長的提示詞提供，只能由編譯器、執行期與可重跑的檢查保證，這也解釋了為什麼重新設計語言會在此刻被提出來。

### 不確定被寫進預算表

通訊領域的 AI 原生研究提供了另一個角度。端到端的物理層學習以 jointly trained 的編碼器與解碼器對，取代調變、編碼與偵測這些分開的工序；論文量化了訓練與測試通道模型不匹配的敏感度，只在 AWGN 通道上訓練的系統在匹配條件下位元錯誤率低，換到 Rayleigh 衰減並帶有實際前端損害時則嚴重退化[⁶](https://arxiv.org/abs/2611.34942v1)。

他們的回應不是重練一個更大的模型，而是從 AWGN 預訓練系統出發，只微調發射端與接收端的一小部分層、凍結其餘，並把選哪些層寫成預算受限的最佳化問題，以降低適配新通道所需的時間與能量[⁶](https://arxiv.org/abs/2611.34942v1)。這條路線與語言設計那條在結構上相似：能力已經現成，成本落在適配與確認，而適配被當成有價格、可最佳化的工程專案。

兩件事放在一起，模型能力的商品化並沒有讓工程投入消失，只是把它從訓練端搬到複核端。受益的是能提供可重跑憑證、嚴格檢查與明確擔保的供應商；承壓的是仍以生成量為主要賣點，卻回答不出誰來確認的一方。

從池水到星空的解析度提升，說的是同一件事：看得更多之後，能不能讀完、能不能確認，成了新的稀缺。當生成不再是門檻，價值會往願意為正確性保證的那一層沉積，而這一層目前握在把編譯器、執行期與複核流程做成產品的人手上。

---

## 今日其他值得一讀

- [紅果上線“時間鎖”：一個4億使用者平臺開始主動降速](https://www.woshipm.com/share/6500787.html) · 人人都是產品經理
  紅果短劇 MAU 4.3 億、單日使用 151 分鐘卻上線時間鎖，廣告千次曝光成交價已從 30 元跌至不足 20 元。
- [黃仁勳復職後首次主持全員大會：向員工道歉；iPhone Duo 零部件成本創歷代 iPhone 新高；Anthropic 跌破發行價｜極客早知道](http://www.geekpark.net/news/374500) · 極客公園
  iPhone Duo 拆解顯示 2644 個零部件、成本率 64% 創歷代新高，鉸鏈成本 105 美元對三星 45 美元。
- [Plaud、影石、小紅書、Looki......我們發起了一場 AI 硬體圈的年度聚會](http://www.geekpark.net/news/374487) · 極客公園
  AI 硬體創業者告別超級入口敘事，轉進健康穿戴、錄音卡與墨水屏，並把模型視為變化極快的新供應鏈。

---

1. [OpenAI研究團隊宣稱確立方陣乘法精確計算複雜度](https://www.pingwest.com/w/319911) · 品玩

2. [[AINews] Math Proofs Are Landing Faster Than We Can Read Them](https://latent.space/p/ainews-math-proofs-are-landing-faster-than-we-can-read-them/) · Latent Space (AI產品落地)

3. [Show HN: Lightscale AI – A language for LLMs to write business software](https://lightscale.ai/) · Hacker News (Show HN)

4. [Quoting Scott Aaronson](https://simonwillison.net/2026/Nov/25/scott-aaronson/) · Simon Willison (AI實戰筆記)

5. [The Revolution vs The Renaissance — Thariq Shihipar, OpenAI](https://latent.space/p/the_revolution_vs_the_renaissance_thariq_shihipar_openai/) · Latent Space (AI產品落地)

6. [✨ Spectrum Adaptation of AI-Native Autoencoder Communications with Transfer Learning](https://arxiv.org/abs/2611.34942v1) · arXiv (跨界前沿論文)

---