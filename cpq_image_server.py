# -*- coding: utf-8 -*-
"""配置报价CPQ —— 产品图片维护服务（默认 :8011）。

用途：给产品清单里的每个成品挂一张图片。
  - 产品清单来自 Postgres 的 `product_para_value`（产品参数值表 / 成品清单，
    与报价第 1 步「产品匹配」用的是同一张表）。
  - 图片存本地目录 product_images/，**文件名 = product_item_code（成品编码）**，
    扩展名沿用上传文件的原扩展名。是否"已配图"就看该目录下有没有同名文件。

由 cpq_suite_server.py 在后台线程里一起启动（也可单独运行调试）：
    python cpq_image_server.py --port 8011
"""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cpq_db

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_DIR = os.path.join(SCRIPT_DIR, "product_images")

# 产品清单来源（成品清单）。列名与 DA 本体一致，缺列会自动跳过。
PRODUCT_TABLE = "product_para_value"
COL_ID = "id"
COL_CODE = "product_item_code"        # ← 图片文件名用它
COL_NAME = "product_item_name"
EXTRA_COLS = ["cell_model", "rated_voltage", "rated_capacity", "application_scope",
              "service_life", "hermeticity"]
LIST_LIMIT = int(os.getenv("CPQ_IMAGE_LIST_LIMIT", "3000"))

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_UPLOAD = int(os.getenv("CPQ_IMAGE_MAX_MB", "10")) * 1024 * 1024


# ---------------------------------------------------------------------------
# 图片文件
# ---------------------------------------------------------------------------
def _safe_stem(code: str) -> str:
    """成品编码 -> 安全文件名主干。仅替换文件系统不允许的字符，尽量保持编码原样可读。"""
    s = (code or "").strip()
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s)
    return s.strip(". ")[:120]


def _find_image(code: str):
    """返回该成品编码已上传的图片文件名（含扩展名），没有则 None。"""
    stem = _safe_stem(code)
    if not stem:
        return None
    for ext in ALLOWED_EXT:
        p = os.path.join(IMAGE_DIR, stem + ext)
        if os.path.isfile(p):
            return stem + ext
    return None


def _all_images() -> dict:
    """目录里现有的全部图片：{文件名主干: 文件名}。"""
    out = {}
    try:
        for fn in os.listdir(IMAGE_DIR):
            stem, ext = os.path.splitext(fn)
            if ext.lower() in ALLOWED_EXT and os.path.isfile(os.path.join(IMAGE_DIR, fn)):
                out[stem] = fn
    except OSError:
        pass
    return out


def _save_image(code: str, filename: str, data: bytes) -> str:
    stem = _safe_stem(code)
    if not stem:
        raise ValueError("成品编码为空，无法命名图片")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("只支持 " + "/".join(sorted(e[1:] for e in ALLOWED_EXT)) + " 格式")
    if not data:
        raise ValueError("文件内容为空")
    if len(data) > MAX_UPLOAD:
        raise ValueError(f"图片超过 {MAX_UPLOAD // 1024 // 1024}MB 限制")
    os.makedirs(IMAGE_DIR, exist_ok=True)
    old = _find_image(code)                      # 换扩展名时先删旧的，避免一码两图
    if old and old != stem + ext:
        try:
            os.remove(os.path.join(IMAGE_DIR, old))
        except OSError:
            pass
    with open(os.path.join(IMAGE_DIR, stem + ext), "wb") as f:
        f.write(data)
    return stem + ext


def _delete_image(code: str) -> bool:
    fn = _find_image(code)
    if not fn:
        return False
    try:
        os.remove(os.path.join(IMAGE_DIR, fn))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 产品清单
# ---------------------------------------------------------------------------
def list_products(keyword: str = "") -> dict:
    """读成品清单并标注配图状态。连不上库时回退成"只列已有图片"，并带上错误说明。"""
    imgs = _all_images()
    try:
        cols, rows = cpq_db.run_select(f"SELECT * FROM {PRODUCT_TABLE}", LIST_LIMIT)
    except Exception as e:
        # 连不上库/无驱动：仍把已上传的图片列出来，方便核对与补传
        items = [{"id": "", "code": stem, "name": "（未连库，仅显示已上传图片）",
                  "extra": {}, "image": fn} for stem, fn in sorted(imgs.items())]
        return {"ok": False, "error": f"读取产品清单失败：{str(e).splitlines()[0][:160]}",
                "products": items, "total": len(items), "with_image": len(items),
                "db": cpq_db.DB_LABEL}

    idx = {c: i for i, c in enumerate(cols)}
    kw = (keyword or "").strip().lower()
    items = []
    for r in rows[:LIST_LIMIT]:
        def val(c):
            i = idx.get(c)
            return "" if i is None or r[i] is None else str(r[i])
        code = val(COL_CODE)
        if not code:
            continue
        name = val(COL_NAME)
        if kw and kw not in (code + " " + name).lower():
            continue
        items.append({
            "id": val(COL_ID),
            "code": code,
            "name": name,
            "extra": {c: val(c) for c in EXTRA_COLS if c in idx and val(c)},
            "image": imgs.get(_safe_stem(code)),
        })
    items.sort(key=lambda x: (x["image"] is not None, x["code"]))
    return {"ok": True, "products": items, "total": len(items),
            "with_image": sum(1 for x in items if x["image"]), "db": cpq_db.DB_LABEL}


