---
name: h3-colab-digital-human
description: 用一張人物照＋一句中文需求，自動在使用者已開好的 Colab（或本機）ComfyUI 上生成 MiniMax H3 會說話的數字人影片。當使用者說「做數字人」「讓照片裡的人說話」「H3 生成影片」「幫我用 Colab 生成」並提供 trycloudflare 網址或 ComfyUI 網址時使用。需要能執行本機 PowerShell（或 Python）的桌面版 AI。
---

# H3 數字人：一張照片＋一句話 → 會說話的影片

## 使用者要先做的事（AI 做不到，請提醒）
1. 從 <https://github.com/dseditor/minimax-h3-colab> 開啟 `H3_Basic_ComfyUI.ipynb`（或 `H3_Digital_Human.ipynb`），執行階段選 **L4**。
2. 依序執行 **①、②、③-b、④**，把 ③-b 印出來的 `https://….trycloudflare.com` 網址交給你。
   （③ 的 Colab 內建代理只有使用者本人的瀏覽器打得開，AI 用不了，一定要用 ③-b。）
3. 給你：**人物照的檔案路徑**、**一句中文需求**（說什麼、什麼語氣、幾秒；沒給秒數就用 10 秒）。

網址等於 GPU 鑰匙：只在這次對話使用，不要寫進任何公開的檔案。

## 你要做的事

### 1. 先測連線
```powershell
powershell -ExecutionPolicy Bypass -File "<skill>/scripts/h3_colab.ps1" -Url <網址> -Check
```
`[FAIL]` 就停下來，請使用者確認 Colab 的 ② 與 ③-b 還在執行（執行階段沒有中斷）、網址沒貼錯。
（沒有 PowerShell 的環境改用 `python <skill>/scripts/h3_colab.py --url <網址> --check`，參數相同。）

### 2. 看照片，寫提示詞
- **打開照片親眼看過再寫。** 這個模型參考力偏弱：提示詞描述的人物、服裝、場景只要跟照片對不上，模型就不看參考圖，直接照文字生成。
- 用 Ref2VA 格式，**完整規則在 MiniMax 官方指南 `ref-en.txt`，寫之前先讀**。照片就是 `<Picture 1>`：
  - 已安裝官方 `h3-prompt-writing` skill 的話，直接讀它的 `references/ref-en.txt`。
  - 沒有的話下載（固定版本）存到本 skill 的 `references/`：
    `https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/d21241f0a4b3acbb34c97dae47fa417b7065e438/.claude/skills/h3-prompt-writing/references/ref-en.txt`
  - `<Subject 1>` ＝照片裡的人（髮型、髮飾、服裝、配件照實寫），`<Subject 2>` ＝照片裡的場景。
  - 照片裡沒有的東西不要給 Subject 標籤，也盡量不要加道具：道具寫越多，模型越容易變出奇怪的東西。
- 數字人的固定寫法：
  - 單一鏡頭、中近景、構圖貼近照片，鏡頭幾乎不動（頂多很慢地推近）。
  - 台詞放進 `<d>[Chinese] …</d>`，**保留使用者給的中文原文**，說話者標 `(S1)`。
  - **台詞長度：每秒約 4～5 個中文字**（10 秒約 40～45 字）。太長句尾會糊，太短可以補一個自然的手勢或表情。
  - 寫明「她是畫面中唯一的人、唯一的聲音；畫面乾淨，沒有任何文字或字幕」。
  - 全文 300～500 個英文字。
- 寫好存成 UTF-8 的 `prompt.txt`，先把台詞那一句貼給使用者確認，對了再送出。

### 3. 生成並下載
```powershell
powershell -ExecutionPolicy Bypass -File "<skill>/scripts/h3_colab.ps1" -Url <網址> -Image <照片> -PromptFile prompt.txt -Seconds 10 -Out <輸出.mp4>
```
- 秒數 5～15，預設解析度 0.4MP，seed 預設隨機（`-Seed` 可固定）。
- L4 生成 10 秒大約 4 分鐘；腳本每 10 秒回報一次進度，跑完印出 `[DONE]` 與檔案位置。
- 失敗時腳本會印出 ComfyUI 的錯誤內容，照實轉告使用者，不要自行猜測原因。

### 4. 交付
告訴使用者影片路徑，並請他看三件事：臉和衣服像不像照片、嘴型對不對、有沒有多出字幕或第二個人。
不像照片時，先回頭對照片檢查提示詞的人物描述，不要急著換 seed 重抽。

## 檔案
- `assets/workflow_api.json`：ComfyUI API 格式工作流（PRISM 融合 int8 模型、6 步、全原生節點），腳本只會改提示詞、圖片、秒數、解析度、seed 五個值。
- `references/`：放 MiniMax 官方提示詞指南（不隨本 repo 散布，來源與版本見 `references/H3_NOTICE.md`）。
- `scripts/h3_colab.ps1`：Windows PowerShell 5.1／7 都能跑，上傳和下載用 Windows 內建的 `curl.exe`。
- `scripts/h3_colab.py`：同功能的 Python 版（只用標準函式庫）。
