# -*- coding: utf-8 -*-
"""H3 digital human: drive a ComfyUI server (Colab tunnel or local) through its HTTP API. Stdlib only.

python h3_colab.py --url https://xxxx.trycloudflare.com --check
python h3_colab.py --url <URL> --image photo.jpg --prompt prompt.txt --seconds 10 --out result.mp4
"""
import argparse, json, os, random, sys, time, uuid, urllib.parse, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument('--url', required=True)
ap.add_argument('--check', action='store_true')
ap.add_argument('--image')
ap.add_argument('--prompt')
ap.add_argument('--seconds', type=int, default=10)
ap.add_argument('--megapixels', type=float, default=0.4)
ap.add_argument('--seed', type=int, default=-1)
ap.add_argument('--out', default='h3_result.mp4')
ap.add_argument('--timeout-min', type=int, default=30)
a = ap.parse_args()
URL = a.url.rstrip('/')
HERE = os.path.dirname(os.path.abspath(__file__))


def get(path, timeout=30):
    return json.loads(urllib.request.urlopen(URL + path, timeout=timeout).read())


try:
    dev = get('/system_stats', 20)['devices'][0]
    print('[OK] connected: %s, VRAM %.1f GiB' % (dev['name'], dev['vram_total'] / 2**30))
except Exception as e:
    print('[FAIL] cannot reach %s/system_stats (%s)' % (URL, e))
    print('       Check that Colab cells 2 and 3-b are still running and the URL is correct.')
    sys.exit(2)
if a.check:
    sys.exit(0)
assert a.image and os.path.isfile(a.image), 'image not found'
assert a.prompt and os.path.isfile(a.prompt), 'prompt file not found'
assert 5 <= a.seconds <= 15, 'seconds must be 5-15'

# upload (multipart)
b = uuid.uuid4().hex
fn = os.path.basename(a.image)
body = (('--%s\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n' % b).encode()
        + ('--%s\r\nContent-Disposition: form-data; name="image"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n' % (b, fn)).encode()
        + open(a.image, 'rb').read() + ('\r\n--%s--\r\n' % b).encode())
req = urllib.request.Request(URL + '/upload/image', data=body, headers={'Content-Type': 'multipart/form-data; boundary=' + b})
up = json.loads(urllib.request.urlopen(req, timeout=120).read())
img = (up['subfolder'] + '/' + up['name']) if up.get('subfolder') else up['name']
print('[OK] uploaded image:', img)

prompt = open(a.prompt, encoding='utf-8').read().strip()
wf = json.load(open(os.path.join(HERE, '..', 'assets', 'workflow_api.json'), encoding='utf-8'))
wf['7']['inputs']['prompt'] = prompt
wf['5']['inputs']['image'] = img
wf['56']['inputs']['value'] = a.seconds
wf['61']['inputs']['megapixels'] = a.megapixels
seed = a.seed if a.seed >= 0 else random.randint(0, 2**31 - 1)
wf['54']['inputs']['seed'] = seed
req = urllib.request.Request(URL + '/prompt', data=json.dumps({'prompt': wf}).encode('utf-8'),
                             headers={'Content-Type': 'application/json'})
pid = json.loads(urllib.request.urlopen(req, timeout=60).read())['prompt_id']
print('[OK] queued %s (seed %d, %d s). Words in prompt: %d' % (pid, seed, a.seconds, len(prompt.split())))

t0 = time.time()
while True:
    time.sleep(10)
    h = get('/history/' + pid)
    if pid in h:
        e = h[pid]
        if e['status'].get('status_str') == 'error':
            print(json.dumps(e['status'], ensure_ascii=False)[:3000]); sys.exit('Generation failed')
        break
    if time.time() - t0 > a.timeout_min * 60:
        sys.exit('Timed out')
    print('  ... %d s' % (time.time() - t0))

v = e['outputs']['16']['images'][0]
q = urllib.parse.urlencode({'filename': v['filename'], 'subfolder': v['subfolder'], 'type': v['type']})
data = urllib.request.urlopen(URL + '/view?' + q, timeout=300).read()
open(a.out, 'wb').write(data)
print('[DONE] %s (%.1f MB) in %.1f min' % (os.path.abspath(a.out), len(data) / 2**20, (time.time() - t0) / 60))
