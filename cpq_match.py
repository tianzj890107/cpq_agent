# -*- coding: utf-8 -*-
"""配置报价CPQ —— 第 1 步「产品匹配」评分引擎（确定性，不交给大模型算）。

从 `product_para_value`（产品参数值表）里按需求打分，输出推荐清单 Top N。
六个维度按固定权重加权，规则严格照业务定义实现；模型只负责把需求解析成结构化参数，
分数与排序全部由本模块算——保证同样的需求永远得到同样的结果、且每一分都可解释。

  维度            字段                    权重   规则
  尺寸合规        max_dimension           25%   完全在容差内=100；超限<10%=60；超限>10%=0(过滤/警告)
  用途/场景契合   application_scope       20%   完全一致或父类=100；交叉场景=70；否则=30
  温度范围覆盖    operating_temperature   20%   完全覆盖需求区间=100；部分覆盖=50；无交集=0(警告)
  寿命满足        service_life            15%   ≥需求=100；≥需求80%=70；否则=0(需征询工程师)
  密封性匹配      hermeticity             15%   IP等级≥需求=100；低一级(可加装防护)=50；否则=0(警告)
  其他            —                       5%    参数完整度（信息越全越可信），可解释的兜底项

排序：加权总分降序；完全同分时按成品编码，保证结果稳定可复现。
（DA 里没有工时字段，也无法从现有数据推算，故不纳入排序与打分。）
"""
from __future__ import annotations

import re

import cpq_db

PRODUCT_TABLE = "product_para_value"
COL_CODE = "product_item_code"
COL_NAME = "product_item_name"

WEIGHTS = {
    "dimension": 0.25,
    "scope": 0.20,
    "temperature": 0.20,
    "life": 0.15,
    "hermeticity": 0.15,
    "other": 0.05,
}
DIM_LABELS = {
    "dimension": "尺寸合规",
    "scope": "用途/场景契合",
    "temperature": "温度范围覆盖",
    "life": "寿命满足",
    "hermeticity": "密封性匹配",
    "other": "其他(参数完整度)",
}
RECOMMEND_THRESHOLD = 70.0     # 最高分低于此值 -> 建议转定制评估
FETCH_LIMIT = 3000


# ---------------------------------------------------------------------------
# 取值解析：DA 里这些字段都是自由文本，解析必须容错
# ---------------------------------------------------------------------------
_NUM = r"-?\d+(?:\.\d+)?"


def _nums(s) -> list:
    """抽出字符串里的所有数字。"""
    return [float(x) for x in re.findall(_NUM, str(s if s is not None else ""))]


def parse_dims(s) -> list:
    """尺寸 -> 降序的三边列表。支持 '100*50*20' / '100×50×20mm' / 'L100 W50 H20' 等。
    不足 3 个数按实际个数返回；取不到返回 []。"""
    v = _nums(s)
    v = [x for x in v if x > 0][:3]
    return sorted(v, reverse=True)


def parse_range(s):
    """温度区间 -> (min, max)。支持 '-20~60' / '-20℃至60℃' / '-20 to 60' / 单值。"""
    v = _nums(s)
    if not v:
        return None
    if len(v) == 1:
        return (v[0], v[0])
    return (min(v), max(v))


def parse_life(s):
    """寿命 -> 数值（次/年，单位由双方一致性保证；只比数值大小）。"""
    v = _nums(s)
    return v[0] if v else None


def parse_ip(s):
    """密封性 -> (防尘等级, 防水等级)。'IP67' -> (6,7)；'IP6K9K' 取首位数字。"""
    m = re.search(r"IP\s*(\d)\s*[Kk]?\s*(\d)", str(s or ""), re.I)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    v = _nums(s)
    if len(v) >= 2:
        return (int(v[0]), int(v[1]))
    return None


def _tokens(s) -> set:
    """场景文本切成词集合（中英混排：按非字母数字切，再补中文 2-gram）。"""
    t = str(s or "").strip().lower()
    if not t:
        return set()
    parts = {p for p in re.split(r"[^0-9a-z一-鿿]+", t) if p}
    out = set()
    for p in parts:
        out.add(p)
        if re.fullmatch(r"[一-鿿]+", p) and len(p) >= 2:
            out |= {p[i:i + 2] for i in range(len(p) - 1)}
    return out


