# -*- coding: utf-8 -*-
"""產生兩本 Colab 筆記本（在 repo 根目錄執行：python tools/build_notebooks.py）
  H3_Basic_ComfyUI.ipynb   基礎：ComfyUI＋H3 模型組，不帶工作流
  H3_Digital_Human.ipynb   數字人：基礎＋工作流＋上傳照片／貼提示詞／生成
筆記本是產物，要改請改這支再重新產生。"""
import json, io, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
api = json.load(io.open('workflows/H3_digital_human_api.json', encoding='utf-8'))
fix_src = io.open('tools/userdata_slash_fix.py', encoding='utf-8').read()
ui_wf = io.open('workflows/H3_digital_human.json', encoding='utf-8').read()
sample_prompt = api['7']['inputs']['prompt']
api['7']['inputs']['prompt'] = ''


def md(s):
    return {'cell_type': 'markdown', 'metadata': {}, 'source': s}


def code(s):
    return {'cell_type': 'code', 'metadata': {'cellView': 'form'}, 'execution_count': None, 'outputs': [], 'source': s}


TQ = '"' * 3
cells = [
    md("""# MiniMax H3 數字人｜Colab 版（一張照片＋一段提示詞 → 會說話的影片）

由上往下依序執行。**執行階段請選 L4 GPU**（A100 也可以但很耗點數；T4 跑不動）。

| 步驟 | 做什麼 | 時間（主人 L4 實測） |
|---|---|---|
| ① 檢查＋安裝 | GPU／驅動不符就停下，不安裝；通過才裝 ComfyUI＋節點 | 約 1 分鐘 |
| ② 啟動 | 背景啟動 ComfyUI，工作流放進清單 | 約 1 分鐘 |
| ③ 開介面 | Colab 內建代理（推薦）；③-b 是 cloudflare 備援 | 數秒 |
| ④ 下載模型 | 約 37 GB（ComfyUI 確定正常才下載） | 約 1 分鐘 |
| ⑤ 上傳照片 | 人物照一張 | — |
| ⑥ 貼提示詞 | 用 H3 提示詞 skill 產生後整段貼上 | — |
| ⑦ 生成 | 10 秒影片 | 約 4 分鐘 |

只想用介面操作節點：跑 ①②③④ 就好。只想用表單生成影片：③ 可以跳過。

**Hugging Face 權杖（建議）**：左側欄「🔑 密鑰」新增 `HF_TOKEN`（請用**唯讀**權杖），並開啟「筆記本存取權」。

**交給桌面版 AI 生成**：跑 ①②③-b④，把 ③-b 的網址交給裝了 `h3-colab-digital-human` skill 的 AI（skill 在 repo 的 `skills/`）。

模型：`Minimax-h3_Singularity_x_10Eros-Max_PRISM`（int8，加速已融合，6 步）＋官方 Qwen3-VL 文字編碼器與 VAE。授權依照各來源模型。

最新版：<https://github.com/dseditor/minimax-h3-colab>"""),
    code("#@title ① 檢查環境＋安裝 ComfyUI（GPU 或驅動不符就停下，不會安裝）\nFIX_SRC = " + repr(fix_src) + """
import torch, shutil, subprocess, sys, os
from pathlib import Path
if not torch.cuda.is_available():
    raise RuntimeError('沒有 GPU：請到「執行階段 → 變更執行階段類型」選 L4 GPU，再重跑這格')
cap = torch.cuda.get_device_capability(0)
drv = subprocess.run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'], capture_output=True, text=True).stdout.strip()
print('GPU:', torch.cuda.get_device_name(0), '| capability', cap, '| VRAM', round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1), 'GiB')
print('驅動:', drv, '| 目前 PyTorch CUDA:', torch.version.cuda, '| 可用磁碟', round(shutil.disk_usage('/content').free / 2**30, 1), 'GiB')
if cap < (8, 0):
    raise RuntimeError(f'這張卡（capability {cap}）跑不動 int8 模型，沒有安裝任何東西。請換 L4（或 A100）再重跑。')
if int(drv.split('.')[0]) < 580:
    raise RuntimeError(f'驅動 {drv} 不支援 CUDA 13（需要 580 以上），沒有安裝任何東西。請換一台執行階段（L4）再試。')
print('✅ 環境檢查通過，開始安裝')
# HF 權杖先放進環境變數：之後啟動的 ComfyUI（含 ListHelper 的模型下載節點）都會沿用
try:
    from google.colab import userdata
    os.environ['HF_TOKEN'] = userdata.get('HF_TOKEN'); print('已讀到 HF_TOKEN')
except Exception:
    print('沒有設定 HF_TOKEN，之後改用匿名下載（人多時可能被限速）')
COMFY = Path('/content/ComfyUI')
if not COMFY.exists():
    subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/Comfy-Org/ComfyUI.git', str(COMFY)], check=True)
if not (torch.version.cuda or '').startswith('13'):
    print('安裝 CUDA 13 版 PyTorch（int8 ConvRot 權重需要）…')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'torch', 'torchvision', 'torchaudio',
                    '--index-url', 'https://download.pytorch.org/whl/cu130'], check=True)
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(COMFY / 'requirements.txt'), 'huggingface_hub', 'hf_xet'], check=True)
# 預裝 ListHelper 節點組（含 ModelDownloader：在介面裡從 Hugging Face 抓模型）
LH = COMFY / 'custom_nodes' / 'ComfyUI-ListHelper'
if not LH.exists():
    subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/dseditor/ComfyUI-ListHelper.git', str(LH)], check=True)
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(LH / 'requirements.txt')], check=True)
# Colab 內建代理（③）會把網址的 %2F 解開，工作流清單點開會 404 → 裝一個補路由的小節點
FIX = COMFY / 'custom_nodes' / 'colab_userdata_slash_fix'
FIX.mkdir(parents=True, exist_ok=True)
(FIX / '__init__.py').write_text(FIX_SRC, encoding='utf-8')
# 節點管理器（ComfyUI-Manager 已併入官方，用 pip 套件＋啟動旗標 --enable-manager）
if (COMFY / 'manager_requirements.txt').exists():
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(COMFY / 'manager_requirements.txt')], check=True)
out = subprocess.run([sys.executable, '-c', 'import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available())'],
                     capture_output=True, text=True)
print('ComfyUI 會用的 PyTorch：', out.stdout.strip(), out.stderr[-300:])
"""),
    code("#@title ② 啟動 ComfyUI（背景執行）＋把 H3 數字人工作流放進清單\nUI_WORKFLOW = " + repr(ui_wf) + """
import subprocess, time, urllib.request, sys
# --enable-cors-header：經 cloudflare 通道開介面時，瀏覽器的 Origin（trycloudflare 網址）
# 跟轉進來的 Host（127.0.0.1）對不上，ComfyUI 預設會回 403「存取遭拒」；開這個旗標才放行。
if 'server' in globals() and server.poll() is None:
    server.terminate(); server.wait(); print('已關閉舊的 ComfyUI，重新啟動')
# --enable-compress-response-body：介面要抓一大包節點清單和前端檔案，壓縮後走通道快很多。
# 節點管理器安全等級：normal- ＝可從清單裝節點，也可用 Git 網址安裝（本機模式下）。
# 這台 Colab 用完即丟；但 HF_TOKEN 在環境變數裡，自訂節點讀得到 → HF 權杖請用「唯讀」的。
cfg = COMFY / 'user' / '__manager' / 'config.ini'
cfg.parent.mkdir(parents=True, exist_ok=True)
if not cfg.exists():
    cfg.write_text('[default]\\nsecurity_level = normal-\\n', encoding='utf-8')
# 工作流放進清單（③、③-b 都看得到）；檔名用英文，避免網址編碼在代理上出問題
wf_dir = COMFY / 'user' / 'default' / 'workflows'
wf_dir.mkdir(parents=True, exist_ok=True)
(wf_dir / 'H3_digital_human.json').write_text(UI_WORKFLOW, encoding='utf-8')
old_cn = wf_dir / 'H3數字人.json'
if old_cn.exists(): old_cn.unlink()
Path('/content/H3_digital_human.json').write_text(UI_WORKFLOW, encoding='utf-8')
log = open('/content/comfyui.log', 'w')
server = subprocess.Popen([sys.executable, 'main.py', '--listen', '127.0.0.1', '--port', '8188', '--enable-cors-header', '--enable-manager', '--enable-compress-response-body'],
                          cwd=str(COMFY), stdout=log, stderr=subprocess.STDOUT)
for i in range(180):
    try:
        urllib.request.urlopen('http://127.0.0.1:8188/system_stats', timeout=2)
        print('ComfyUI 已啟動'); break
    except Exception:
        time.sleep(2)
else:
    print(open('/content/comfyui.log').read()[-3000:])
    raise RuntimeError('ComfyUI 沒有啟動成功，看上面的紀錄')
"""),
    code("""#@title ③ 開啟 ComfyUI 介面（推薦：Colab 內建代理，快、穩、網址不外流）
# 執行後下面會出現一個連結；顯示成 localhost 也沒關係，直接點它就會開新分頁。
# ⚠️ 開分頁的瀏覽器必須登入「跟這本 Colab 同一個 Google 帳號」；帳號不同就改用下一格 ③-b。
from google.colab import output
output.serve_kernel_port_as_window(8188)
# 如果左側工作流清單打不開（404），按下面下載 JSON，直接拖進 ComfyUI 畫面就好
from google.colab import files
files.download('/content/H3_digital_human.json')
"""),
    code("""#@title ③-b（備援）用 cloudflare 網址開介面：瀏覽器登入的 Google 帳號和 Colab 不同時用這格
# 會產生一個 trycloudflare 網址，點開就是 ComfyUI；也是交給桌面版 AI 操作時要給的網址。
# ⚠️ 網址沒有密碼，拿到的人都能用你的 GPU —— 不要貼到公開的地方。
import subprocess, re, time, json, urllib.request
if not Path('/content/cloudflared').exists():
    subprocess.run(['wget', '-q', '-O', '/content/cloudflared',
                    'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64'], check=True)
    subprocess.run(['chmod', '+x', '/content/cloudflared'], check=True)
# 通道的輸出寫進檔案，不要接 PIPE：PIPE 沒人讀會塞滿，通道就卡住，網頁停在載入畫面
if 'tun' in globals() and tun.poll() is None:
    tun.terminate(); tun.wait()
open('/content/cloudflared.log', 'w').close()
tun = subprocess.Popen(['/content/cloudflared', 'tunnel', '--no-autoupdate', '--url', 'http://127.0.0.1:8188'],
                       stdout=open('/content/cloudflared.log', 'a'), stderr=subprocess.STDOUT)
url = None
for i in range(60):
    time.sleep(1)
    m = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', open('/content/cloudflared.log').read())
    if m:
        url = m.group(0); break
print('👉 ComfyUI 介面：', url or '（沒拿到網址，看 /content/cloudflared.log）')
"""),
    code("""#@title ④ 下載模型（約 37 GB）
# 預設直接從 Hugging Face 下載（最快）。HF 權杖已在 ① 讀入。
# 只有在 Hugging Face 撞到流量上限時，才勾選 USE_DRIVE_CACHE：
#   勾選後會從「我的雲端硬碟/H3_models」複製（比 HF 慢），雲端硬碟沒有的檔案才去 HF 抓並順便存一份。
USE_DRIVE_CACHE = False  #@param {type:"boolean"}
from huggingface_hub import hf_hub_download
import shutil, os
CACHE = None
if USE_DRIVE_CACHE:
    from google.colab import drive
    drive.mount('/content/drive')
    CACHE = Path('/content/drive/MyDrive/H3_models'); CACHE.mkdir(parents=True, exist_ok=True)
files = [
    ('OliviaRossi/Minimax-h3_Singularity-x-10Eros-Max_PRISM', 'Minimax-h3_Singularity_x_10Eros-Max_PRISM_int8.safetensors', 'diffusion_models'),
    ('Comfy-Org/MiniMax-H3', 'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors', 'text_encoders'),
    ('Comfy-Org/MiniMax-H3', 'vae/minimax_h3_video_vae_int8_convrot.safetensors', 'vae'),
    ('Comfy-Org/MiniMax-H3', 'vae/minimax_h3_audio_vae_fp32.safetensors', 'vae'),
]
for repo, name, folder in files:
    target = COMFY / 'models' / folder / Path(name).name
    target.parent.mkdir(parents=True, exist_ok=True)
    cached = CACHE / Path(name).name if CACHE else None
    if not target.is_file():
        if cached and cached.is_file():
            print('從雲端硬碟複製', cached.name); shutil.copy(str(cached), str(target))
        else:
            got = Path(hf_hub_download(repo_id=repo, filename=name, local_dir='/content/hf'))
            shutil.move(str(got), str(target))
    if cached and not cached.is_file():
        print('存一份到雲端硬碟', cached.name); shutil.copy(str(target), str(cached))
    print(folder, target.name, f'{target.stat().st_size / 2**30:.1f} GiB')
"""),
    code("""#@title ⑤ 上傳人物照（一張）
from google.colab import files
from IPython.display import Image as _Img, display
up = files.upload()
IMAGE_NAME = list(up)[0]
(COMFY / 'input').mkdir(exist_ok=True)
(COMFY / 'input' / IMAGE_NAME).write_bytes(up[IMAGE_NAME])
display(_Img(data=up[IMAGE_NAME], width=320))
"""),
    code("#@title ⑥ 貼上提示詞＋設定（執行後下方會出現輸入框）\n"
         "# 執行這格後，下面會出現一個大的輸入框：把 skill 產生的整段提示詞直接貼進去就好，\n"
         "# 不用管引號、換行。貼完不用再執行這格，直接執行 ⑦。\n"
         "import ipywidgets as W\n"
         "from IPython.display import display\n"
         "SAMPLE_PROMPT = " + repr(sample_prompt) + "\n"
         """_keep = prompt_box.value if 'prompt_box' in globals() else SAMPLE_PROMPT   # 重跑這格不會洗掉已貼的內容
prompt_box = W.Textarea(value=_keep, placeholder='把整段提示詞貼在這裡',
                        layout=W.Layout(width='100%', height='320px'))
dur_box = W.IntSlider(value=10, min=5, max=15, step=1, description='秒數')
mp_box = W.FloatText(value=0.4, description='百萬像素')
seed_box = W.IntText(value=-1, description='seed（-1 隨機）')
clear_btn = W.Button(description='清空輸入框')
clear_btn.on_click(lambda _: setattr(prompt_box, 'value', ''))
count = W.HTML()
def _count(change=None):
    n = len(prompt_box.value.split())
    count.value = f'<b>{n}</b> 個英文字（官方建議 350～500）'
prompt_box.observe(_count, names='value'); _count()
display(W.VBox([W.HBox([clear_btn, count]), prompt_box, dur_box, mp_box, seed_box]))
"""),
    code("#@title ⑦ 生成影片\n"
         "import json, random, time, urllib.request\n"
         "from IPython.display import Video, display\n"
         "WORKFLOW = json.loads(" + repr(json.dumps(api, ensure_ascii=False)) + ")\n"
         """p = json.loads(json.dumps(WORKFLOW))
PROMPT = prompt_box.value.strip()
assert PROMPT, '⑥ 的輸入框是空的，請先貼上提示詞'
print('這次送出的提示詞開頭：', PROMPT[:160].replace(chr(10), ' '), '…（共', len(PROMPT.split()), '字）')
DURATION_SECONDS, MEGAPIXELS, SEED = dur_box.value, mp_box.value, seed_box.value
p['7']['inputs']['prompt'] = PROMPT
p['5']['inputs']['image'] = IMAGE_NAME
p['56']['inputs']['value'] = int(DURATION_SECONDS)
p['61']['inputs']['megapixels'] = float(MEGAPIXELS)
p['54']['inputs']['seed'] = SEED if SEED >= 0 else random.randint(0, 2**50)
req = urllib.request.Request('http://127.0.0.1:8188/prompt', data=json.dumps({'prompt': p}).encode(),
                             headers={'Content-Type': 'application/json'})
try:
    pid = json.loads(urllib.request.urlopen(req).read())['prompt_id']
except urllib.error.HTTPError as e:
    raise RuntimeError(e.read().decode()[:2000])
t0 = time.time(); print('已送出，生成中…（seed', p['54']['inputs']['seed'], '）')
while True:
    time.sleep(10)
    h = json.loads(urllib.request.urlopen('http://127.0.0.1:8188/history/' + pid).read())
    if pid in h:
        st = h[pid]['status']
        if st.get('status_str') == 'error':
            print(json.dumps(st, ensure_ascii=False)[:3000]); raise RuntimeError('生成失敗')
        break
    print(f'  {int(time.time() - t0)} 秒', end='\\r')
vids = [v for o in h[pid]['outputs'].values() for v in o.get('images', []) + o.get('videos', []) if v['filename'].endswith('.mp4')]
out = COMFY / 'output' / vids[0]['subfolder'] / vids[0]['filename']
print(f'完成，{(time.time() - t0) / 60:.1f} 分鐘：', out)
display(Video(str(out), embed=True, width=640))
from google.colab import files; files.download(str(out))
"""),
    code("""#@title 🔍 出問題時跑這格：看 GPU 與 ComfyUI 紀錄
import subprocess
print(subprocess.run(['nvidia-smi'], capture_output=True, text=True).stdout)
log = open('/content/comfyui.log', encoding='utf-8', errors='ignore').read()
keys = ('error', 'Error', 'int8', 'quant', 'fallback', 'out of memory', 'loaded', 'Prompt executed')
print('\\n'.join(l for l in log.splitlines() if any(k in l for k in keys))[-4000:])
"""),
]


