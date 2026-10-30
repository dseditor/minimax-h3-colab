# H3 Toybox

一個接在 ComfyUI 後面的 MiniMax H3 網頁前端（H3 Studio 改名重做，2026-10-30）。

啟動：`start_h3toybox.bat`（需要 ComfyUI 已在 8188 執行），開 http://127.0.0.1:8190

## 三種模式

| 模式 | H3 任務 | 條件節點 | 改寫用的指南 |
|---|---|---|---|
| 🧸 參考生成 | Ref2VA | `MiniMaxH3ReferenceToVideo`（圖 9／影片 3／聲音 3） | `guides/..._ref_en.md` |
| 🎞 頭尾格 | I2VA／FL2VA／L2VA（看放了幾張） | `MiniMaxH3ImageToVideo` | `guides/..._base_en.md` |
| ✏️ 純文字 | T2VA | `MiniMaxH3ImageToVideo`（不接圖） | `guides/..._base_en.md` |

- 改寫模型是 Ref2VA 訓練的，base 任務它常回六段式；`normalize_base()` 會把它收回三段式，並重建第一行的對齊句（那一行是固定格式）。
- 指南來源：<https://huggingface.co/MiniMaxAI/MiniMax-H3/tree/main/docs>

## 🧵 延伸影片（參考模式）

素材盤裡的影片可以勾「延伸這支影片」，這時它不是參考影片，而是要被接下去的原片：

1. ffmpeg 把原片最後 3 秒轉成 24fps、生成尺寸（`h3t_tail_*.mp4`）
2. `MiniMaxH3MotionContext`（NikoDemon80/ComfyUI-H3-Motion-Context）把尾巴 22 格釘進新片段開頭（氣鎖），接在語意橋之後、採樣器之前
3. 原片最後一格同時當成一張參考圖，告訴改寫模型「從這一格接下去」
4. 解碼後 `MiniMaxH3MotionContextTrim` 剪掉那 22 格，再用 ffmpeg 把原片接上新片段 → `outputs/<job>_full.mp4`

SelfLift 採樣器會把 `minimax_keyframes` 縮到低解析度階段，所以頭尾格與氣鎖都能直接接兩段採樣。

## 工作流改動（相對 WF.json）

- 拿掉只為預覽存在的 `MiniMaxChunkFeedForward`(401) 與 `ModelPreviewOverrideKJ`(329)，SigmaShift(333) 直接接 scheduler／sampler
- `VHS_VideoCombine` 換成內建 `CreateVideo` ＋ `SaveVideo`（存到 `output/H3Toybox/`）
- 需要的外掛：T8 audio（Semantic Bridge）、SelfLift、H3 Motion Context

## 進度與剩餘時間

伺服器用標準庫自己連 ComfyUI 的 websocket（不需要 websocket-client），送件時帶同一個 client_id，就收得到那個 prompt 的 executing／progress 事件。

- 階段：排隊 → 準備 → 低解析度採樣 → 高解析度採樣 → 解碼存檔 →（延伸）接回原片
- 預估：採樣時用「已跑完的步」的中位數秒數外推；高解析度步還沒開始時，用歷史的高／低步數比換算
- 每次跑完把各階段時間（依像素×幀數正規化）以移動平均寫進 `stats.json`，越用越準

## 已知

- 延伸接點後約第 2 格有一格幾乎重複（幀差 1.5，正常約 19），播放時可能有 1/24 秒的頓點，尚未查明是哪一側。

## Colab

repo 根目錄的 `H3_Toybox.ipynb`：① 安裝 → ② 下載約 50 GB → ③ 啟動並用 Colab 內建代理開網頁。
Colab 版的 `config.json` 由 ③ 寫入（`free_after_rewrite: true`：改寫完就卸載 9B 模型，避免和 H3 模型同時佔記憶體）。