# ---------------------------------------------------------------------------
# 六个维度打分：每个返回 (分数 0~100, 说明, 是否告警)
# ---------------------------------------------------------------------------
def score_dimension(need, prod, tol_pct=0.0):
    """尺寸合规：完全在需求长宽高容差内=100；超限<10%=60；超限>10%=0(过滤/警告)。"""
    nd, pd = parse_dims(need), parse_dims(prod)
    if not nd:
        return 100.0, "需求未给尺寸，不作限制", False
    if not pd:
        return 0.0, "产品未标注尺寸，无法核对", True
    n = min(len(nd), len(pd))
    worst = 0.0          # 最大超限比例
    for i in range(n):
        allow = nd[i] * (1 + max(tol_pct, 0.0) / 100.0)
        if pd[i] > allow and allow > 0:
            worst = max(worst, (pd[i] - allow) / allow * 100.0)
    dims = "×".join(str(int(x) if x == int(x) else x) for x in pd)
    if worst <= 0:
        return 100.0, f"产品 {dims} 完全在需求容差内", False
    if worst < 10:
        return 60.0, f"产品 {dims} 超限 {worst:.1f}%（<10%，可接受）", False
    return 0.0, f"产品 {dims} 超限 {worst:.1f}%（>10%，已过滤/需警告）", True


def score_scope(need, prod):
    """用途/场景契合：完全一致或父类匹配=100；交叉场景=70；否则=30。"""
    n, p = str(need or "").strip(), str(prod or "").strip()
    if not n:
        return 100.0, "需求未限定应用场景", False
    if not p:
        return 30.0, "产品未标注应用范围", True
    a, b = n.lower(), p.lower()
    if a == b:
        return 100.0, f"应用范围完全一致（{p}）", False
    if a in b or b in a:                     # 父类/子类包含
        return 100.0, f"父子类匹配（需求「{n}」↔ 产品「{p}」）", False
    ta, tb = _tokens(n), _tokens(p)
    if ta & tb:
        return 70.0, f"交叉场景（共同点：{'、'.join(sorted(ta & tb)[:3])}）", False
    return 30.0, f"场景不匹配（需求「{n}」vs 产品「{p}」）", True


def score_temperature(need, prod):
    """温度范围覆盖：产品区间完全覆盖需求=100；部分覆盖=50；无交集=0(警告)。"""
    nr, pr = parse_range(need), parse_range(prod)
    if not nr:
        return 100.0, "需求未给工作温度，不作限制", False
    if not pr:
        return 0.0, "产品未标注工作温度，无法核对", True
    (nmin, nmax), (pmin, pmax) = nr, pr
    if pmin <= nmin and pmax >= nmax:
        return 100.0, f"产品 {pmin:g}~{pmax:g}℃ 完全覆盖需求 {nmin:g}~{nmax:g}℃", False
    if pmax < nmin or pmin > nmax:
        return 0.0, f"产品 {pmin:g}~{pmax:g}℃ 与需求 {nmin:g}~{nmax:g}℃ 无交集", True
    return 50.0, (f"产品 {pmin:g}~{pmax:g}℃ 仅部分覆盖需求 {nmin:g}~{nmax:g}℃"
                  f"（另一端不满足，需确认充/放电分段要求）"), True


def score_life(need, prod):
    """寿命满足：≥需求=100；≥需求80%=70；否则=0(需征询工程师)。"""
    nv, pv = parse_life(need), parse_life(prod)
    if nv is None:
        return 100.0, "需求未给寿命要求", False
    if pv is None:
        return 0.0, "产品未标注寿命，需征询工程师", True
    if pv >= nv:
        return 100.0, f"产品寿命 {pv:g} ≥ 需求 {nv:g}", False
    ratio = pv / nv if nv else 0
    if ratio >= 0.8:
        return 70.0, f"产品寿命 {pv:g} 为需求 {nv:g} 的 {ratio * 100:.0f}%（≥80%）", False
    return 0.0, f"产品寿命 {pv:g} 仅为需求 {nv:g} 的 {ratio * 100:.0f}%，需征询工程师", True


def score_hermeticity(need, prod):
    """密封性匹配：IP 等级完全一致或更高=100；低一级(可加装防护)=50；否则=0(警告)。"""
    ni, pi = parse_ip(need), parse_ip(prod)
    if not ni:
        return 100.0, "需求未限定密封等级", False
    if not pi:
        return 0.0, "产品未标注密封等级", True
    fmt = lambda t: f"IP{t[0]}{t[1]}"  # noqa: E731
    if pi[0] >= ni[0] and pi[1] >= ni[1]:
        eq = "完全一致" if pi == ni else "高于需求"
        return 100.0, f"产品 {fmt(pi)} {eq}（需求 {fmt(ni)}）", False
    gap = max(ni[0] - pi[0], ni[1] - pi[1])
    if gap == 1:
        return 50.0, f"产品 {fmt(pi)} 低需求 {fmt(ni)} 一级，可加装防护处理", False
    return 0.0, f"产品 {fmt(pi)} 低于需求 {fmt(ni)} {gap} 级，不满足", True