def without(cell, *snippets):
    """從共用格子拿掉數字人專用的片段；片段找不到就報錯，避免改了上面卻靜默沒拿掉。"""
    src = cell['source']
    for s in snippets:
        assert s in src, '找不到要拿掉的片段：' + s[:60]
        src = src.replace(s, '')
    return dict(cell, source=src)


HEAD_BASIC = md("""# MiniMax H3｜ComfyUI 基礎版（Colab）

在 Colab 上裝好 ComfyUI 與 MiniMax H3 模型組，**不帶任何工作流**——開好介面後自己拉節點、或拖入自己的工作流學習。
由上往下依序執行。**執行階段請選 L4 GPU**（A100 也可以但很耗點數；T4 跑不動）。

| 步驟 | 做什麼 | 時間（L4 實測） |
|---|---|---|
| ① 檢查＋安裝 | GPU／驅動不符就停下，不安裝；通過才裝 ComfyUI＋節點管理器＋ListHelper | 約 1 分鐘 |
| ② 啟動 | 背景啟動 ComfyUI | 約 1 分鐘 |
| ③ 開介面 | Colab 內建代理（推薦）；③-b 是 cloudflare 備援 | 數秒 |
| ④ 下載模型 | 約 37 GB（ComfyUI 確定正常才下載） | 約 1 分鐘 |

想直接做「會說話的數字人」：改開同一個 repo 的 `H3_Digital_Human.ipynb`。

**Hugging Face 權杖（建議）**：左側欄「🔑 密鑰」新增 `HF_TOKEN`（請用**唯讀**權杖），並開啟「筆記本存取權」。

模型：`Minimax-h3_Singularity_x_10Eros-Max_PRISM`（int8，加速已融合，6 步）＋官方 Qwen3-VL 文字編碼器與 VAE。授權依照各來源模型。

最新版：<https://github.com/dseditor/minimax-h3-colab>""")

