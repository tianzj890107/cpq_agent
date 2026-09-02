"""
回调 CPQ 一体化服务，完成两件需要业务库的事：写主数据、推送到报价。

服务端实现见 配置报价CPQ/cpq_tech_bridge.py，路由是 /wf/tech/*。这里只做 HTTP 客户端。

为什么不直接连库：业务库连接、雪花主键、DA 类型清洗、报价工作流全都在一体化服务
那一侧（cpq_db / cpq_wf）。tech_app 直连意味着要引 psycopg、复制一份编码规则、
再维护第二条写库路径 —— 与登录（cpq_sso）一样，让一体化服务当唯一出口。

调用方带的是**用户自己的 CPQ 令牌**：写主数据和推送报价都要留痕到具体的人，
不能用服务账号顶替。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from ..config import CPQ_AUTH_BASE_URL, CPQ_AUTH_TIMEOUT_SECONDS

# 写库和推送都可能慢（远程 PG + 多次插入），比验票的 5 秒宽一些。
_TIMEOUT = max(20.0, CPQ_AUTH_TIMEOUT_SECONDS * 4)


class BridgeUnavailable(RuntimeError):
    """连不上一体化服务，或它连不上业务库。"""


class BridgeRejected(RuntimeError):
    """业务侧明确拒绝（权限、数据不合法…）。文案可直接给用户看。"""


def _post(path: str, token: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{CPQ_AUTH_BASE_URL}{path}", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            message = (json.loads(raw) or {}).get("error") or raw
        except json.JSONDecodeError:
            message = raw
        # 400/401/403 是业务判定，原样回给用户；5xx/503 是服务本身的问题。
        if exc.code in (400, 401, 403, 409):
            raise BridgeRejected(str(message)[:400]) from exc
        raise BridgeUnavailable(f"CPQ 服务返回 {exc.code}：{str(message)[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeUnavailable(f"连不上 CPQ 服务（{CPQ_AUTH_BASE_URL}）：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise BridgeUnavailable("CPQ 服务返回的不是 JSON") from exc


def write_material(token: str, product_name: str, unit_price: float,
                   breakdown: Optional[dict] = None, spec: str = "") -> dict:
    """新建成品编码，并把成品与成本写进 md_clm_material_base_info / md_clm_material_cost_cnf。"""
    return _post("/wf/tech/material", token, {
        "product_name": product_name,
        "unit_price": unit_price,
        "breakdown": breakdown or {},
        "spec": spec,
    })


def send_to_quote(token: str, session_id: str, title: str, customer: str = "",
                  project_name: str = "", note: str = "",
                  source_task_id: str = "", result: Optional[dict] = None) -> dict:
    """确认工艺（报价第 2 步）并把卡片推进到第 3 步定价，通知销售经理。

    source_task_id 是当初那条「新增工艺」任务：给了它，一体化服务会回到**原来那张
    报价卡片**、把任务退回给当初发起的那个人，而不是新开一张卡片群发给销售角色。
    result 是随任务带回去的整机结论（成品编码、四项成本、按 DA 字段拉平的参数）。
    """
    return _post("/wf/tech/handoff", token, {
        "session_id": session_id,
        "title": title,
        "customer": customer,
        "project_name": project_name,
        "note": note,
        "source_task_id": source_task_id,
        "result": result or {},
    })
