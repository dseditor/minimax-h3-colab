# Colab 內建代理會把網址裡的 %2F 解成 /，ComfyUI 的 /userdata/{file} 就配不到 → 工作流清單點開 404。
# 這個小節點補一條「被拆成兩段」的路由，讀寫同一個使用者資料夾。
import os
from aiohttp import web
import folder_paths
from server import PromptServer

routes = PromptServer.instance.routes
BASE = os.path.abspath(os.path.join(folder_paths.get_user_directory(), 'default'))


def _path(req):
    p = os.path.abspath(os.path.join(BASE, req.match_info['sub'], req.match_info['file']))
    if not p.startswith(BASE + os.sep):
        raise web.HTTPForbidden()
    return p


@routes.get('/userdata/{sub}/{file}')
async def _get(req):
    p = _path(req)
    if not os.path.isfile(p):
        return web.Response(status=404)
    return web.FileResponse(p)


@routes.post('/userdata/{sub}/{file}')
async def _post(req):
    p = _path(req)
    if os.path.exists(p) and req.query.get('overwrite', 'true') == 'false':
        return web.Response(status=409, text='File already exists')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as fh:
        fh.write(await req.read())
    rel = os.path.relpath(p, BASE).replace(os.sep, '/')
    if req.query.get('full_info', 'false') == 'true':
        st = os.stat(p)
        return web.json_response({'path': rel, 'size': st.st_size, 'modified': st.st_mtime})
    return web.json_response(rel)


@routes.delete('/userdata/{sub}/{file}')
async def _delete(req):
    p = _path(req)
    if not os.path.isfile(p):
        return web.Response(status=404)
    os.remove(p)
    return web.Response(status=204)


NODE_CLASS_MAPPINGS = {}