C2_BASIC = without(cells[2],
    '＋把 H3 數字人工作流放進清單\nUI_WORKFLOW = ' + repr(ui_wf),
    """# 工作流放進清單（③、③-b 都看得到）；檔名用英文，避免網址編碼在代理上出問題
wf_dir = COMFY / 'user' / 'default' / 'workflows'
wf_dir.mkdir(parents=True, exist_ok=True)
(wf_dir / 'H3_digital_human.json').write_text(UI_WORKFLOW, encoding='utf-8')
old_cn = wf_dir / 'H3數字人.json'
if old_cn.exists(): old_cn.unlink()
Path('/content/H3_digital_human.json').write_text(UI_WORKFLOW, encoding='utf-8')
""")
C3_BASIC = without(cells[3], """# 如果左側工作流清單打不開（404），按下面下載 JSON，直接拖進 ComfyUI 畫面就好
from google.colab import files
files.download('/content/H3_digital_human.json')
""")

# ---------------------------------------------------------------- H3 Toybox（網頁前端）
# ComfyUI 只當背景引擎、不開它的介面；瀏覽器只開 toybox/ 這個前端（Colab 內建代理）。
TOYBOX = [
    md("""# H3 Toybox｜Colab 版（可愛的 MiniMax H3 網頁前端）

把想像丟進玩具箱，搖一搖就變成影片 🧸 ComfyUI 只在背景當引擎，**不用開 ComfyUI 介面**，所有操作都在 H3 Toybox 網頁裡。

由上往下依序執行。**執行階段請選 L4 GPU**（A100 也可以但很耗點數；T4 跑不動）。

| 步驟 | 做什麼 |
|---|---|
| ① 檢查＋安裝 | GPU／驅動不符就停下；通過才裝 ComfyUI、3 個節點組、H3 Toybox |
| ② 下載模型 | 約 50 GB（含 7 GB 的提示詞改寫模型，可以不勾） |
| ③ 啟動＋開網頁 | 背景啟動 ComfyUI 與 H3 Toybox，用 Colab 內建代理開網頁；作品預設存到雲端硬碟「H3Toybox」資料夾 |
| ③-b | 瀏覽器的 Google 帳號和 Colab 不同時，改用 cloudflare 網址 |

**網頁裡能做的事**
- 🧸 **參考生成**：圖片 9 張／影片 3 支／聲音 3 段當參考，提示詞裡用 `@圖片1` 標記
- 🎞 **頭尾格**：只放首格、只放尾格、或兩張都放
- ✏️ **純文字**：只靠一段描述
- 🧵 **延伸影片**：參考影片勾「延伸這支影片」，新片段會從它的最後一格接下去，並自動接回原片
- ✨ **提示詞改寫**：把中文描述擴寫成 H3 規格的英文提示詞（改寫結果預設收起來）。預設用 H3 官方的 9B 改寫模型；也可以在 ⚙ 設定改用 **Gemini** 或 **OpenAI 相容端點**
- ⚡ **快速模式**（預設開）：8 步出片；關掉是 12 步
- 📁 **素材庫資料夾**：把圖片、影片、聲音拖進資料夾整理
- 每支影片都會顯示目前做到哪一步、還要多久

**Hugging Face 權杖（建議）**：左側欄「🔑 密鑰」新增 `HF_TOKEN`（請用**唯讀**權杖），並開啟「筆記本存取權」。

模型：`Minimax-h3_Singularity_x_10Eros-Max_PRISM`（int8）＋官方 Qwen3-VL 文字編碼器與 VAE；LoRA、語意橋、潛空間放大器與改寫模型鏡像在 [dseditor/minimax-h3-studio-assets](https://huggingface.co/dseditor/minimax-h3-studio-assets)（各檔來源見該頁）。授權依照各來源模型。

最新版：<https://github.com/dseditor/minimax-h3-colab>"""),
    code("""#@title ① 檢查環境＋安裝 ComfyUI、節點與 H3 Toybox（GPU 或驅動不符就停下，不會安裝）
import torch, shutil, subprocess, sys, os
from pathlib import Path
if not torch.cuda.is_available():
    raise RuntimeError('沒有 GPU：請到「執行階段 → 變更執行階段類型」選 L4 GPU，再重跑這格')
cap = torch.cuda.get_device_capability(0)
drv = subprocess.run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'], capture_output=True, text=True).stdout.strip()
print('GPU:', torch.cuda.get_device_name(0), '| capability', cap, '| VRAM', round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1), 'GiB')
print('驅動:', drv, '| 可用磁碟', round(shutil.disk_usage('/content').free / 2**30, 1), 'GiB')
if cap < (8, 0):
    raise RuntimeError(f'這張卡（capability {cap}）跑不動 int8 模型，沒有安裝任何東西。請換 L4（或 A100）再重跑。')
if int(drv.split('.')[0]) < 580:
    raise RuntimeError(f'驅動 {drv} 不支援 CUDA 13（需要 580 以上），沒有安裝任何東西。請換一台執行階段（L4）再試。')
if shutil.disk_usage('/content').free < 60 * 2**30:
    raise RuntimeError('磁碟剩不到 60 GiB，放不下約 50 GB 的模型。請換一台執行階段。')
print('✅ 環境檢查通過，開始安裝')
try:
    from google.colab import userdata
    os.environ['HF_TOKEN'] = userdata.get('HF_TOKEN'); print('已讀到 HF_TOKEN')
except Exception:
    print('沒有設定 HF_TOKEN，之後改用匿名下載（人多時可能被限速）')

def clone(url, dest):
    if not Path(dest).exists():
        subprocess.run(['git', 'clone', '--depth', '1', url, str(dest)], check=True)
    else:
        subprocess.run(['git', '-C', str(dest), 'pull', '--ff-only'], check=False)

COMFY = Path('/content/ComfyUI')
clone('https://github.com/Comfy-Org/ComfyUI.git', COMFY)
if not (torch.version.cuda or '').startswith('13'):
    print('安裝 CUDA 13 版 PyTorch（int8 ConvRot 權重需要）…')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'torch', 'torchvision', 'torchaudio',
                    '--index-url', 'https://download.pytorch.org/whl/cu130'], check=True)
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(COMFY / 'requirements.txt'), 'huggingface_hub', 'hf_xet'], check=True)
# 工作流用到的三個節點組（都不會動到 PyTorch）
NODES = {
    'minimax-h3-audio-t8': 'https://github.com/T8mars/comfyui-minimax-h3-audio-T8',        # 語意橋 Semantic Bridge
    'comfyui-SelfLift': 'https://github.com/facok/comfyui-SelfLift',                      # 低解析度→潛空間放大→高解析度
    'ComfyUI-H3-Motion-Context': 'https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context',  # 延伸影片（氣鎖）
}
for name, url in NODES.items():
    clone(url, COMFY / 'custom_nodes' / name)
# 導演 Skill 只是文字檔：放在 custom_nodes 外面，不載入它的節點
clone('https://github.com/T8mars/comfyui-minimax-h3-prompt-enhancer-T8', '/content/t8-enhancer')
# H3 Toybox 本體
clone('https://github.com/dseditor/minimax-h3-colab.git', '/content/minimax-h3-colab')
APP = Path('/content/minimax-h3-colab/toybox')
print('ffmpeg:', shutil.which('ffmpeg') or '沒有（延伸影片會失敗）')
out = subprocess.run([sys.executable, '-c', 'import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available())'],
                     capture_output=True, text=True)
print('ComfyUI 會用的 PyTorch：', out.stdout.strip(), out.stderr[-300:])
print('✅ 安裝完成')
"""),
    code("""#@title ② 下載模型（約 50 GB）
# 預設直接從 Hugging Face 下載（最快）。HF 權杖已在 ① 讀入。
# 只有在 Hugging Face 撞到流量上限時，才勾選 USE_DRIVE_CACHE：
#   勾選後會從「我的雲端硬碟/H3_models」複製（比 HF 慢），雲端硬碟沒有的檔案才去 HF 抓並順便存一份。
USE_DRIVE_CACHE = False  #@param {type:"boolean"}
#@markdown 不勾就少下載 7 GB：網頁的本地改寫會關掉，可以到 ⚙ 設定改用 Gemini 或 OpenAI 端點改寫。
INCLUDE_REWRITER = True  #@param {type:"boolean"}
from huggingface_hub import hf_hub_download
import shutil, time
CACHE = None
if USE_DRIVE_CACHE:
    from google.colab import drive
    drive.mount('/content/drive')
    CACHE = Path('/content/drive/MyDrive/H3_models'); CACHE.mkdir(parents=True, exist_ok=True)
ASSETS = 'dseditor/minimax-h3-studio-assets'
files = [  # (repo, 檔案, 放到 models/ 底下的哪裡)
    ('OliviaRossi/Minimax-h3_Singularity-x-10Eros-Max_PRISM', 'Minimax-h3_Singularity_x_10Eros-Max_PRISM_int8.safetensors', 'diffusion_models'),
    ('Comfy-Org/MiniMax-H3', 'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors', 'text_encoders'),
    ('Comfy-Org/MiniMax-H3', 'vae/minimax_h3_video_vae_int8_convrot.safetensors', 'vae'),
    ('Comfy-Org/MiniMax-H3', 'vae/minimax_h3_audio_vae_fp32.safetensors', 'vae'),
] + [(ASSETS, f, str(Path(f).parent)) for f in [
    'loras/minimax_h3_dmad_full_dareties_v4_step600_comfy_fro.safetensors',
    'loras/minimax_h3_turbo_v4_step600_ema_DasiwaREF2VAHybridV1_0_curveproj1025_compat_v001.safetensors',
    'loras/MysticXXX_MMH3-V2.safetensors',
    'loras/H3_Combat_V2.safetensors',
    'loras/Motion_Repair.safetensors',
    'loras/H3_SPEED_slider.safetensors',
    'semantic_bridge/t8_compat/BUNNY_H3_ActionLogic_Bridge_V1_T8_Compat.safetensors',
    'latent_upscale_models/minimax_h3_latent_upscaler_3d_fp16_V2.safetensors',
] + (['text_encoders/minimax_h3_rewriter_ref2va_qwen3.5_9b_w4a8.safetensors'] if INCLUDE_REWRITER else [])]
t0 = time.time()
for repo, name, folder in files:
    target = COMFY / 'models' / folder / Path(name).name
    target.parent.mkdir(parents=True, exist_ok=True)
    cached = CACHE / Path(name).name if CACHE else None
    if not target.is_file():
        if cached and cached.is_file():
            print('從雲端硬碟複製', cached.name); shutil.copy(str(cached), str(target))
        else:
            got = Path(hf_hub_download(repo_id=repo, filename=name, local_dir='/content/hf'))
            shutil.move(str(got), str(target))
    if cached and not cached.is_file():
        print('存一份到雲端硬碟', cached.name); shutil.copy(str(target), str(cached))
    print(f'{folder:<28} {target.name[:60]:<60} {target.stat().st_size / 2**30:5.1f} GiB')
# 官方提示詞寫作指南（改寫模型照它寫）
GUIDES = APP / 'guides'; GUIDES.mkdir(exist_ok=True)
for g in ('VIDEO_PROMPT_WRITING_GUIDE_ref_en.md', 'VIDEO_PROMPT_WRITING_GUIDE_base_en.md'):
    got = Path(hf_hub_download(repo_id='MiniMaxAI/MiniMax-H3', filename='docs/' + g, local_dir='/content/hf'))
    shutil.copy(str(got), str(GUIDES / g))
print(f'✅ 下載完成，{(time.time() - t0) / 60:.1f} 分鐘')
"""),
    code("""#@title ③ 啟動 ComfyUI（背景）＋ H3 Toybox，開網頁
# 執行後下面會出現一個連結；顯示成 localhost 也沒關係，直接點它就會開新分頁。
# ⚠️ 開分頁的瀏覽器必須登入「跟這本 Colab 同一個 Google 帳號」；帳號不同就改用下一格 ③-b。
#@markdown **作品存到雲端硬碟**：素材庫、資料夾、生成的影片、生成紀錄都放在「我的雲端硬碟/H3Toybox」，
#@markdown 斷線或不小心關掉執行階段，重開後再跑一次 ①②③ 東西都還在。第一次會跳出授權視窗，按同意即可。
SAVE_TO_DRIVE = True  #@param {type:"boolean"}
#@markdown API 金鑰也存到雲端硬碟（下次不用重貼；但雲端硬碟裡會有一份金鑰檔）
SAVE_API_KEYS = False  #@param {type:"boolean"}
import subprocess, time, urllib.request, json, sys, os, shutil
def wait_http(url, name, log, tries=180):
    for i in range(tries):
        try:
            urllib.request.urlopen(url, timeout=2); print(name, '已啟動'); return
        except Exception:
            time.sleep(2)
    print(open(log, encoding='utf-8', errors='ignore').read()[-3000:])
    raise RuntimeError(name + ' 沒有啟動成功，看上面的紀錄')

for proc in ('server', 'toybox'):
    if proc in globals() and globals()[proc].poll() is None:
        globals()[proc].terminate(); globals()[proc].wait()
# ComfyUI 只當引擎：只聽 127.0.0.1，不對外開介面
server = subprocess.Popen([sys.executable, 'main.py', '--listen', '127.0.0.1', '--port', '8188'],
                          cwd=str(COMFY), stdout=open('/content/comfyui.log', 'w'), stderr=subprocess.STDOUT)
wait_http('http://127.0.0.1:8188/system_stats', 'ComfyUI', '/content/comfyui.log')
# 作品放哪裡：雲端硬碟（授權成功）或這台執行階段的暫存空間
DATA = Path('/content/h3toybox_data')
if SAVE_TO_DRIVE:
    try:
        from google.colab import drive
        drive.mount('/content/drive')
        DATA = Path('/content/drive/MyDrive/H3Toybox')
    except Exception as e:
        print('⚠️ 沒有掛上雲端硬碟（', e, '）：這次的作品只存在暫存空間，關掉執行階段就會消失')
DATA.mkdir(parents=True, exist_ok=True)
# ComfyUI 生成的影片直接寫進 DATA/videos（用連結，不用另外同步）
videos, out_dir = DATA / 'videos', COMFY / 'output' / 'H3Toybox'
videos.mkdir(exist_ok=True)
out_dir.parent.mkdir(parents=True, exist_ok=True)
if out_dir.is_dir() and not out_dir.is_symlink():
    for f in out_dir.iterdir():
        shutil.move(str(f), str(videos / f.name))
    out_dir.rmdir()
if not out_dir.exists():
    out_dir.symlink_to(videos, target_is_directory=True)
# H3 Toybox 的 Colab 設定（保留使用者在網頁設定頁改過的內容，只覆寫這台機器的路徑）
(COMFY / 'input').mkdir(exist_ok=True)
cfg_path = DATA / 'config.json'
cfg = json.loads(cfg_path.read_text(encoding='utf-8')) if cfg_path.exists() else {}
cfg.update({
    'comfy_url': 'http://127.0.0.1:8188',
    'comfy_input_dir': str(COMFY / 'input'),
    'skills_dir': '/content/t8-enhancer/directional_skills',
    'free_after_rewrite': True,   # Colab 記憶體比較小：改寫完就把 9B 模型卸載
})
cfg.pop('guides', None)
cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
secrets = DATA / 'secrets.json' if SAVE_API_KEYS else Path('/content/h3toybox_secrets.json')
toybox = subprocess.Popen([sys.executable, 'server.py'], cwd=str(APP),
                          env=dict(os.environ, H3TOYBOX_PORT='8190', H3TOYBOX_DATA=str(DATA), H3TOYBOX_SECRETS=str(secrets)),
                          stdout=open('/content/toybox.log', 'w'), stderr=subprocess.STDOUT)
wait_http('http://127.0.0.1:8190/api/config', 'H3 Toybox', '/content/toybox.log', tries=30)
print('📁 作品存放位置：', DATA)
from google.colab import output
output.serve_kernel_port_as_window(8190, anchor_text='🧸 打開 H3 Toybox')
"""),
    code("""#@title ③-b（備援）用 cloudflare 網址開 H3 Toybox：瀏覽器登入的 Google 帳號和 Colab 不同時用這格
# ⚠️ 網址沒有密碼，拿到的人都能用你的 GPU —— 不要貼到公開的地方。
import subprocess, re, time
if not Path('/content/cloudflared').exists():
    subprocess.run(['wget', '-q', '-O', '/content/cloudflared',
                    'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64'], check=True)
    subprocess.run(['chmod', '+x', '/content/cloudflared'], check=True)
# 通道的輸出寫進檔案，不要接 PIPE：PIPE 沒人讀會塞滿，通道就卡住
if 'tun' in globals() and tun.poll() is None:
    tun.terminate(); tun.wait()
open('/content/cloudflared.log', 'w').close()
tun = subprocess.Popen(['/content/cloudflared', 'tunnel', '--no-autoupdate', '--url', 'http://127.0.0.1:8190'],
                       stdout=open('/content/cloudflared.log', 'a'), stderr=subprocess.STDOUT)
url = None
for i in range(60):
    time.sleep(1)
    m = re.search(r'https://[a-z0-9-]+\\.trycloudflare\\.com', open('/content/cloudflared.log').read())
    if m:
        url = m.group(0); break
print('👉 H3 Toybox：', url or '（沒拿到網址，看 /content/cloudflared.log）')
"""),
    code("""#@title 🔍 出問題時跑這格：看 GPU、ComfyUI 與 H3 Toybox 的紀錄
import subprocess
print(subprocess.run(['nvidia-smi'], capture_output=True, text=True).stdout)
print(subprocess.run(['free', '-g'], capture_output=True, text=True).stdout)
log = open('/content/comfyui.log', encoding='utf-8', errors='ignore').read()
keys = ('error', 'Error', 'Traceback', 'out of memory', 'SelfLift timing] high_resolution total', 'Prompt executed', 'IMPORT FAILED')
print('\\n'.join(l for l in log.splitlines() if any(k in l for k in keys))[-4000:])
print('---- H3 Toybox ----')
print(open('/content/toybox.log', encoding='utf-8', errors='ignore').read()[-2000:])
"""),
]

NOTEBOOKS = {
    'H3_Digital_Human.ipynb': cells,
    'H3_Basic_ComfyUI.ipynb': [HEAD_BASIC, cells[1], C2_BASIC, C3_BASIC, cells[4], cells[5], cells[9]],
    'H3_Toybox.ipynb': TOYBOX,
}
for fname, cs in NOTEBOOKS.items():
    nb = {'cells': cs,
          'metadata': {'accelerator': 'GPU', 'colab': {'provenance': [], 'gpuType': 'L4'},
                       'kernelspec': {'name': 'python3', 'display_name': 'Python 3'}},
          'nbformat': 4, 'nbformat_minor': 0}
    io.open(fname, 'w', encoding='utf-8', newline='\n').write(json.dumps(nb, ensure_ascii=False, indent=1) + '\n')
    # 自檢：每個 code cell 都要能編譯
    for i, c in enumerate(cs):
        if c['cell_type'] == 'code':
            compile(c['source'], f'{fname}:cell{i}', 'exec')
    print('ok', fname, 'cells =', len(cs))