# ---------------------------------------------------------------------------
# multipart 解析（不依赖将被移除的 cgi 模块）
# ---------------------------------------------------------------------------
def _parse_multipart(body: bytes, content_type: str):
    m = re.search(r'boundary=("?)([^";]+)\1', content_type or "")
    if not m:
        return {}, {}
    boundary = ("--" + m.group(2)).encode()
    fields, files = {}, {}
    for part in body.split(boundary):
        if not part or part in (b"--", b"--\r\n"):
            continue
        part = part.lstrip(b"\r\n")
        if part.endswith(b"\r\n"):
            part = part[:-2]
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = head.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]*)"', headers)
        if not nm:
            continue
        fn = re.search(r'filename="([^"]*)"', headers)
        if fn:
            files[nm.group(1)] = (fn.group(1), data)
        else:
            fields[nm.group(1)] = data.decode("utf-8", "replace").strip()
    return fields, files


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class ImageHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/products":
            self._json(200, list_products((q.get("q") or [""])[0]))
        elif path.startswith("/img/"):
            self._serve_image(urllib.parse.unquote(path[len("/img/"):]))
        elif path.startswith("/vendor/"):
            self._serve_vendor(urllib.parse.unquote(path.lstrip("/")))
        else:
            self._json(404, {"ok": False, "error": "未知接口"})

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/api/upload":
            self._json(404, {"ok": False, "error": "未知接口"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_UPLOAD + 1024 * 1024:
            self._json(400, {"ok": False, "error": "文件过大"})
            return
        body = self.rfile.read(length) if length else b""
        fields, files = _parse_multipart(body, self.headers.get("Content-Type", ""))
        code = fields.get("code", "")
        up = files.get("file")
        if not code or not up:
            self._json(400, {"ok": False, "error": "缺少成品编码或图片文件"})
            return
        try:
            fn = _save_image(code, up[0], up[1])
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        except OSError as e:
            self._json(500, {"ok": False, "error": f"写入失败：{e}"})
            return
        self._json(200, {"ok": True, "code": code, "image": fn})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/image":
            self._json(404, {"ok": False, "error": "未知接口"})
            return
        code = (urllib.parse.parse_qs(parsed.query).get("code") or [""])[0]
        self._json(200, {"ok": _delete_image(code)})

    def _serve_image(self, name: str):
        safe = os.path.basename(name)                     # 防目录穿越
        full = os.path.normpath(os.path.join(IMAGE_DIR, safe))
        if not full.startswith(IMAGE_DIR) or not os.path.isfile(full):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        if os.path.splitext(full)[1].lower() not in ALLOWED_EXT:
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._send(500, b"read error", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        self._send(200, data, ctype)

    def _serve_vendor(self, rel: str):
        """复用主目录下的 vendor/（tabler 图标），保持与其它页面同一套图标。"""
        full = os.path.normpath(os.path.join(SCRIPT_DIR, rel))
        vendor_root = os.path.join(SCRIPT_DIR, "vendor")
        if not full.startswith(vendor_root) or not os.path.isfile(full):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._send(500, b"read error", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        self._send(200, data, ctype)


class QuietServer(ThreadingHTTPServer):
    """客户端断开连接产生的噪音 traceback 直接忽略（与 cpq_suite_server 一致）。"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>产品图片维护 · 配置报价CPQ</title>
<link rel="stylesheet" href="vendor/tabler-icons/tabler-icons.min.css">
<style>
  :root{--primary:#6366f1;--bg:#f7f7f8;--card:#fff;--border:#e7e7ea;
        --text:#1f2023;--text2:#62636d;--text3:#9a9ba6;--ok:#10b981;--warn:#f59e0b;}
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--text);font-size:14px;}
  header{background:var(--card);border-bottom:1px solid var(--border);padding:14px 24px;
         display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:10;}
  header h1{margin:0;font-size:17px;font-weight:650;display:flex;align-items:center;gap:8px;}
  header h1 .ti{color:var(--primary)}
  .stat{font-size:12px;color:var(--text2);}
  .stat b{color:var(--primary)}
  .search{margin-left:auto;display:flex;gap:8px;align-items:center;}
  .search input{width:260px;padding:8px 12px;border:1px solid var(--border);border-radius:8px;
                background:var(--card);color:var(--text);outline:none;font-size:13px;}
  .search input:focus{border-color:var(--primary)}
  .filter{padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--card);
          color:var(--text);font-size:13px;cursor:pointer;}
  .wrap{padding:20px 24px 40px;}
  .msg{padding:10px 14px;border-radius:8px;font-size:13px;margin-bottom:16px;display:none;}
  .msg.err{display:block;background:rgba(239,68,68,.09);color:#b91c1c;border:1px solid rgba(239,68,68,.25);}
  .msg.ok{display:block;background:rgba(16,185,129,.10);color:#047857;border:1px solid rgba(16,185,129,.25);}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;
        display:flex;flex-direction:column;transition:box-shadow .15s,transform .15s;}
  .card:hover{box-shadow:0 8px 24px rgba(0,0,0,.09);transform:translateY(-2px)}
  .thumb{height:150px;background:#fafafb;display:flex;align-items:center;justify-content:center;
         cursor:pointer;position:relative;border-bottom:1px solid var(--border);overflow:hidden;}
  .thumb img{width:100%;height:100%;object-fit:contain;}
  .thumb .ph{display:flex;flex-direction:column;align-items:center;gap:6px;color:var(--text3);font-size:12px;}
  .thumb .ph .ti{font-size:30px}
  .thumb:hover .overlay{opacity:1}
  .overlay{position:absolute;inset:0;background:rgba(15,23,42,.55);color:#fff;display:flex;
           align-items:center;justify-content:center;gap:6px;font-size:13px;opacity:0;transition:opacity .15s;}
  .info{padding:10px 12px;flex:1;display:flex;flex-direction:column;gap:4px;}
  .code{font-size:13px;font-weight:650;word-break:break-all;}
  .name{font-size:12px;color:var(--text2);line-height:1.45;
        display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
  .tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px;}
  .tag{font-size:10px;padding:1px 6px;border-radius:10px;background:var(--bg);color:var(--text3);}
  .foot{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;
        border-top:1px solid var(--border);}
  .badge{font-size:11px;padding:2px 8px;border-radius:10px;}
  .badge.has{background:rgba(16,185,129,.12);color:var(--ok)}
  .badge.no{background:rgba(245,158,11,.14);color:var(--warn)}
  .del{background:none;border:none;color:var(--text3);cursor:pointer;font-size:14px;padding:2px 4px;}
  .del:hover{color:#ef4444}
  .empty{padding:60px 20px;text-align:center;color:var(--text2);}
  .lightbox{position:fixed;inset:0;background:rgba(15,23,42,.8);display:none;align-items:center;
            justify-content:center;z-index:100;padding:40px;}
  .lightbox.on{display:flex}
  .lightbox img{max-width:100%;max-height:100%;border-radius:8px;background:#fff}
</style>
</head>
<body>
<header>
  <h1><i class="ti ti-photo"></i> 产品图片维护</h1>
  <span class="stat" id="stat">加载中…</span>
  <div class="search">
    <select class="filter" id="filter">
      <option value="all">全部产品</option>
      <option value="no">未配图</option>
      <option value="has">已配图</option>
    </select>
    <input id="q" placeholder="搜索成品编码 / 描述">
  </div>
</header>
<div class="wrap">
  <div class="msg" id="msg"></div>
  <div class="grid" id="grid"></div>
</div>
<input type="file" id="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp" hidden>
<div class="lightbox" id="lb"><img id="lbImg" alt=""></div>

<script>
var PRODUCTS = [], PICK = null;
function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function msg(t,cls){var m=$('msg');m.textContent=t||'';m.className='msg'+(t?' '+cls:'');
  if(t&&cls==='ok')setTimeout(function(){if(m.textContent===t){m.textContent='';m.className='msg';}},3000);}

function load(){
  fetch('api/products').then(function(r){return r.json();}).then(function(d){
    PRODUCTS = d.products||[];
    if(!d.ok) msg(d.error||'读取产品清单失败','err'); else msg('','');
    $('stat').innerHTML = '共 <b>'+(d.total||0)+'</b> 个产品 · 已配图 <b>'+(d.with_image||0)+'</b> · 未配图 <b>'+
                          ((d.total||0)-(d.with_image||0))+'</b>';
    render();
  }).catch(function(e){ msg('无法连接图片服务：'+e.message,'err'); });
}

function render(){
  var q=($('q').value||'').trim().toLowerCase(), f=$('filter').value;
  var list=PRODUCTS.filter(function(p){
    if(f==='has'&&!p.image)return false;
    if(f==='no'&&p.image)return false;
    if(q&&(p.code+' '+p.name).toLowerCase().indexOf(q)<0)return false;
    return true;
  });
  if(!list.length){ $('grid').innerHTML='<div class="empty">没有匹配的产品。</div>'; return; }
  $('grid').innerHTML = list.map(function(p){
    var tags=Object.keys(p.extra||{}).slice(0,3).map(function(k){
      return '<span class="tag">'+esc(p.extra[k])+'</span>';}).join('');
    return '<div class="card">'+
      '<div class="thumb" data-code="'+esc(p.code)+'" data-img="'+esc(p.image||'')+'">'+
        (p.image ? '<img src="img/'+encodeURIComponent(p.image)+'" alt="">'
                 : '<div class="ph"><i class="ti ti-photo-plus"></i><span>点击上传图片</span></div>')+
        '<div class="overlay"><i class="ti ti-upload"></i>'+(p.image?'更换图片':'上传图片')+'</div>'+
      '</div>'+
      '<div class="info"><div class="code">'+esc(p.code)+'</div>'+
        '<div class="name">'+esc(p.name||'—')+'</div>'+
        (tags?'<div class="tags">'+tags+'</div>':'')+
      '</div>'+
      '<div class="foot"><span class="badge '+(p.image?'has':'no')+'">'+
        (p.image?'已配图':'未配图')+'</span>'+
        (p.image?'<button class="del" data-del="'+esc(p.code)+'" title="删除图片"><i class="ti ti-trash"></i></button>':'')+
      '</div></div>';
  }).join('');
  Array.prototype.forEach.call($('grid').querySelectorAll('.thumb'),function(el){
    el.onclick=function(ev){ if(ev.shiftKey&&el.dataset.img){ showLb(el.dataset.img); return; }
      PICK=el.dataset.code; $('file').value=''; $('file').click(); };
  });
  Array.prototype.forEach.call($('grid').querySelectorAll('[data-del]'),function(b){
    b.onclick=function(){ del(b.dataset.del); };
  });
}

function showLb(img){ $('lbImg').src='img/'+encodeURIComponent(img); $('lb').classList.add('on'); }
$('lb').onclick=function(){ $('lb').classList.remove('on'); };

$('file').onchange=function(){
  var f=$('file').files[0]; if(!f||!PICK) return;
  var fd=new FormData(); fd.append('code',PICK); fd.append('file',f);
  msg('正在上传 '+PICK+' …','ok');
  fetch('api/upload',{method:'POST',body:fd}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){ msg(d.error||'上传失败','err'); return; }
    msg('已保存：'+d.image,'ok');
    var p=PRODUCTS.filter(function(x){return x.code===d.code;})[0];
    if(p){ p.image=d.image; }
    $('stat').innerHTML='';
    load();
  }).catch(function(e){ msg('上传失败：'+e.message,'err'); });
};

function del(code){
  if(!confirm('删除「'+code+'」的图片？')) return;
  fetch('api/image?code='+encodeURIComponent(code),{method:'DELETE'})
    .then(function(r){return r.json();}).then(function(){ load(); })
    .catch(function(e){ msg('删除失败：'+e.message,'err'); });
}

$('q').oninput=render;
$('filter').onchange=render;
load();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def serve(host: str = "127.0.0.1", port: int = 8011):
    os.makedirs(IMAGE_DIR, exist_ok=True)
    server = QuietServer((host, port), ImageHandler)
    server.serve_forever()


def start_in_thread(host: str = "127.0.0.1", port: int = 8011):
    """供 cpq_suite_server 调用：后台线程里跑，随主进程退出。返回 server（失败返回 None）。"""
    os.makedirs(IMAGE_DIR, exist_ok=True)
    try:
        server = QuietServer((host, port), ImageHandler)
    except OSError as e:
        print(f"[cpq-image] 端口 {port} 启动失败：{e}", file=sys.stderr)
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    ap = argparse.ArgumentParser(description="产品图片维护服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.getenv("CPQ_IMAGE_PORT", "8011")))
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print(f"[cpq-image] 产品图片维护 http://{args.host}:{args.port}/  图片目录 {IMAGE_DIR}")
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
