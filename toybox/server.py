"""H3 Toybox - a cute web frontend for MiniMax H3 on top of a running ComfyUI.

Modes
  ref     Ref2VA: up to 9 images / 3 videos / 3 audios as references (@圖片1 ...).
          One tray video can be marked "extend": its tail is pinned into the head of the
          new clip (H3 Motion Context) and the result is joined back onto the source.
  frames  I2VA / FL2VA / L2VA: first frame, last frame, or both (MiniMaxH3ImageToVideo).
  text    T2VA: text only.

Run with ComfyUI's embedded python (stdlib + PIL + av, all already installed):
    F:\\CUI\\python_embeded\\python.exe -I server.py
then open http://127.0.0.1:8190
"""
import base64
import copy
import json
import mimetypes
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import av
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
LIBRARY = ROOT / "library"
OUTPUTS = ROOT / "outputs"
CONFIG_PATH = ROOT / "config.json"
JOBS_PATH = ROOT / "jobs.json"
STATS_PATH = ROOT / "stats.json"
TEMPLATE_PATH = ROOT / "WF.json"
PORT = int(os.environ.get("H3TOYBOX_PORT", 8190))
FPS = 24

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}

DEFAULT_CONFIG = {
    "comfy_url": "http://127.0.0.1:8188",
    "comfy_input_dir": r"F:\CUI\ComfyUI\input",
    "guides": {
        "ref": str(ROOT / "guides" / "VIDEO_PROMPT_WRITING_GUIDE_ref_en.md"),
        "base": str(ROOT / "guides" / "VIDEO_PROMPT_WRITING_GUIDE_base_en.md"),
    },
    "skills_dir": r"F:\CUI\ComfyUI\custom_nodes\minimax-h3-seedance-music3-prompt-enhancer-t8\directional_skills",
    "rewriter": {
        "model": "minimax_h3_rewriter_ref2va_qwen3.5_9b_w4a8.safetensors",
        "thinking": False,
        "mtp": "auto",
        "max_length": 8192,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "image_side": 768,
        "video_frames": 8,
    },
    # LoRA node ids in WF.json. "drama" LoRAs always apply; "action" LoRAs apply only
    # when motion intensity is on, with strength = template strength * multiplier.
    "loras": {
        "drama": ["53", "400", "404"],
        "action": ["342", "344", "343"],
    },
    "intensity_multipliers": {"low": 0.6, "mid": 1.0, "high": 1.3},
    # extension (H3 Motion Context): frames of the source tail pinned into the new clip
    "extend": {"context_length": "22", "audio_context_length": 24, "tail_seconds": 3.0},
    # Colab: unload the 9B rewriter right after it answers, so it never sits in RAM next to the H3 models
    "free_after_rewrite": False,
}

# Node ids in WF.json that the server rewires
N_COND, N_SAMPLER, N_SCHED, N_SHIFT, N_BRIDGE = "265", "397", "261", "333", "402"
N_VAE, N_AVAE, N_CLIP, N_LEN, N_SECONDS = "36", "37", "38", "250", "259"
N_DECODE, N_ADECODE = "253", "254"
PREVIEW_ONLY = ("401", "329", "364", "51", "252")  # ChunkFeedForward + preview override + VHS + sample inputs

SYSTEM_REF = (
    "You enhance prompts for reference-conditioned video and audio generation (Ref2VA). "
    "Infer reference roles from the user's prompt and supplied media. "
    "Preserve intent and explicit constraints while adding relevant visual and sound details. "
    "Follow the guide below and return only its six sections in order, "
    "with plain-text headers on separate lines and no Markdown fences.\n\n"
)
SYSTEM_BASE = (
    "You enhance prompts for video and audio generation ({task}). "
    "Preserve intent and explicit constraints while adding relevant visual and sound details. "
    "Follow the guide below. Return the alignment instruction line if the task has one, then only the three core "
    "sections integrated_multimodal_description, overall_soundscape and non_diegetic_music, "
    "with plain-text headers and no Markdown fences.\n\n"
)
LABEL = {"image": "Picture", "video": "Video", "audio": "Audio"}
CHINESE_NAME = {"image": "图片", "video": "视频", "audio": "音频"}
# @圖片1 / @影片1 / @音訊1, plus H3Easy-style @Image1 / @Video1 / @Audio1
MENTION_RE = re.compile(r"@(圖片|图片|Image|影片|视频|Video|音訊|音频|Audio)\s?(\d+)", re.IGNORECASE)
MENTION_KIND = {"圖片": "image", "图片": "image", "image": "image", "影片": "video", "视频": "video",
                "video": "video", "音訊": "audio", "音频": "audio", "audio": "audio"}
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4), "21:9": (21, 9)}
VISION = "<|vision_start|><|image_pad|><|vision_end|>"
# Display names follow the T8 enhancer's docs/directional-skills.md
SKILL_NAMES = {
    "wushu_combat": "Jojocodex-武術打鬥", "high_density_combat": "土豆-高密度連續攻防",
    "continuous_combat": "Fisher-連續戰鬥長鏡頭", "cinematic_gunfight": "兔子-電影槍戰導演",
    "ning_wenwu": "寧版-文武雙全", "drama_scene": "戲劇場面｜關係與潛台詞",
    "situational_drama": "情境戲劇｜處境與鋪墊回收", "zhenzhen_pov": "貞貞-POV劇情導演",
}

lock = threading.Lock()


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return copy.deepcopy(default)


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    saved = load_json(CONFIG_PATH, {})
    saved.pop("guide_path", None)  # H3 Studio's single-guide key
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


# ---------------------------------------------------------------- ComfyUI client

