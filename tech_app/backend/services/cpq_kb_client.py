"""技术工艺取知识库（`kb_*`）的 HTTP 客户端 —— 知识库只有一份，在 CPQ 的 `cpq_kb`。

技术工艺**不直连 Postgres**（见 docs/specs/kb-in-pg-http-snapshot.md C8）。本模块是
那条通道的唯一入口：从配置报价一体化服务（8010）的 `GET /wf/tech/kb/snapshot` 拉整包
快照，交给 storage/kb_repo.py 缓存与检索。

契约（C4 / C5）：

  · 鉴权只认服务间内部令牌 `X-Internal-Token`（env 里的 `CPQ_INTERNAL_TOKEN`）——
    后台刷新没有用户票，快照接口也不该开给外部用户；
  · 缺令牌 / 连不上 / 非 200 / 响应不是 JSON / `ok` 不为真 → 一律抛 `KbUnavailable`，
    **绝不静默降级成"空知识库"**：那会把"桥断了"伪装成"库里没有可复用零件"，
    正是本批要消灭的假象；
  · `since=<kb_version>` 命中时服务端只回 `unchanged=true`（不带表数据），调用方据此
    沿用本地缓存。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from ..config import CPQ_KB_BASE_URL, CPQ_KB_TIMEOUT_SECONDS

INTERNAL_TOKEN_HEADER = "X-Internal-Token"
SNAPSHOT_PATH = "/wf/tech/kb/snapshot"


class KbUnavailable(RuntimeError):
    """知识库快照拿不到（未配置令牌 / 连不上 / 非 200 / 响应不可解析）。"""


def _base() -> str:
    return (CPQ_KB_BASE_URL or "").rstrip("/")


def _internal_token() -> str:
    return (os.getenv("CPQ_INTERNAL_TOKEN") or "").strip()


def fetch_snapshot(since=None) -> dict:
    """拉整包快照：`{"kb_version", "unchanged", "tables"}`。

    `since` 传本地缓存的版本号；服务端认为没变时回 `unchanged=True` 且 `tables` 为空。
    任何失败都抛 `KbUnavailable`（异常里带可读原因）。
    """
    token = _internal_token()
    if not token:
        raise KbUnavailable(
            "缺少服务间内部令牌 CPQ_INTERNAL_TOKEN，无法读取知识库快照"
            "（技术工艺不直连 Postgres）")
    base = _base()
    if not base:
        raise KbUnavailable("未配置 CPQ_AUTH_BASE_URL，无法定位知识库快照接口")
    url = base + SNAPSHOT_PATH
    if since is not None and str(since).strip() != "":
        url += "?" + urllib.parse.urlencode({"since": str(since)})
    request = urllib.request.Request(url, method="GET", headers={
        INTERNAL_TOKEN_HEADER: token,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=CPQ_KB_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 0) or 0)
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:                                    # pragma: no cover - 读不到就算了
            detail = ""
        raise KbUnavailable(
            "知识库快照接口返回 HTTP %s：%s" % (exc.code, detail or exc.reason)) from exc
    except Exception as exc:                                 # noqa: BLE001 - 连接/超时/SSL
        raise KbUnavailable(
            "无法访问知识库快照接口 %s：%s: %s" % (url, type(exc).__name__, exc)) from exc
    if status != 200:
        raise KbUnavailable("知识库快照接口返回 HTTP %s" % status)
    try:
        payload = json.loads(raw or "{}")
    except ValueError as exc:
        raise KbUnavailable("知识库快照接口返回的不是 JSON：%s" % raw[:200]) from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = (payload or {}).get("error") if isinstance(payload, dict) else ""
        raise KbUnavailable("知识库快照不可用：%s" % (error or raw[:200]))
    return payload