_INFO_COLS = ("max_dimension", "application_scope", "operating_temperature",
              "service_life", "hermeticity", "rated_voltage", "rated_capacity",
              "max_continuous_current", "weight")


def score_other(row: dict):
    """其他 5%：参数完整度——关键参数越齐全，报价与选型越可信。"""
    have = [c for c in _INFO_COLS if str(row.get(c) or "").strip()]
    pct = len(have) / len(_INFO_COLS) * 100.0
    return round(pct, 1), f"关键参数完整度 {len(have)}/{len(_INFO_COLS)}", False


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def score_row(req: dict, row: dict) -> dict:
    """给单个产品打六维分，返回明细 + 加权总分。"""
    tol = req.get("dimension_tolerance_pct") or 0.0
    try:
        tol = float(tol)
    except (TypeError, ValueError):
        tol = 0.0

    parts = {
        "dimension": score_dimension(req.get("max_dimension"), row.get("max_dimension"), tol),
        "scope": score_scope(req.get("application_scope"), row.get("application_scope")),
        "temperature": score_temperature(req.get("operating_temperature"), row.get("operating_temperature")),
        "life": score_life(req.get("service_life"), row.get("service_life")),
        "hermeticity": score_hermeticity(req.get("hermeticity"), row.get("hermeticity")),
        "other": score_other(row),
    }
    detail, warnings, total = {}, [], 0.0
    for key, (sc, why, warn) in parts.items():
        detail[key] = {"label": DIM_LABELS[key], "score": round(sc, 1),
                       "weight": int(WEIGHTS[key] * 100), "reason": why,
                       "weighted": round(sc * WEIGHTS[key], 2)}
        total += sc * WEIGHTS[key]
        if warn:
            warnings.append(f"{DIM_LABELS[key]}：{why}")
    return {"detail": detail, "total": round(total, 1), "warnings": warnings}


def match(req: dict, top_n: int = 3, drop_oversize: bool = True) -> dict:
    """按需求给出推荐清单。

    req —— 需求参数（max_dimension / application_scope / operating_temperature /
           service_life / hermeticity / dimension_tolerance_pct）
    返回 {ok, products(TopN), all_count, threshold, below_threshold, advice, error}
    """
    try:
        cols, rows = cpq_db.run_select(f"SELECT * FROM {PRODUCT_TABLE}", FETCH_LIMIT)
    except Exception as e:
        return {"ok": False, "error": f"读取 {PRODUCT_TABLE} 失败：{str(e).splitlines()[0][:160]}",
                "products": [], "all_count": 0}

    idx = {c: i for i, c in enumerate(cols)}
    scored = []
    for r in rows[:FETCH_LIMIT]:
        row = {c: ("" if idx.get(c) is None or r[idx[c]] is None else str(r[idx[c]]))
               for c in cols}
        code = row.get(COL_CODE, "")
        if not code:
            continue
        s = score_row(req, row)
        # 尺寸超限 >10% 按业务规则直接过滤（仍保留在 filtered 里备查）
        oversize = s["detail"]["dimension"]["score"] == 0 and str(row.get("max_dimension") or "").strip()
        item = {
            "code": code,
            "name": row.get(COL_NAME, ""),
            "total": s["total"],
            "detail": s["detail"],
            "warnings": s["warnings"],
            "params": {c: row.get(c, "") for c in _INFO_COLS if row.get(c)},
            "oversize": bool(oversize),
        }
        scored.append(item)

    pool = [x for x in scored if not (drop_oversize and x["oversize"])]
    if not pool:                      # 全被尺寸过滤：退回全量并标注，别让用户看到空清单
        pool = scored

    # 排序：总分降序；完全同分时按编码，保证同样输入永远同样输出
    pool.sort(key=lambda x: (-x["total"], x["code"]))
    top = pool[:max(1, int(top_n or 3))]
    best = top[0]["total"] if top else 0.0
    below = best < RECOMMEND_THRESHOLD
    return {
        "ok": True,
        "products": top,
        "all_count": len(scored),
        "pool_count": len(pool),
        "threshold": RECOMMEND_THRESHOLD,
        "below_threshold": below,
        "advice": ("无高度匹配标品，建议转入定制评估。"
                   if below else ""),
        "weights": {DIM_LABELS[k]: f"{int(v * 100)}%" for k, v in WEIGHTS.items()},
    }