def comfy(cfg, path, payload=None, timeout=60):
    url = cfg["comfy_url"].rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def queue_prompt(cfg, wf):
    try:
        return comfy(cfg, "/prompt", {"prompt": wf, "client_id": MONITOR.client_id})["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError("ComfyUI 拒絕了工作流：" + e.read().decode("utf-8", "replace")[:2000])


def history_entry(cfg, prompt_id):
    return comfy(cfg, f"/history/{prompt_id}").get(prompt_id)


def wait_prompt(cfg, prompt_id, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        entry = history_entry(cfg, prompt_id)
        if entry:
            if entry["status"].get("status_str") == "error":
                raise RuntimeError(execution_error(entry))
            return entry
        time.sleep(1.0)
    raise RuntimeError("等待 ComfyUI 逾時")


def execution_error(entry):
    for name, msg in entry["status"].get("messages", []):
        if name == "execution_error":
            return f"{msg.get('node_type')}: {msg.get('exception_message', '').strip()}"
    return "ComfyUI 執行失敗"


def comfy_download(cfg, fileinfo, dest):
    q = urllib.parse.urlencode({"filename": fileinfo["filename"], "subfolder": fileinfo.get("subfolder", ""),
                                "type": fileinfo.get("type", "output")})
    with urllib.request.urlopen(cfg["comfy_url"].rstrip("/") + "/view?" + q, timeout=600) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


# ---------------------------------------------------------------- progress monitor (stdlib websocket)

class Monitor(threading.Thread):
    """Listens to ComfyUI's websocket so jobs can show their stage, step and a time estimate.

    Prompts are queued with this client id, so ComfyUI sends their progress events here.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.client_id = "h3toybox-" + uuid.uuid4().hex[:8]
        self.state = {}          # prompt_id -> live state
        self.connected = False
        self.mutex = threading.Lock()

    def run(self):
        while True:
            try:
                self.listen(load_config()["comfy_url"])
            except Exception:
                pass
            self.connected = False
            time.sleep(3)

    def listen(self, comfy_url):
        u = urllib.parse.urlparse(comfy_url)
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=30)
        if u.scheme == "https":
            import ssl
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {u.path.rstrip('/')}/ws?clientId={self.client_id} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                      f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1024)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("websocket refused")
        buf = head.split(b"\r\n\r\n", 1)[1]
        sock.settimeout(None)
        self.connected = True

        def read(n):
            nonlocal buf
            while len(buf) < n:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError("websocket closed")
                buf += chunk
            out, buf = buf[:n], buf[n:]
            return out

        while True:
            b1, b2 = read(2)
            opcode, length = b1 & 0x0F, b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", read(8))[0]
            mask = read(4) if b2 & 0x80 else None
            payload = read(length)
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if opcode == 8:
                raise ConnectionError("websocket closed")
            if opcode == 9:  # ping -> masked pong
                m = os.urandom(4)
                sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + m + bytes(c ^ m[i % 4] for i, c in enumerate(payload)))
            elif opcode == 1:
                try:
                    self.handle(json.loads(payload))
                except Exception:
                    pass

    def handle(self, msg):
        kind, data = msg.get("type"), msg.get("data") or {}
        pid = data.get("prompt_id")
        if not pid:
            return
        now = time.time()
        with self.mutex:
            st = self.state.setdefault(pid, {"started": None, "node": None, "node_since": None, "steps": [],
                                             "max": 0, "nodes_seen": {}, "done": False})
            if kind == "execution_start":
                st["started"] = now
            elif kind == "executing":
                node = data.get("node")
                if st["node"] is not None and st["node_since"]:
                    st["nodes_seen"][st["node"]] = now - st["node_since"]
                st["node"], st["node_since"] = node, now
                if node is None:
                    st["done"] = True
            elif kind == "progress":
                if data.get("node") == N_SAMPLER:
                    st["max"] = data.get("max", 0)
                    if not st["steps"] or st["steps"][-1][1] != data.get("value"):
                        st["steps"].append((now, data.get("value")))
            elif kind in ("execution_success", "execution_error", "execution_interrupted"):
                st["done"] = True
                st["finished"] = now

    def snapshot(self, pid):
        with self.mutex:
            st = self.state.get(pid)
            return copy.deepcopy(st) if st else None


MONITOR = Monitor()

# Defaults measured on an RTX 5090 at 1664x928, 124 frames, 6 low + 6 high SelfLift steps.
REF_WORK = 1664 * 928 * 124
DEFAULT_STATS = {"pre": 12.0, "low_step": 3.4, "high_step": 8.9, "transition": 1.6, "post": 16.0,
                 "rewrite": 25.0, "runs": 0}


def work_of(job):
    return max(1.0, job.get("width", 1280) * job.get("height", 720) * (float(job.get("seconds", 5)) * FPS + 1))


def scales(job):
    r = work_of(job) / REF_WORK
    return r ** 1.0, r ** 1.25  # linear parts (load/decode), attention-ish parts (sampling)


def sampler_steps(job):
    total = int(job.get("steps", 12))
    low = int(job.get("transition_step", 6))
    return low, total - low


def estimate(job, st, stats):
    """Returns (stage, percent 0-100, eta seconds or None)."""
    lin, att = scales(job)
    n_low, n_high = sampler_steps(job)
    pre = stats["pre"] * lin
    low_step, high_step = stats["low_step"] * att, stats["high_step"] * att
    post = stats["post"] * lin + (12 if job.get("extend") else 0)
    full = pre + n_low * low_step + stats["transition"] + n_high * high_step + post
    now = time.time()
    if not st or not st.get("started"):
        return "queued", 0, full
    elapsed = now - st["started"]
    steps = st["steps"]
    node = st.get("node")
    if not steps:
        if node in (N_DECODE, N_ADECODE) or (node and st["nodes_seen"].get(N_SAMPLER)):
            remain = max(2.0, post - (now - (st["node_since"] or now)))
            stage = "decode"
        else:
            remain = max(3.0, pre - elapsed) + n_low * low_step + stats["transition"] + n_high * high_step + post
            stage = "prepare"
    else:
        v = steps[-1][1]
        durs = [steps[i][0] - steps[i - 1][0] for i in range(1, len(steps))]
        low_durs = [d for (t, s), d in zip(steps[1:], durs) if s <= n_low]
        high_durs = [d for (t, s), d in zip(steps[1:], durs) if s > n_low + 1]
        if low_durs:
            low_step = sorted(low_durs)[len(low_durs) // 2]
            high_step = low_step * stats["high_step"] / stats["low_step"]
        if high_durs:
            high_step = sorted(high_durs)[len(high_durs) // 2]
        since = now - steps[-1][0]
        if node == N_SAMPLER and v < n_low + n_high:
            stage = "low" if v < n_low else "high"
            if v < n_low:
                remain = max(0.0, (n_low - v) * low_step - since) + stats["transition"] + n_high * high_step + post
            else:
                remain = max(0.0, (n_low + n_high - v) * high_step - since) + post
        else:
            stage = "decode"
            remain = max(2.0, post - (now - (st["node_since"] or now)))
    percent = max(1, min(99, int(100 * elapsed / (elapsed + remain))))
    return stage, percent, remain


def learn(job, st):
    """Fold a finished job's measured stage times into stats.json (exponential moving average)."""
    steps = st.get("steps") or []
    if not st.get("started") or len(steps) < 3:
        return
    lin, att = scales(job)
    n_low, n_high = sampler_steps(job)
    first_step = steps[0][0]
    durs = [(steps[i][1], steps[i][0] - steps[i - 1][0]) for i in range(1, len(steps))]
    low = [d for s, d in durs if s <= n_low]
    high = [d for s, d in durs if s > n_low + 1]
    end = st.get("finished") or time.time()
    sample = {"pre": max(0.0, first_step - st["started"]) / lin, "post": max(0.0, end - steps[-1][0]) / lin}
    if low:
        sample["low_step"] = sorted(low)[len(low) // 2] / att
    if high:
        sample["high_step"] = sorted(high)[len(high) // 2] / att
    if job.get("extend"):
        sample["post"] = max(0.0, sample["post"] - 12 / lin)
    with lock:
        stats = load_json(STATS_PATH, DEFAULT_STATS)
        for k, v in sample.items():
            stats[k] = round(stats[k] * 0.6 + v * 0.4, 3) if stats.get("runs") else round(v, 3)
        stats["runs"] = stats.get("runs", 0) + 1
        save_json(STATS_PATH, stats)


# ---------------------------------------------------------------- asset library

def library_index():
    return load_json(LIBRARY / "library.json", [])


def probe_video(path):
    with av.open(str(path)) as c:
        stream = c.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration else float(c.duration or 0) / av.time_base
        fps = float(stream.average_rate or 0)
        return duration, fps, len(c.streams.audio) > 0, stream.codec_context.width, stream.codec_context.height


def video_frames(path, count):
    """Sample frames uniformly like post_vllm.sample_video; returns [(timestamp, PIL.Image)]."""
    duration, fps, *_ = probe_video(path)
    last = max(0.0, duration - max(0.1, 1 / fps if fps > 0 else 0.1))
    if fps > 0:
        count = max(1, min(count, round(duration * fps)))
    stamps = [round(last * i / (count - 1), 6) for i in range(count)] if count > 1 else [0.0]
    out = []
    with av.open(str(path)) as c:
        stream = c.streams.video[0]
        for ts in stamps:
            c.seek(int(ts / stream.time_base), stream=stream, backward=True)
            frame = None
            for f in c.decode(stream):
                frame = f
                if f.time is not None and f.time >= ts:
                    break
            if frame is not None:
                out.append((ts, frame.to_image()))
    return duration, out


def last_frame(path):
    """The final decodable frame of a video."""
    frame = None
    with av.open(str(path)) as c:
        stream = c.streams.video[0]
        duration = probe_video(path)[0]
        c.seek(int(max(0.0, duration - 1.5) / stream.time_base), stream=stream, backward=True)
        for f in c.decode(stream):
            frame = f
    if frame is None:
        raise ValueError("讀不到延伸影片的最後一格")
    return frame.to_image()


def add_asset(cfg, filename, body):
    ext = Path(filename).suffix.lower()
    kind = "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else None
    if kind is None:
        raise ValueError(f"不支援的檔案格式：{ext}")
    asset_id = uuid.uuid4().hex[:10]
    LIBRARY.mkdir(exist_ok=True)
    stored = LIBRARY / f"{asset_id}{ext}"
    stored.write_bytes(body)
    item = {"id": asset_id, "name": Path(filename).name, "kind": kind, "file": stored.name,
            "comfy_name": f"h3fe_{asset_id}{ext}", "created": time.time()}
    thumb = LIBRARY / f"{asset_id}_thumb.jpg"
    if kind == "image":
        with Image.open(stored) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            item["width"], item["height"] = im.size
            im.thumbnail((360, 360))
            im.save(thumb, quality=85)
        item["thumb"] = thumb.name
    elif kind == "video":
        duration, fps, has_audio, w, h = probe_video(stored)
        item.update(duration=round(duration, 2), fps=round(fps, 2), has_audio=has_audio, width=w, height=h)
        frames = video_frames(stored, 1)[1]
        if frames:
            im = frames[0][1].convert("RGB")
            im.thumbnail((360, 360))
            im.save(thumb, quality=85)
            item["thumb"] = thumb.name
    shutil.copyfile(stored, Path(cfg["comfy_input_dir"]) / item["comfy_name"])
    with lock:
        index = library_index()
        index.insert(0, item)
        save_json(LIBRARY / "library.json", index)
    return item


def add_output_to_library(cfg, job_id):
    job = next((j for j in load_json(JOBS_PATH, []) if j["id"] == job_id), None)
    if not job or job.get("status") != "done":
        raise ValueError("這個工作還沒有完成的影片")
    if job.get("full_video"):
        body, name = (OUTPUTS / job["full_video"]).read_bytes(), job["full_video"]
    else:
        v = job["video"]
        tmp = OUTPUTS / f"_lib_{job_id}.mp4"
        comfy_download(cfg, v, tmp)
        body, name = tmp.read_bytes(), v["filename"]
        tmp.unlink(missing_ok=True)
    return add_asset(cfg, name, body)


def delete_asset(cfg, asset_id):
    with lock:
        index = library_index()
        item = next((i for i in index if i["id"] == asset_id), None)
        if item is None:
            return
        index.remove(item)
        save_json(LIBRARY / "library.json", index)
    for name in (item["file"], item.get("thumb")):
        if name:
            (LIBRARY / name).unlink(missing_ok=True)
    (Path(cfg["comfy_input_dir"]) / item["comfy_name"]).unlink(missing_ok=True)


# ---------------------------------------------------------------- references

def asset(asset_id):
    item = next((i for i in library_index() if i["id"] == asset_id), None)
    if item is None:
        raise ValueError("素材已經不在素材庫裡了")
    return item


def resolve_mentions(text, tray):
    """Map @圖片N mentions (N = position among tray items of that kind) to compact H3 tags.

    The extension source video is not a reference, so it is skipped when numbering.
    Only mentioned assets are used, numbered by first mention, like H3Easy's per-segment binding.
    Returns (text_with_tags, {"image": [...], "video": [...], "audio": [...]}).
    """
    index = {i["id"]: i for i in library_index()}
    by_kind = {"image": [], "video": [], "audio": []}
    for entry in tray:
        item = index.get(entry["id"])
        if item and not entry.get("extend"):
            by_kind[item["kind"]].append(dict(item, use_audio=entry.get("use_audio", True)))
    chosen = {"image": [], "video": [], "audio": []}
    assigned = {}

    def repl(m):
        kind = MENTION_KIND[m.group(1).lower() if m.group(1).isascii() else m.group(1)]
        n = int(m.group(2))
        pool = by_kind[kind]
        if not 1 <= n <= len(pool):
            raise ValueError(f"找不到參考素材 {m.group(0)}")
        key = (kind, n)
        if key not in assigned:
            chosen[kind].append(pool[n - 1])
            assigned[key] = len(chosen[kind])
        return f"<{LABEL[kind]} {assigned[key]}>"

    return MENTION_RE.sub(repl, text), chosen


def extend_source(req):
    """The tray video marked for extension (ref mode only), or None."""
    if req.get("mode", "ref") != "ref":
        return None
    marked = [t for t in req.get("tray", []) if t.get("extend")]
    if len(marked) > 1:
        raise ValueError("一次只能標記一支影片作為延伸")
    if not marked:
        return None
    item = asset(marked[0]["id"])
    if item["kind"] != "video":
        raise ValueError("只有影片可以標記為延伸")
    return item


def extend_last_frame(cfg, src):
    """Save the source's last frame into ComfyUI's input folder; the new clip continues from it."""
    name = f"h3t_last_{src['id']}.jpg"
    path = Path(cfg["comfy_input_dir"]) / name
    if not path.exists():
        last_frame(LIBRARY / src["file"]).convert("RGB").save(path, quality=95)
    return {"id": f"last_{src['id']}", "name": "延伸來源最後一格", "kind": "image", "comfy_name": name,
            "path": str(path), "extend_frame": True}


def collect(cfg, req, text):
    """Resolve the request into (tagged_text, chosen media, task, extend source)."""
    mode = req.get("mode", "ref")
    if mode == "ref":
        tagged, chosen = resolve_mentions(text, req.get("tray", []))
        src = extend_source(req)
        if src:
            chosen["image"].append(extend_last_frame(cfg, src))
        return tagged, chosen, "Ref2VA", src
    chosen = {"image": [], "video": [], "audio": []}
    if mode == "frames":
        first, last = req.get("first_id"), req.get("last_id")
        if not first and not last:
            raise ValueError("頭尾格模式至少要放一張首格或尾格")
        for asset_id in (first, last):
            if asset_id:
                item = asset(asset_id)
                if item["kind"] != "image":
                    raise ValueError("首格與尾格必須是圖片")
                chosen["image"].append(item)
        task = "FL2VA" if first and last else "I2VA" if first else "L2VA"
        return text, chosen, task, None
    return text, chosen, "T2VA", None


def media_path(item):
    return Path(item["path"]) if item.get("path") else LIBRARY / item["file"]


# ---------------------------------------------------------------- prompt rewriting

def load_skills(cfg):
    skills = []
    base = Path(cfg["skills_dir"])
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if (d / "SKILL.md").is_file():
                skills.append({"id": d.name, "title": SKILL_NAMES.get(d.name, d.name)})
    return skills


def skill_text(cfg, skill_id):
    if not skill_id:
        return ""
    path = Path(cfg["skills_dir"]) / skill_id / "SKILL.md"
    if path.parent.parent != Path(cfg["skills_dir"]) or not path.is_file():
        raise ValueError(f"找不到導演 Skill：{skill_id}")
    return re.sub(r"^---\n.*?\n---\n", "", path.read_text(encoding="utf-8"), flags=re.S).strip()


def pad_square(im, side):
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((side, side))
    canvas = Image.new("RGB", (side, side), "white")
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    return canvas


def alignment_line(task, duration, last_shot=1):
    """The fixed first line the base guide requires for keyframe tasks (section 2.1)."""
    end = f"{duration:.2f}"
    if task == "I2VA":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if task == "FL2VA":
        return ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
                f"0.00-second mark of the target video; Picture 2 (from Shot {last_shot}) aligns with the "
                f"{end}-second mark of the target video.")
    if task == "L2VA":
        return ("How the reference pictures align with the target video — <Picture 1> (from "
                f"[Shot {last_shot}]) aligns with the {end}-second mark of the target video.")
    return ""


BASE_SECTIONS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description") + BASE_SECTIONS[1:]


def normalize_base(text, task, duration):
    """Bring the rewriter's answer into the base guide's format.

    The rewriter was trained on Ref2VA, so for T2VA/I2VA/FL2VA/L2VA it sometimes answers in the
    six Ref2VA sections. Keep its description, map <Subject N> back to plain words, and rebuild the
    alignment line, which is formulaic anyway.
    """
    text = re.sub(r"^```\w*\n|\n```$", "", text.strip())
    keys = "|".join(REF_SECTIONS + BASE_SECTIONS[:1])
    parts = {}
    for m in re.finditer(rf"^\s*({keys})\s*:\s*(.*?)(?=^\s*(?:{keys})\s*:|\Z)", text, flags=re.S | re.M):
        parts[m.group(1)] = m.group(2).strip()
    body = parts.get("integrated_multimodal_description") or parts.get("detailed_description")
    if not body:
        body = text
        for line in text.splitlines():  # drop a stray alignment line; it is rebuilt below
            if line.startswith(("How the reference pictures", "For the target video")):
                body = text.replace(line, "", 1).strip()
    subjects = {}
    for m in re.finditer(r"<Subject (\d+)>\s+is\s+(.+)", parts.get("subject_definitions", "")):
        subjects[m.group(1)] = m.group(2).split(",")[0].strip().rstrip(".")
    body = re.sub(r"<Subject (\d+)>", lambda m: subjects.get(m.group(1), "the character"), body)
    shots = [int(n) for n in re.findall(r"\[Shot (\d+)\]", body)]
    out = []
    head = alignment_line(task, duration, max(shots) if shots else 1)
    if head:
        out += [head, ""]
    out.append("integrated_multimodal_description: " + body)
    out.append("\noverall_soundscape: " + (parts.get("overall_soundscape") or "N/A"))
    out.append("\nnon_diegetic_music: " + (parts.get("non_diegetic_music") or "N/A"))
    return "\n".join(out)


def raw_prompt(text, task, duration, chosen, src):
    """Final prompt when the rewriter is off."""
    if task == "Ref2VA":
        if src:
            k = len(chosen["image"])
            text += (f"\n<Picture {k}> is the last frame of the previous clip; the video continues directly from it.")
        return text
    head = alignment_line(task, duration)
    return (head + "\n\n" if head else "") + text


def build_rewrite(cfg, tagged_text, chosen, task, duration, skill_id, job_id, src):
    """Raw Qwen3.5 chat text + vision images, matching post_vllm.build_messages ordering."""
    rw = cfg["rewriter"]
    side = int(rw["image_side"])
    names = ({"Subject": "主体", "Picture": "图片", "Video": "视频", "Audio": "音频"}
             if re.search(r"[\u3400-\u9fff]", tagged_text) else
             {"Subject": "subject ", "Picture": "image ", "Video": "video ", "Audio": "audio "})
    original = re.sub(r"<(Subject|Picture|Video|Audio)\s+(\d+)>", lambda m: names[m[1]] + m[2], tagged_text.strip())

    text = f"Target video duration: {duration:.6f} seconds.\n"
    if task == "Ref2VA":
        if chosen["image"]:
            text += "Inspect the reference images labeled <Picture N> and use them according to the user's instructions.\n"
        if src:
            k = len(chosen["image"])
            text += (f"<Picture {k}> is the last frame of the previous clip. The target video is its direct continuation: "
                     f"it starts exactly from <Picture {k}> and carries the same scene, characters and motion forward.\n")
        if chosen["video"]:
            text += "Video frames are sparse samples of their labeled <Video N>; timestamps refer to source time, not target cuts.\n"
        if chosen["audio"]:
            text += ("The user has also provided reference audio, but you do not know its actual content and should not guess. "
                     "Enhance the prompt according to the user's instructions.\n")
    else:
        text += f"Task: {task}.\n"
        if task == "I2VA":
            text += "<Picture 1> is the actual first frame of the video at 0.00 seconds.\n"
        elif task == "FL2VA":
            text += f"<Picture 1> is the first frame at 0.00 seconds and <Picture 2> is the last frame at {duration:.2f} seconds.\n"
        elif task == "L2VA":
            text += f"<Picture 1> is the final frame of the video at {duration:.2f} seconds.\n"
    text += ("Choose shots that best express the original intent. "
             "Allocate shot durations and cut timing within the target duration according to content and pacing. "
             "Respect explicit shot requests, reference constraints and the guide's transition rules.\n")
    skill = skill_text(cfg, skill_id)
    if skill:
        text += ("Directing method for choreography and shot planning only; it never changes the guide's "
                 f"output format, reference labels, dialogue or duration:\n{skill}\n")
    text += f"Original prompt:\n{original}\n"

    images = []
    for kind in ("image", "video", "audio"):
        for i, item in enumerate(chosen[kind], start=1):
            label = f"<{LABEL[kind]} {i}>"
            text += f"{CHINESE_NAME[kind]}{i} / reference {kind} {i}: {label}."
            if kind == "image":
                with Image.open(media_path(item)) as im:
                    images.append(pad_square(im, side))
                text += VISION
            elif kind == "video":
                src_duration, frames = video_frames(media_path(item), int(rw["video_frames"]))
                text += f"{label} duration: {src_duration:.6f}s. Frames follow in source order."
                for ts, im in frames:
                    images.append(pad_square(im, side))
                    text += f"{label} frame near source time {ts:.6f}s." + VISION

    if task == "Ref2VA":
        system = SYSTEM_REF + Path(cfg["guides"]["ref"]).read_text(encoding="utf-8")
    else:
        system = SYSTEM_BASE.format(task=task) + Path(cfg["guides"]["base"]).read_text(encoding="utf-8")
    raw = (f"<|im_start|>system\n{system.strip()}<|im_end|>\n"
           f"<|im_start|>user\n{text.strip()}<|im_end|>\n<|im_start|>assistant\n")
    raw += "<think>\n" if rw["thinking"] else "<think>\n\n</think>\n\n"

    inp = Path(cfg["comfy_input_dir"])
    wf = {"1": {"class_type": "CLIPLoader", "inputs": {"clip_name": rw["model"], "type": "stable_diffusion", "device": "default"}}}
    batch = None
    for n, im in enumerate(images):
        name = f"h3fe_rw_{job_id}_{n}.jpg"
        im.save(inp / name, quality=92)
        wf[f"i{n}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        if batch is None:
            batch = [f"i{n}", 0]
        else:
            wf[f"b{n}"] = {"class_type": "ImageBatch", "inputs": {"image1": batch, "image2": [f"i{n}", 0]}}
            batch = [f"b{n}", 0]
    gen = {"clip": ["1", 0], "prompt": raw, "max_length": int(rw["max_length"]),
           "sampling_mode": "on", "sampling_mode.temperature": float(rw["temperature"]),
           "sampling_mode.top_k": int(rw["top_k"]), "sampling_mode.top_p": float(rw["top_p"]),
           "sampling_mode.min_p": 0.0, "sampling_mode.repetition_penalty": 1.0,
           "sampling_mode.seed": random.randint(0, 2**31 - 1), "sampling_mode.presence_penalty": 0.0,
           "thinking": bool(rw["thinking"]), "use_default_template": True, "mtp": rw["mtp"]}
    if batch:
        gen["image"] = batch
    wf["2"] = {"class_type": "TextGenerate", "inputs": gen}
    wf["3"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
    return wf, len(images)


def rewrite(cfg, req):
    tagged, chosen, task, src = collect(cfg, req, req["prompt"])
    job_id = uuid.uuid4().hex[:8]
    duration = float(req["seconds"])
    wf, n_images = build_rewrite(cfg, tagged, chosen, task, duration, req.get("skill"), job_id, src)
    t0 = time.time()
    try:
        entry = wait_prompt(cfg, queue_prompt(cfg, wf))
    finally:
        for n in range(n_images):
            (Path(cfg["comfy_input_dir"]) / f"h3fe_rw_{job_id}_{n}.jpg").unlink(missing_ok=True)
    text = "".join(entry["outputs"].get("3", {}).get("text", [])).strip()
    if cfg.get("free_after_rewrite"):
        try:
            comfy(cfg, "/free", {"unload_models": True, "free_memory": True})
        except Exception:
            pass
    if task != "Ref2VA":
        text = normalize_base(text, task, duration)
    used = round(time.time() - t0, 1)
    with lock:
        stats = load_json(STATS_PATH, DEFAULT_STATS)
        stats["rewrite"] = round(stats["rewrite"] * 0.6 + used * 0.4, 1)
        save_json(STATS_PATH, stats)
    return {"prompt": text, "seconds_used": used, "task": task}


# ---------------------------------------------------------------- video generation

def round32(x):
    return max(32, int(round(x / 32)) * 32)


def dims(aspect, resolution, auto_size=None):
    """aspect "auto" follows auto_size (first frame / extension source) and falls back to 16:9."""
    short = int(resolution.rstrip("pP"))
    if aspect == "auto" and auto_size:
        a, b = auto_size
    else:
        a, b = ASPECTS.get(aspect, (16, 9))
    w, h = (short * a / b, short) if a >= b else (short, short * b / a)
    return round32(w), round32(h)


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("找不到 ffmpeg：延伸影片需要 ffmpeg")


def conform(src_path, dest, width, height, has_audio, tail=None):
    """Re-encode (the tail of) a video at 24 fps and the generation size, always with a stereo track."""
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
    if tail:
        cmd += ["-sseof", f"-{tail}"]
    cmd += ["-i", str(src_path)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-vf", f"fps={FPS},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
            "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0", "-shortest",
            "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


def join_clips(first, second, dest):
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(first), "-i", str(second), "-filter_complex",
           "[0:a]aresample=48000,aformat=channel_layouts=stereo[a0];[1:a]aresample=48000,aformat=channel_layouts=stereo[a1];"
           "[0:v][a0][1:v][a1]concat=n=2:v=1:a=1[v][a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


def bypass(wf, node_id, port="model"):
    """Remove a pass-through node and wire its consumers to its input."""
    upstream = wf[node_id]["inputs"][port]
    for other in wf.values():
        for k, v in other["inputs"].items():
            if v == [node_id, 0]:
                other["inputs"][k] = upstream
    del wf[node_id]


def build_generate(cfg, final_text, chosen, task, req, size, src_job=None):
    wf = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    # preview-only nodes are not needed behind a web frontend
    for node_id in ("329", "401"):
        if node_id in wf:
            bypass(wf, node_id)
    for node_id in PREVIEW_ONLY:
        wf.pop(node_id, None)
    width, height = size

    if task == "Ref2VA":
        ref = wf[N_COND]["inputs"]
        for k in [k for k in ref if k.startswith("ref_") and "." in k]:  # autogrow slots only
            del ref[k]
        for i, item in enumerate(chosen["image"]):
            wf[f"ri{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["comfy_name"]}}
            ref[f"ref_images.ref_image_{i}"] = [f"ri{i}", 0]
        for i, item in enumerate(chosen["video"]):
            wf[f"rv{i}"] = {"class_type": "LoadVideo", "inputs": {"file": item["comfy_name"]}}
            wf[f"rc{i}"] = {"class_type": "GetVideoComponents", "inputs": {"video": [f"rv{i}", 0]}}
            ref[f"ref_videos.ref_video_{i}"] = [f"rc{i}", 0]
            if item.get("has_audio") and item.get("use_audio", True):
                ref[f"ref_video_audios.ref_video_audio_{i}"] = [f"rc{i}", 1]
        for i, item in enumerate(chosen["audio"]):
            wf[f"ra{i}"] = {"class_type": "LoadAudio", "inputs": {"audio": item["comfy_name"]}}
            ref[f"ref_audios.ref_audio_{i}"] = [f"ra{i}", 0]
        ref["prompt"] = final_text
        ref["width"], ref["height"] = width, height
    else:  # T2VA / I2VA / FL2VA / L2VA share the stock image-to-video conditioning
        old = wf[N_COND]["inputs"]
        node = {"clip": [N_CLIP, 0], "vae": [N_VAE, 0], "prompt": final_text, "width": width, "height": height,
                "length": old["length"]}
        frames = chosen["image"]
        slots = {"I2VA": ["first_frame"], "FL2VA": ["first_frame", "last_frame"], "L2VA": ["last_frame"]}.get(task, [])
        for i, (slot, item) in enumerate(zip(slots, frames)):
            wf[f"kf{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["comfy_name"]}}
            wf[f"ks{i}"] = {"class_type": "ImageScale", "inputs": {"image": [f"kf{i}", 0], "upscale_method": "lanczos",
                                                                    "width": width, "height": height, "crop": "center"}}
            node[slot] = [f"ks{i}", 0]
        wf[N_COND] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": node}

    wf[N_SECONDS]["inputs"]["value"] = float(req["seconds"])
    seed = req.get("seed")
    wf[N_SAMPLER]["inputs"]["seed"] = int(seed) if seed not in (None, "", -1) else random.randint(0, 2**48)

    images, audio = [N_DECODE, 0], [N_ADECODE, 0]
    if src_job:  # pin the source tail into the head of the clip, then cut the pinned frames off again
        ext = cfg["extend"]
        wf["ev"] = {"class_type": "LoadVideo", "inputs": {"file": src_job["tail_name"]}}
        wf["ec"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["ev", 0]}}
        wf["mc"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {
            "conditioning": [N_BRIDGE, 0], "vae": [N_VAE, 0], "latent": [N_COND, 1],
            "context_length": str(ext["context_length"]), "audio_context_length": int(ext["audio_context_length"]),
            "context_frames": ["ec", 0], "audio_vae": [N_AVAE, 0], "context_audio": ["ec", 1]}}
        wf[N_SAMPLER]["inputs"]["positive"] = ["mc", 0]
        wf["mt"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {
            "images": images, "trim_frames": ["mc", 1], "audio": audio, "fps": float(FPS), "match_tail": True}}
        images, audio = ["mt", 0], ["mt", 1]

    wf["cv"] = {"class_type": "CreateVideo", "inputs": {"images": images, "audio": audio, "fps": float(FPS)}}
    wf["sv"] = {"class_type": "SaveVideo", "inputs": {"video": ["cv", 0], "filename_prefix": "H3Toybox/h3t",
                                                      "format": "mp4", "format.codec": "h264"}}

    intensity = req.get("intensity", "off")
    mult = cfg["intensity_multipliers"].get(intensity)
    for node_id in cfg["loras"]["action"]:
        node = wf.get(node_id)
        if node is None:
            continue
        if mult is None:  # intensity off: bypass by rewiring consumers to this LoRA's input model
            bypass(wf, node_id)
        else:
            node["inputs"]["strength_model"] = round(node["inputs"]["strength_model"] * mult, 4)
    return wf, wf[N_SAMPLER]["inputs"]["seed"]


def generate(cfg, req):
    mode = req.get("mode", "ref")
    if mode not in ("ref", "frames", "text"):
        raise ValueError("未知的模式")
    duration = float(req["seconds"])
    tagged, chosen, task, src = collect(cfg, req, req["prompt"])
    final_text = req.get("final_prompt") or raw_prompt(tagged, task, duration, chosen, src)

    auto_size = None
    if src:
        auto_size = (src["width"], src["height"])
    elif task in ("I2VA", "FL2VA", "L2VA"):
        first = chosen["image"][0]
        auto_size = (first.get("width"), first.get("height")) if first.get("width") else None
    size = dims(req["aspect"], req["resolution"], auto_size)

    job_id = uuid.uuid4().hex[:12]
    src_job = None
    if src:
        tail = f"h3t_tail_{job_id}.mp4"
        conform(LIBRARY / src["file"], Path(cfg["comfy_input_dir"]) / tail, *size, src.get("has_audio"),
                tail=cfg["extend"]["tail_seconds"])
        src_job = {"tail_name": tail}
    wf, seed = build_generate(cfg, final_text, chosen, task, req, size, src_job)
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    prompt_id = queue_prompt(cfg, wf)
    job = {"id": prompt_id, "created": time.time(), "status": "queued", "mode": mode, "task": task,
           "prompt": req["prompt"], "final_prompt": final_text, "seed": seed, "aspect": req["aspect"],
           "resolution": req["resolution"], "width": size[0], "height": size[1], "seconds": req["seconds"],
           "intensity": req.get("intensity", "off"), "skill": req.get("skill") or "", "tray": req.get("tray", []),
           "first_id": req.get("first_id"), "last_id": req.get("last_id"),
           "steps": template[N_SCHED]["inputs"].get("steps", 12),
           "transition_step": template[N_SAMPLER]["inputs"].get("transition_step", 6)}
    if src:
        job["extend"] = {"source_id": src["id"], "source_name": src["name"], "tail": src_job["tail_name"],
                         "has_audio": src.get("has_audio", False)}
    with lock:
        jobs = load_json(JOBS_PATH, [])
        jobs.insert(0, job)
        save_json(JOBS_PATH, jobs)
    return job


def saved_video(entry):
    """The video our SaveVideo node wrote.

    LoadVideo also reports its input file as a UI output (type "input"), so only files of
    type "output" count, and the save node ("sv"; "364" for H3 Studio's VHS jobs) is preferred.
    """
    outputs = entry.get("outputs", {})
    ordered = [outputs[k] for k in ("sv", "364") if k in outputs] + list(outputs.values())
    for node_out in ordered:
        for key in ("images", "gifs", "videos", "video"):
            for f in node_out.get(key, []) or []:
                if (isinstance(f, dict) and f.get("type", "output") == "output"
                        and str(f.get("filename", "")).lower().endswith((".mp4", ".webm", ".mkv", ".mov"))):
                    return f
    return None


def finish_extend(cfg, job):
    """Join the conformed source and the new clip into one longer video."""
    src = asset(job["extend"]["source_id"])
    OUTPUTS.mkdir(exist_ok=True)
    part = OUTPUTS / f"{job['id']}_part.mp4"
    comfy_download(cfg, job["video"], part)
    full_src = OUTPUTS / f"{job['id']}_src.mp4"
    conform(LIBRARY / src["file"], full_src, job["width"], job["height"], src.get("has_audio"))
    name = f"{job['id']}_full.mp4"
    join_clips(full_src, part, OUTPUTS / name)
    for p in (part, full_src):
        p.unlink(missing_ok=True)
    (Path(cfg["comfy_input_dir"]) / job["extend"]["tail"]).unlink(missing_ok=True)
    return name


def refresh_jobs(cfg):
    with lock:
        jobs = load_json(JOBS_PATH, [])
    pending = [j for j in jobs if j["status"] in ("queued", "running", "joining")]
    if pending:
        try:
            q = comfy(cfg, "/queue")
        except OSError:
            return jobs
        running = {item[1] for item in q.get("queue_running", [])}
        for job in pending:
            if job["status"] == "joining":
                continue
            entry = history_entry(cfg, job["id"])
            if entry:
                st = MONITOR.snapshot(job["id"])
                if st:
                    learn(job, st)
                if entry["status"].get("status_str") == "error":
                    job.update(status="error", error=execution_error(entry))
                    continue
                job.update(video=saved_video(entry), finished=time.time())
                if job.get("extend") and job["video"]:
                    job["status"] = "joining"
                    threading.Thread(target=join_worker, args=(cfg, dict(job)), daemon=True).start()
                else:
                    job["status"] = "done"
            elif job["id"] in running:
                job["status"] = "running"
        with lock:
            stored = {j["id"]: j for j in load_json(JOBS_PATH, [])}
            stored.update({j["id"]: j for j in pending})
            jobs = sorted(stored.values(), key=lambda j: -j["created"])
            save_json(JOBS_PATH, jobs)
    stats = load_json(STATS_PATH, DEFAULT_STATS)
    for job in jobs:
        if job["status"] in ("queued", "running"):
            stage, percent, eta = estimate(job, MONITOR.snapshot(job["id"]), stats)
            job["progress"] = {"stage": stage, "percent": percent, "eta": round(eta) if eta is not None else None,
                               "live": MONITOR.connected}
        elif job["status"] == "joining":
            job["progress"] = {"stage": "join", "percent": 99, "eta": 5, "live": True}
    return jobs


def join_worker(cfg, job):
    try:
        update = {"status": "done", "full_video": finish_extend(cfg, job)}
    except Exception as e:
        update = {"status": "done", "join_error": f"接回原片失敗：{e}"}
    with lock:
        jobs = load_json(JOBS_PATH, [])
        for j in jobs:
            if j["id"] == job["id"]:
                j.update(update)
        save_json(JOBS_PATH, jobs)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not path.is_file():
            return self.send_error(404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def body_json(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        cfg = load_config()
        try:
            if url.path == "/":
                return self.send_file(STATIC / "index.html")
            if url.path.startswith("/static/"):
                return self.send_file(safe_child(STATIC, url.path[len("/static/"):]))
            if url.path.startswith("/library/"):
                return self.send_file(safe_child(LIBRARY, url.path[len("/library/"):]))
            if url.path.startswith("/outputs/"):
                return self.send_file(safe_child(OUTPUTS, url.path[len("/outputs/"):]))
            if url.path == "/api/library":
                return self.send_json(library_index())
            if url.path == "/api/jobs":
                return self.send_json(refresh_jobs(cfg))
            if url.path == "/api/config":
                wf = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
                loras = [{"id": k, "name": v["inputs"]["lora_name"], "strength": v["inputs"]["strength_model"]}
                         for k, v in wf.items() if v["class_type"] == "LoraLoaderModelOnly"]
                stats = load_json(STATS_PATH, DEFAULT_STATS)
                return self.send_json({"config": cfg, "skills": load_skills(cfg), "aspects": list(ASPECTS),
                                       "lora_nodes": loras, "rewrite_seconds": stats["rewrite"]})
            if url.path == "/api/models":
                info = comfy(cfg, "/object_info/CLIPLoader")
                names = info["CLIPLoader"]["input"]["required"]["clip_name"][0]
                return self.send_json([n for n in names if "qwen3.5" in n.lower() or "rewriter" in n.lower()])
            if url.path == "/api/view":
                q = urllib.parse.urlencode(dict(urllib.parse.parse_qsl(url.query)))
                with urllib.request.urlopen(cfg["comfy_url"].rstrip("/") + "/view?" + q) as r:
                    data = r.read()
                    ctype = r.headers.get("Content-Type", "video/mp4")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            self.send_error(404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        cfg = load_config()
        try:
            if url.path == "/api/upload":
                name = urllib.parse.unquote(self.headers.get("X-Filename", "upload"))
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                return self.send_json(add_asset(cfg, name, body))
            if url.path == "/api/library/delete":
                delete_asset(cfg, self.body_json()["id"])
                return self.send_json({"ok": True})
            if url.path == "/api/library/from_job":
                return self.send_json(add_output_to_library(cfg, self.body_json()["id"]))
            if url.path == "/api/rewrite":
                return self.send_json(rewrite(cfg, self.body_json()))
            if url.path == "/api/generate":
                return self.send_json(generate(cfg, self.body_json()))
            if url.path == "/api/jobs/delete":
                job_id = self.body_json()["id"]
                with lock:
                    save_json(JOBS_PATH, [j for j in load_json(JOBS_PATH, []) if j["id"] != job_id])
                return self.send_json({"ok": True})
            if url.path == "/api/config":
                new = self.body_json()
                save_json(CONFIG_PATH, {k: new[k] for k in DEFAULT_CONFIG if k in new})
                return self.send_json({"ok": True})
            self.send_error(404)
        except subprocess.CalledProcessError as e:
            self.send_json({"error": "ffmpeg 失敗：" + (e.stderr or b"").decode("utf-8", "replace")[-800:]}, 400)
        except (ValueError, RuntimeError, KeyError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)


def safe_child(base, rel):
    path = (base / urllib.parse.unquote(rel)).resolve()
    if base.resolve() not in path.parents:
        raise ValueError("invalid path")
    return path


if __name__ == "__main__":
    LIBRARY.mkdir(exist_ok=True)
    OUTPUTS.mkdir(exist_ok=True)
    MONITOR.start()
    print(f"H3 Toybox running at http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
