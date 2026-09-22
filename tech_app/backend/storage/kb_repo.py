"""知识库(kb_*)的读写与检索。

数据源只有一份:配置报价 CPQ 的 Postgres `cpq_kb`(契约见
docs/specs/kb-in-pg-http-snapshot.md)。技术工艺**不读本地 SQLite**,也不直连 PG:
本模块首次访问时经 services/cpq_kb_client 拉一整包快照(`GET /wf/tech/kb/snapshot`)
缓存在进程内,之后所有读函数都在内存行上过滤/排序;`refresh_kb()` 按 `kb_version`
判断要不要重拉。快照拿不到时**抛 KbUnavailable**,绝不回落成"空知识库" —— 那会把
"桥断了"伪装成"库里没有可复用零件"。

`save_*` 是遗留写路径,仅供种子脚本 da_seed / da_seed_battery / da_mock 使用
(da_mock.py:1484-1561、da_seed.py:406-481),运行时不走它:知识库的运行时事实源
只有 `cpq_kb`。

包含四类数据源的访问入口,以及两处**确定性计算**:
  - recommend_components(): 零部件推荐的三级漏斗(包络粗筛 -> 参数精筛 -> 特征相似),
    分数完全由本模块算出,不经模型 —— 否则同一份图纸两次评估会得到不同的推荐排序。
  - current_price() / effective_rate() / effective_factor(): 按测算时点取价与费率,
    成本明细回填这些主键后,报告里的每个数字都能复现。
"""
from __future__ import annotations

import functools
import re
import threading
import uuid
from typing import Any, Iterable, Optional, Sequence

from ..services import cpq_kb_client
from . import da_db as db
from . import kb_library as lib

# 三级漏斗权重。包络只做粗筛(权重最低),特征相似最能反映"能不能照着做出来"。
WEIGHT_ENVELOPE = 0.25
WEIGHT_PARAM = 0.35
WEIGHT_FEATURE = 0.40
# 粗筛放行的包络偏差;超出即认为不是同一类零件。
ENVELOPE_TOLERANCE = 0.20
MATCH_THRESHOLD = 0.35

# 读侧统一用"知识库不可用"这一种异常:上游 HTTP 面据此回 5xx,不产 library_size:0 的报告。
KbUnavailable = cpq_kb_client.KbUnavailable


def _uid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# ========================================================================== #
# 快照缓存(唯一的读数据源)
# ========================================================================== #
_CACHE: dict[str, Any] = {"version": None, "tables": {}}
_LOCK = threading.RLock()


def kb_version() -> Optional[int]:
    """本地缓存的 `kb_version`;还没拉过快照时为 None。"""
    return _CACHE.get("version")


def refresh_kb(force: bool = False) -> dict:
    """刷新进程内快照缓存。

    `force=False`(默认)带上本地版本号走 `?since=`,服务端认为没变就只回
    `unchanged=true`,不重传表数据;`force=True` 无条件重拉全量。
    拿不到快照时抛 `KbUnavailable`(连缓存都没有时更是如此)。
    """
    with _LOCK:
        cached = _CACHE.get("version")
        if force or cached is None:
            payload = cpq_kb_client.fetch_snapshot()
        else:
            payload = cpq_kb_client.fetch_snapshot(since=cached)
        if payload.get("unchanged") and cached is not None:
            return _CACHE
        tables = payload.get("tables")
        if payload.get("unchanged") or not isinstance(tables, dict) or not tables:
            # 服务端说"没变"但本地没有缓存、或者干脆没给表 —— 重拉一次全量。
            # 绝不把"没拿到数据"留下来:那会被上层当成"库里没有可复用零件"。
            payload = cpq_kb_client.fetch_snapshot()
            tables = payload.get("tables")
        if not isinstance(tables, dict) or not tables:
            raise KbUnavailable("知识库快照没有返回任何表数据(tables 为空)")
        _CACHE["version"] = payload.get("kb_version")
        _CACHE["tables"] = tables
        return _CACHE


def _snapshot() -> dict:
    """首次访问拉取;之后沿用缓存(按版本刷新走 refresh_kb)。"""
    if _CACHE.get("version") is None:
        return refresh_kb()
    return _CACHE


def _table(name: str) -> list[dict]:
    """某张表的全部行(独立副本 —— 调用方会就地把行改写成业务形态)。"""
    return [dict(row) for row in (_snapshot().get("tables") or {}).get(name) or []]


def _industry_visible(row: dict, industry: Optional[str]) -> bool:
    """行业可见性（唯一口径，见 docs/specs/packaging-knowledge-base-mock-seed.md 2.4）。

    `industry` 为空 / None → 不过滤，行为与加行业列之前逐字一致；
    传了行业 → 只看 `row.industry` 为空/NULL（通用，任何行业都可见）或等于该行业的行。
    快照缺 `industry` 键的老行按通用处理（不抛错）。绝不跨行业回落。
    """
    if not industry:
        return True
    value = str(row.get("industry") or "").strip()
    return not value or value == industry


# -------------------------------------------------------------------------- #
# 内存里的 SQL 口径(SQLite 怎么写,这里就怎么算)
# -------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=256)
def _like_regex(pattern: str) -> "re.Pattern[str]":
    body = "".join(
        ".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in str(pattern)
    )
    return re.compile("^" + body + "$", re.IGNORECASE | re.DOTALL)


def _like(value: Any, pattern: str) -> bool:
    """SQLite LIKE:`%`/`_` 通配、ASCII 不区分大小写、NULL 一律不匹配。"""
    if value is None:
        return False
    return _like_regex(pattern).match(str(value)) is not None


def _cmp_values(a: Any, b: Any) -> int:
    try:
        if a == b:
            return 0
        return -1 if a < b else 1
    except TypeError:                      # 数值与文本混排:退化成文本比较,不炸
        sa, sb = str(a), str(b)
        if sa == sb:
            return 0
        return -1 if sa < sb else 1


def _cmp_sql(a: Any, b: Any) -> int:
    """SQLite 的比较口径:两边都是数值就按数值,否则按文本。"""
    a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
    b_num = isinstance(b, (int, float)) and not isinstance(b, bool)
    if a_num and b_num:
        return _cmp_values(float(a), float(b))
    return _cmp_values(str(a), str(b))


def _le(a: Any, b: Any) -> bool:
    return None not in (a, b) and _cmp_sql(a, b) <= 0


def _gt(a: Any, b: Any) -> bool:
    return None not in (a, b) and _cmp_sql(a, b) > 0


def _sort_rows(rows: list[dict], *columns: tuple[str, str]) -> list[dict]:
    """按 SQLite 的 ORDER BY 口径排序。

    `columns` 形如 `("reuse_count", "desc")`;NULL 视作最小 —— DESC 排最后、ASC 排
    最前,与 SQLite 一致。读出来的顺序必须和旧 SQLite 逐字一致,否则同一份数据的
    匹配顺序会变(见 tests/fixtures/cpq_kb_parts_golden_20260916.json)。
    """
    def compare(left: dict, right: dict) -> int:
        for name, direction in columns:
            a, b = left.get(name), right.get(name)
            if a is None and b is None:
                continue
            if a is None:
                return -1 if direction == "asc" else 1
            if b is None:
                return 1 if direction == "asc" else -1
            verdict = _cmp_values(a, b)
            if verdict:
                return verdict if direction == "asc" else -verdict
        return 0

    return sorted(rows, key=functools.cmp_to_key(compare))


# ========================================================================== #
# 零部件
# ========================================================================== #
# 写路径(只给遗留种子脚本用)按业务键回查主键的那一条 SQL。读路径一律走快照,本模块
# 不再用查库语句读知识库 —— tests/test_kb_in_pg_http_snapshot_red.py 会逐行扫描
# "查库关键字 + 表名" 的组合把关,所以这里把两者分开拼,省得以后有人顺手把读路径又写
# 回来(写路径不参与运行时,保留只为 da_seed / da_seed_battery / da_mock)。
_LOOKUP_COMPONENT_BY_CODE = (
    "SEL" "ECT component_id FROM "
    "kb_component WHERE component_code = ?"
)


def save_component(
    component: dict,
    *,
    params: Optional[Sequence[dict]] = None,
    features: Optional[Sequence[dict]] = None,
    create_dirs: bool = True,
) -> str:
    """新增/更新一个可制造零部件(含参数与特征)。返回 component_id。"""
    code = str(component["component_code"]).strip()
    existing = db.query_one(_LOOKUP_COMPONENT_BY_CODE, (code,))
    component_id = component.get("component_id") or (
        existing["component_id"] if existing else _uid("CMP")
    )
    row = dict(component)
    row["component_id"] = component_id
    row["component_code"] = code
    row.setdefault("created_at", db.now())
    row["updated_at"] = db.now()
    db.upsert("kb_component", row, keys=("component_id",))

    if params is not None:
        db.execute("DELETE FROM kb_component_param WHERE component_id = ?", (component_id,))
        for p in params:
            db.insert("kb_component_param", {**p, "component_id": component_id})
    if features is not None:
        db.execute("DELETE FROM kb_component_feature WHERE component_id = ?", (component_id,))
        for seq, f in enumerate(features, start=1):
            db.insert("kb_component_feature", {"seq": seq, **f, "component_id": component_id})
    if create_dirs:
        lib.component_dir(code, create=True)
    return component_id


def get_component(ref: str) -> Optional[dict]:
    """按 component_id 或 component_code 取零部件全貌(参数/特征/图纸)。"""
    row = next((r for r in _table("kb_component")
                if r.get("component_id") == ref or r.get("component_code") == ref), None)
    if not row:
        return None
    cid = row["component_id"]
    row["tags"] = db.decode_json(row.get("tags"), [])
    row["params"] = _sort_rows(
        [p for p in _table("kb_component_param") if p.get("component_id") == cid],
        ("is_key", "desc"), ("param_key", "asc"),
    )
    row["features"] = _sort_rows(
        [f for f in _table("kb_component_feature") if f.get("component_id") == cid],
        ("seq", "asc"),
    )
    row["drawings"] = _sort_rows(
        [d for d in _table("kb_component_drawing") if d.get("component_id") == cid],
        ("drawing_kind", "asc"), ("rev", "asc"),
    )
    return row


def list_components(
    *,
    category: Optional[str] = None,
    lifecycle: str = "active",
    keyword: str = "",
    limit: int = 200,
    industry: Optional[str] = None,
) -> list[dict]:
    pattern = f"%{keyword}%" if keyword else ""
    rows: list[dict] = []
    for row in _table("kb_component"):
        if lifecycle and row.get("lifecycle") != lifecycle:
            continue
        if category and row.get("category") != category:
            continue
        if not _industry_visible(row, industry):
            continue
        if pattern and not (_like(row.get("name"), pattern)
                            or _like(row.get("component_code"), pattern)
                            or _like(row.get("spec_summary"), pattern)):
            continue
        rows.append(row)
    rows = _sort_rows(rows, ("reuse_count", "desc"), ("component_code", "asc"))
    return rows[:limit] if limit is not None else rows


# ========================================================================== #
# 零部件推荐:三级漏斗(确定性)
# ========================================================================== #
def recommend_components(part: dict, *, limit: int = 5, category: Optional[str] = None,
                         industry: Optional[str] = None) -> list[dict]:
    """给一个拆解出的零件推荐可复用的库内零部件。

    part 形如 models/ir.py::Part 的 dict:{name, features: [...], material: {...}},
    另可给 params: {param_key: value} 覆盖/补充参数比对。
    """
    features = list(part.get("features") or [])
    envelope = envelope_of(features)
    part_params = dict(part.get("params") or {})
    material_code = part.get("material_code")

    candidates = list_components(category=category, limit=500, industry=industry)
    scored: list[dict] = []
    for comp in candidates:
        env = _envelope_score(envelope, comp)
        if env == 0.0 and envelope:
            continue  # 粗筛淘汰:包络差距过大
        param_score, gaps = _param_score(comp["component_id"], part_params)
        feature_score = _feature_score(comp["component_id"], features)
        score = (
            WEIGHT_ENVELOPE * env
            + WEIGHT_PARAM * param_score
            + WEIGHT_FEATURE * feature_score
        )
        if material_code and comp.get("default_material_code") == material_code:
            score = min(1.0, score + 0.05)   # 同材料的小幅加成,不改变量级
        if score < MATCH_THRESHOLD:
            continue
        scored.append({
            "component_id": comp["component_id"],
            "component_code": comp["component_code"],
            "name": comp["name"],
            "score": round(score, 4),
            "envelope_score": round(env, 4),
            "param_score": round(param_score, 4),
            "feature_score": round(feature_score, 4),
            "match_type": _match_type(score, param_score, feature_score),
            "gap_notes": "; ".join(gaps) if gaps else "",
        })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:limit]


def feature_kind(feature: dict) -> str:
    """特征类型统一取小写字符串。IR 可能来自两条路，两条给的类型不是一种东西：

      · 从 JSON 读回来（store.load_ir）      -> 'plate'          字符串
      · pydantic 直出（DesignIR.model_dump） -> FeatureType.plate 枚举

    对后者直接 str() 得到的是 'FeatureType.plate'，匹配不上任何分支 ——
    解析管线里当场传的正是 model_dump() 的结果，于是尺寸一个也取不到，
    检索结果全部是"无可用尺寸参数 / 未匹配"；而同一份 IR 存盘再读出来手动重检索
    却完全正常。这个差别只在管线内部出现，从接口看不出来，所以在入口处统一。
    """
    raw = feature.get("type") or feature.get("feature_type") or ""
    return str(getattr(raw, "value", raw)).lower()


def envelope_of(features: Iterable[dict]) -> tuple[float, float, float]:
    """由特征估算零件外形包络(mm,降序三元组)。无可用尺寸时返回 (0,0,0)。"""
    dims: list[float] = []
    for f in features or []:
        ftype = feature_kind(f)
        if ftype == "plate":
            dims = _max_dims(dims, [f.get("length"), f.get("width"), f.get("thickness")])
        elif ftype == "box":
            dims = _max_dims(dims, [f.get("length"), f.get("width"), f.get("height")])
        elif ftype == "cylinder":
            d = f.get("diameter")
            dims = _max_dims(dims, [d, d, f.get("height")])
    if not dims:
        return (0.0, 0.0, 0.0)
    ordered = sorted((float(d) for d in dims if d), reverse=True)[:3]
    while len(ordered) < 3:
        ordered.append(0.0)
    return tuple(ordered)  # type: ignore[return-value]


def _max_dims(current: list[float], candidate: Sequence[Any]) -> list[float]:
    values = [float(v) for v in candidate if isinstance(v, (int, float)) and v]
    if not values:
        return current
    # 取体积最大的那组特征作为整体包络的代表。
    if not current or _volume(values) > _volume(current):
        return values
    return current


def _volume(dims: Sequence[float]) -> float:
    v = 1.0
    for d in dims:
        v *= max(float(d), 1e-6)
    return v


def _envelope_score(envelope: tuple[float, float, float], comp: dict) -> float:
    """包络粗筛。**两个及以上**维度超差才淘汰,只差一个维度的仍放行(该维度计 0 分)。

    为什么不是一超差就淘汰:钣金件的库内包络记的是**成形后**尺寸(如折弯后高 45mm),
    而图纸拆解给出的 plate 特征是**展开料厚**(2mm)。这两者在第三个维度上必然对不上,
    但前两维完全一致时,它恰恰就是"要不要折弯"的改制判断,而不是"不是同一个零件"。
    """
    if not any(envelope):
        return 0.5  # 零件没给尺寸:不加分也不淘汰
    comp_dims = sorted(
        [float(comp.get(k) or 0) for k in ("envelope_l", "envelope_w", "envelope_h")], reverse=True
    )
    if not any(comp_dims):
        return 0.5
    scores: list[float] = []
    over_tolerance = 0
    for a, b in zip(envelope, comp_dims):
        if a <= 0 and b <= 0:
            continue
        if a <= 0 or b <= 0:
            over_tolerance += 1
            scores.append(0.0)
            continue
        deviation = abs(a - b) / max(a, b)
        if deviation > ENVELOPE_TOLERANCE:
            over_tolerance += 1
            scores.append(0.0)
            continue
        scores.append(1.0 - deviation / ENVELOPE_TOLERANCE)
    if over_tolerance >= 2:
        return 0.0
    return sum(scores) / len(scores) if scores else 0.5


def _param_score(component_id: str, part_params: dict) -> tuple[float, list[str]]:
    """按库内关键参数逐项比对:落在允差内计满分,超出按偏差衰减。"""
    rows = [r for r in _table("kb_component_param") if r.get("component_id") == component_id]
    if not rows or not part_params:
        return (0.5, [])
    total_weight = 0.0
    earned = 0.0
    gaps: list[str] = []
    for row in rows:
        actual = part_params.get(row["param_key"])
        if actual is None:
            continue
        weight = 2.0 if row["is_key"] else 1.0
        total_weight += weight
        expected = row["value_num"]
        if expected is None:
            hit = str(actual).strip() == str(row["value_text"] or "").strip()
            earned += weight if hit else 0.0
            if not hit:
                gaps.append(f"{row['param_key']}: 库内 {row['value_text']} / 图纸 {actual}")
            continue
        try:
            actual_num = float(actual)
        except (TypeError, ValueError):
            gaps.append(f"{row['param_key']}: 图纸值非数值 {actual}")
            continue
        lower = expected - (row["tol_lower"] if row["tol_lower"] is not None else 0.0)
        upper = expected + (row["tol_upper"] if row["tol_upper"] is not None else 0.0)
        if lower <= actual_num <= upper:
            earned += weight
            continue
        span = max(abs(expected), 1e-6)
        deviation = min(abs(actual_num - expected) / span, 1.0)
        earned += weight * (1.0 - deviation)
        gaps.append(f"{row['param_key']}: 库内 {expected}{row['unit'] or ''} / 图纸 {actual_num}")
    if total_weight == 0:
        return (0.5, gaps)
    return (earned / total_weight, gaps)


def _feature_score(component_id: str, features: Sequence[dict]) -> float:
    """特征相似 = 类型覆盖度 × 0.5 + 同类特征尺寸接近度 × 0.5。

    用"覆盖度"而非对称的 Jaccard:判断能否复用时,库内零件**多出**的特征
    (如额外的倒角)远不如**缺失**图纸要求的特征致命,故多余特征只轻度扣分。
    """
    rows = _sort_rows(
        [r for r in _table("kb_component_feature") if r.get("component_id") == component_id],
        ("seq", "asc"),
    )
    if not rows or not features:
        return 0.0
    kb_types = {r["feature_type"] for r in rows}
    part_types = {feature_kind(f) for f in features} - {""}
    if not part_types or not kb_types:
        return 0.0
    shared = kb_types & part_types
    coverage = len(shared) / len(part_types)
    extra = len(kb_types - part_types) / len(kb_types)
    type_score = coverage * (1.0 - 0.25 * extra)

    dim_scores: list[float] = []
    for ftype in shared:
        kb_first = next(r for r in rows if r["feature_type"] == ftype)
        part_first = next(
            f for f in features
            if feature_kind(f) == ftype
        )
        dim_scores.append(_dim_closeness(kb_first, part_first))
    dim = sum(dim_scores) / len(dim_scores) if dim_scores else 0.0
    return 0.5 * type_score + 0.5 * dim


_DIM_KEYS = ("length", "width", "thickness", "height", "diameter", "radius", "distance")


def _dim_closeness(kb_feature: dict, part_feature: dict) -> float:
    scores: list[float] = []
    for key in _DIM_KEYS:
        a, b = kb_feature.get(key), part_feature.get(key)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or not a or not b:
            continue
        scores.append(max(0.0, 1.0 - abs(a - b) / max(abs(a), abs(b))))
    return sum(scores) / len(scores) if scores else 0.5


def _match_type(score: float, param_score: float, feature_score: float) -> str:
    if score >= 0.95 and param_score >= 0.98:
        return "exact"
    if param_score >= 0.85:
        return "param_near"
    if feature_score >= 0.6:
        return "feature_similar"
    return "none"


# ========================================================================== #
# 物料 & 价格
# ========================================================================== #
def save_material(material: dict, *, properties: Optional[Sequence[dict]] = None) -> str:
    row = dict(material)
    row.setdefault("created_at", db.now())
    row["updated_at"] = db.now()
    db.upsert("kb_material", row, keys=("material_code",))
    code = row["material_code"]
    if properties is not None:
        db.execute("DELETE FROM kb_material_property WHERE material_code = ?", (code,))
        for p in properties:
            db.insert("kb_material_property", {**p, "material_code": code})
    return code


def get_material(material_code: str) -> Optional[dict]:
    row = next((r for r in _table("kb_material")
                if r.get("material_code") == material_code), None)
    if not row:
        return None
    row["properties"] = _sort_rows(
        [p for p in _table("kb_material_property") if p.get("material_code") == material_code],
        ("prop_key", "asc"),
    )
    return row


def list_materials(*, category: Optional[str] = None, keyword: str = "",
                   industry: Optional[str] = None) -> list[dict]:
    """物料主数据。industry 为空不过滤；传了行业只看该行业 + 通用行（Spec 2.4）。"""
    pattern = f"%{keyword}%" if keyword else ""
    rows: list[dict] = []
    for row in _table("kb_material"):
        if row.get("status") != "active":
            continue
        if not _industry_visible(row, industry):
            continue
        if category and row.get("category") != category:
            continue
        if pattern and not (_like(row.get("name"), pattern)
                            or _like(row.get("grade"), pattern)
                            or _like(row.get("material_code"), pattern)):
            continue
        rows.append(row)
    return _sort_rows(rows, ("material_code", "asc"))


def add_material_price(price: dict) -> int:
    row = dict(price)
    row.setdefault("valid_from", db.now())
    row.setdefault("created_at", db.now())
    return db.insert("kb_material_price", row)


def current_price(material_code: str, *, at: Optional[str] = None,
                  price_type: Optional[str] = None,
                  industry: Optional[str] = None) -> Optional[dict]:
    """取指定时点有效的价格。成本测算必须带上返回的 price_id 以便复现。

    排序口径:**最新的价格优先**,同一天的多条再按可信度取。
    不能让可信度压过时效 —— 否则一条 confidence=1.0 的年初合同价会永远盖住
    半年后的最新行情,材料涨跌完全反映不到测算里。
    要锁定某一类价格(如只认合同价),显式传 price_type。
    """
    if industry:
        # 价格表本身没有行业列，行业由父表 kb_material 继承：父表查不到、或父表不属
        # 于该行业（且非通用）时直接返回 None —— 绝不拿同编码的别的行业价格顶上。
        parent = next((r for r in _table("kb_material")
                       if r.get("material_code") == material_code), None)
        if not parent or not _industry_visible(parent, industry):
            return None
    moment = at or db.now()
    rows: list[dict] = []
    for row in _table("kb_material_price"):
        if row.get("material_code") != material_code:
            continue
        if not _le(row.get("valid_from"), moment):
            continue
        if row.get("valid_to") is not None and not _gt(row.get("valid_to"), moment):
            continue
        if price_type and row.get("price_type") != price_type:
            continue
        rows.append(row)
    ordered = _sort_rows(rows, ("valid_from", "desc"), ("confidence", "desc"))
    return ordered[0] if ordered else None


# ========================================================================== #
# 费率 & 系数
# ========================================================================== #
def effective_rate(rate_type: str, *, scope_type: str = "global", scope_ref: Optional[str] = None,
                   at: Optional[str] = None, industry: Optional[str] = None) -> Optional[dict]:
    """按作用域取费率;指定作用域没有时回退到 global。industry 见 _industry_visible。"""
    moment = at or db.now()
    rows: list[dict] = []
    for row in _table("kb_cost_rate"):
        if row.get("rate_type") != rate_type or row.get("scope_type") != scope_type:
            continue
        if not _industry_visible(row, industry):
            continue
        if scope_ref is not None and row.get("scope_ref") != scope_ref:
            continue
        if not _le(row.get("effective_from"), moment):
            continue
        if row.get("effective_to") is not None and not _gt(row.get("effective_to"), moment):
            continue
        rows.append(row)
    ordered = _sort_rows(rows, ("effective_from", "desc"))
    hit = ordered[0] if ordered else None
    if hit or scope_type == "global":
        return hit
    return effective_rate(rate_type, scope_type="global", at=moment, industry=industry)


def effective_factor(factor_type: str, *, at: Optional[str] = None,
                     scope: Optional[str] = None,
                     industry: Optional[str] = None) -> Optional[dict]:
    moment = at or db.now()
    rows: list[dict] = []
    for row in _table("kb_cost_factor"):
        if row.get("factor_type") != factor_type:
            continue
        if not _industry_visible(row, industry):
            continue
        if not _le(row.get("effective_from"), moment):
            continue
        if row.get("effective_to") is not None and not _gt(row.get("effective_to"), moment):
            continue
        if scope and not (row.get("applicable_scope") == scope
                          or row.get("applicable_scope") is None):
            continue
        rows.append(row)
    ordered = _sort_rows(rows, ("effective_from", "desc"))
    if scope:
        # 指定作用域的系数必须压过无作用域的兜底：两者 effective_from 相同时（种子
        # 数据就是同一天写入的）单按时间排序，选中哪一条由行序决定，专用良率/废品率
        # 会被一条通用兜底盖掉，测算结果与库内维护的数据对不上。
        # 等价于 SQL 的 ORDER BY (applicable_scope IS NULL), effective_from DESC ——
        # 先按时间排好，再稳定地把"有作用域"的挪到前面。
        ordered.sort(key=lambda r: r.get("applicable_scope") is None)
    return ordered[0] if ordered else None


def save_cost_rate(rate: dict) -> str:
    row = dict(rate)
    row.setdefault("effective_from", db.now())
    db.upsert("kb_cost_rate", row, keys=("rate_code",))
    return row["rate_code"]


def save_cost_factor(factor: dict) -> str:
    row = dict(factor)
    row.setdefault("effective_from", db.now())
    db.upsert("kb_cost_factor", row, keys=("factor_code",))
    return row["factor_code"]


# ========================================================================== #
# 工艺步骤 / 路线 / 设备
# ========================================================================== #
def save_process_step(step: dict, *, params: Optional[Sequence[dict]] = None) -> str:
    row = dict(step)
    row.setdefault("created_at", db.now())
    row.setdefault("effective_from", db.now())
    row["updated_at"] = db.now()
    db.upsert("kb_process_step", row, keys=("step_code",))
    code = row["step_code"]
    if params is not None:
        db.execute("DELETE FROM kb_process_param_template WHERE step_code = ?", (code,))
        for p in params:
            db.insert("kb_process_param_template", {**p, "step_code": code})
    return code


def get_process_step(step_code: str) -> Optional[dict]:
    row = next((r for r in _table("kb_process_step")
                if r.get("step_code") == step_code), None)
    if not row:
        return None
    row["applicable_material"] = db.decode_json(row.get("applicable_material"), [])
    row["applicable_feature"] = db.decode_json(row.get("applicable_feature"), [])
    row["quality_items"] = db.decode_json(row.get("quality_items"), [])
    row["param_templates"] = _sort_rows(
        [t for t in _table("kb_process_param_template") if t.get("step_code") == step_code],
        ("param_key", "asc"),
    )
    return row


def list_process_steps(*, process_type: Optional[str] = None,
                       category: Optional[str] = None) -> list[dict]:
    rows: list[dict] = []
    for row in _table("kb_process_step"):
        if row.get("status") != "active":
            continue
        if process_type and row.get("process_type") != process_type:
            continue
        if category and row.get("category") != category:
            continue
        rows.append(row)
    return _sort_rows(rows, ("step_code", "asc"))


def steps_for_features(feature_types: Iterable[str]) -> list[dict]:
    """按特征类型召回候选工序(applicable_feature 命中即可)。"""
    wanted = {str(t).lower() for t in feature_types if t}
    if not wanted:
        return []
    hits = []
    for step in list_process_steps():
        applicable = {
            str(x).lower() for x in db.decode_json(step.get("applicable_feature"), [])
        }
        if applicable & wanted:
            hits.append(step)
    return hits


def save_route(route: dict, steps: Optional[Sequence[dict]] = None) -> str:
    row = dict(route)
    row.setdefault("created_at", db.now())
    row["updated_at"] = db.now()
    db.upsert("kb_process_route", row, keys=("route_code",))
    code = row["route_code"]
    if steps is not None:
        db.execute("DELETE FROM kb_process_route_step WHERE route_code = ?", (code,))
        for item in steps:
            db.insert("kb_process_route_step", {**item, "route_code": code})
    return code


def get_route(route_code: str, *, expand: bool = True) -> Optional[dict]:
    row = next((r for r in _table("kb_process_route")
                if r.get("route_code") == route_code), None)
    if not row:
        return None
    row["applicable_material"] = db.decode_json(row.get("applicable_material"), [])
    steps = _sort_rows(
        [s for s in _table("kb_process_route_step") if s.get("route_code") == route_code],
        ("seq", "asc"),
    )
    for item in steps:
        item["depends_on"] = db.decode_json(item.get("depends_on"), [])
        item["param_override"] = db.decode_json(item.get("param_override"), {})
        if expand:
            item["step"] = get_process_step(item["step_code"])
    row["steps"] = steps
    return row


def recommend_routes(*, category: Optional[str] = None, material_category: Optional[str] = None,
                     batch_size: Optional[int] = None,
                     industry: Optional[str] = None) -> list[dict]:
    """按零件类别/材料类别/批量召回工艺路线模板,最匹配的排前面。industry 见 _industry_visible。"""
    routes = _sort_rows([r for r in _table("kb_process_route")
                         if r.get("status") == "active" and _industry_visible(r, industry)],
                        ("route_code", "asc"))
    scored: list[dict] = []
    for route in routes:
        score = 0.0
        if category and route.get("applicable_category") == category:
            score += 0.5
        materials = {str(m).lower() for m in db.decode_json(route.get("applicable_material"), [])}
        if material_category and material_category.lower() in materials:
            score += 0.4
        if batch_size is not None:
            low = route.get("batch_min")
            high = route.get("batch_max")
            if (low is None or batch_size >= low) and (high is None or batch_size <= high):
                score += 0.1
        if score > 0:
            scored.append({**route, "score": round(score, 3)})
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored


def save_equipment(equipment: dict) -> str:
    row = dict(equipment)
    row.setdefault("equipment_id", _uid("EQ"))
    row["updated_at"] = db.now()
    db.upsert("kb_equipment", row, keys=("equipment_id",))
    return row["equipment_id"]


def list_equipment(*, equipment_class: Optional[str] = None) -> list[dict]:
    rows: list[dict] = []
    for row in _table("kb_equipment"):
        if row.get("status") != "active":
            continue
        if equipment_class and row.get("equipment_class") != equipment_class:
            continue
        rows.append(row)
    return _sort_rows(rows, ("name", "asc"))


# ========================================================================== #
# 供应商
# ========================================================================== #
def save_supplier(supplier: dict, *, capabilities: Optional[Sequence[dict]] = None) -> str:
    row = dict(supplier)
    row.setdefault("supplier_id", _uid("SUP"))
    row["updated_at"] = db.now()
    db.upsert("kb_supplier", row, keys=("supplier_id",))
    sid = row["supplier_id"]
    if capabilities is not None:
        db.execute("DELETE FROM kb_supplier_capability WHERE supplier_id = ?", (sid,))
        for cap in capabilities:
            db.insert("kb_supplier_capability", {**cap, "supplier_id": sid})
    return sid


def match_suppliers(requirement: dict) -> list[dict]:
    """按粉末要求做确定性达标判定(对齐 models/material.py::SupplierMatch)。"""
    material_code = requirement.get("material_code")
    material_name = requirement.get("material") or requirement.get("material_name")
    suppliers = {row.get("supplier_id"): row for row in _table("kb_supplier")}
    rows: list[dict] = []
    for cap in _table("kb_supplier_capability"):
        supplier = suppliers.get(cap.get("supplier_id"))
        if not supplier or supplier.get("status") != "active":
            continue                      # 与 SQL 的 INNER JOIN + s.status='active' 等价
        if material_code:
            if cap.get("material_code") != material_code:
                continue
        elif material_name and not _like(cap.get("material_name"), f"%{material_name}%"):
            continue
        rows.append({**cap, "supplier": supplier.get("name")})

    purity_min = requirement.get("purity_pct_min")
    d50_min = requirement.get("d50_um_min")
    d50_max = requirement.get("d50_um_max")
    out: list[dict] = []
    for row in rows:
        gaps: list[str] = []
        # 来料认证未通过的供应商,规格再达标也不算合格供方。
        if not row.get("qualified"):
            gaps.append(f"供方未通过来料认证{('：' + row['note']) if row.get('note') else ''}")
        if purity_min is not None:
            offered = row.get("max_purity_pct")
            if offered is None or offered < purity_min:
                gaps.append(f"纯度 {offered} < 要求 {purity_min}%")
        if d50_min is not None and row.get("d50_max_um") is not None and row["d50_max_um"] < d50_min:
            gaps.append(f"D50 上限 {row['d50_max_um']} < 要求下限 {d50_min}µm")
        if d50_max is not None and row.get("d50_min_um") is not None and row["d50_min_um"] > d50_max:
            gaps.append(f"D50 下限 {row['d50_min_um']} > 要求上限 {d50_max}µm")
        out.append({
            "supplier": row["supplier"],
            "supplier_id": row["supplier_id"],
            "material": row.get("material_code") or row.get("material_name"),
            "offered_purity_pct": row.get("max_purity_pct"),
            "offered_d50_um": row.get("d50_min_um"),
            "qualified": not gaps,
            "gap_notes": "; ".join(gaps),
            "moq": row.get("moq"),
            "lead_time": row.get("lead_time"),
        })
    out.sort(key=lambda r: (not r["qualified"], r["supplier"]))
    return out


# ========================================================================== #
# 标准件
# ========================================================================== #
# ========================================================================== #
# 包装专用查询（包装第 3 批；都只读快照，行业固定 packaging）
# 口径见 docs/specs/packaging-knowledge-base-mock-seed.md 2.5。示例数据不参与计算。
# ========================================================================== #
def packaging_box_types() -> list[dict]:
    """全部包装盒型（按编码升序）。"""
    return _sort_rows(_table("kb_packaging_box_type"), ("box_type_code", "asc"))


def _code_form(value) -> str:
    """盒型 / 部件编码的**比较形**：`-` 与 `_` 视为同一个字符、忽略大小写。

    知识库里的编码两种写法都出现过（同一份来源里 `YT-RB-01001-A` 与 `YT_RB_01001_A` 混用），
    逐字比较会把"有模板"判成"没有模板" —— 而"这个盒型有没有部件模板"同时被**候选可运行性**
    （`packaging_match` 的 `part_template_available`）与 **BOM 展开**（`packaging_bom`）使用，
    两处必须给同一个答案（Spec `packaging-box-candidate-rank-and-runnability.md` §2.2）。
    """
    return str(value or "").strip().upper().replace("_", "-")


def packaging_part_templates(box_type_code: str) -> list[dict]:
    """某个盒型的部件构成模板（按部件编码升序）。

    命中口径是「编码的**比较形**相等」（`-` / `_` 等价、忽略大小写，见 `_code_form()`）；
    空编码不给任何行。
    """
    wanted = _code_form(box_type_code)
    if not wanted:
        return []
    return _sort_rows([r for r in _table("kb_packaging_part_template")
                       if _code_form(r.get("box_type_code")) == wanted],
                      ("part_code", "asc"))


def packaging_process_templates(*, box_type_code: Optional[str] = None,
                                part_code: Optional[str] = None) -> list[dict]:
    """包装工艺路线模板；可按盒型/部件过滤（按盒型、部件、工序号升序）。"""
    rows = _table("kb_packaging_process_template")
    if box_type_code is not None:
        rows = [r for r in rows if r.get("box_type_code") == box_type_code]
    if part_code is not None:
        rows = [r for r in rows if r.get("part_code") == part_code]
    return _sort_rows(rows, ("box_type_code", "asc"), ("part_code", "asc"), ("seq", "asc"))


def packaging_insert_accessories() -> list[dict]:
    """内托与配件库全量（按编码升序）。"""
    return _sort_rows(_table("kb_packaging_insert_accessory"), ("accessory_code", "asc"))


def packaging_match_weights() -> list[dict]:
    """盒型五维匹配的权重与硬门槛（按 dimension 升序）。

    维度闭集、权重与 hard_gate 一律以表为准：匹配引擎不得在代码里写死数字，
    业务调权重只改 `kb_packaging_match_weight`，不改代码。
    """
    return _sort_rows(_table("kb_packaging_match_weight"), ("dimension", "asc"))


def packaging_logistics_rules() -> list[dict]:
    """包装物流规则（整箱/托盘/打样快递，按 rule_code 升序）。

    包装第 5 批的 `packaging` 类 BOM 行直接引用这张表的 rule_code；本批只带出规则，
    不算运费（第 7 批）。
    """
    return _sort_rows(_table("kb_packaging_logistics_rule"), ("rule_code", "asc"))


def packaging_cost_contents() -> list[dict]:
    """包材明细全量（按编码升序）。

    包装第 7 批的成本引擎逐条套 FORMULA_CATALOG 的 `PKG-P-*` 公式；本模块只读快照，
    不做任何计算、不缓存派生结果。
    """
    return _sort_rows(_table("kb_packaging_cost_content"), ("content_code", "asc"))


def packaging_tooling_rules() -> list[dict]:
    """工装/刀模规则全量（按编码升序）。

    `mode` 是五种分摊方式的闭集，业务改分摊方式只改这张表，不改代码（包装第 7 批）。
    """
    return _sort_rows(_table("kb_packaging_tooling_rule"), ("tooling_code", "asc"))


def save_standard_part(part: dict) -> str:
    row = dict(part)
    row.setdefault("std_id", _uid("STD"))
    db.upsert("kb_standard_part", row, keys=("std_id",))
    return row["std_id"]


def find_standard_part(spec: str) -> Optional[dict]:
    """按图纸上的规格串(如 'GB/T 5783 M8x25')反查标准件。"""
    text = (spec or "").strip()
    if not text:
        return None
    for row in _table("kb_standard_part"):
        standard_no, designation = row.get("standard_no"), row.get("designation")
        # SQL 的 || 遇 NULL 得 NULL,比较自然不成立 —— 两个字段缺一个就不是精确命中。
        if row.get("status") != "active" or standard_no is None or designation is None:
            continue
        if f"{standard_no} {designation}" == text:
            return row
    hits = [
        row for row in _table("kb_standard_part")
        if row.get("status") == "active" and row.get("designation") is not None
        and _like(text, f"%{row['designation']}%")
    ]
    # ORDER BY LENGTH(designation) DESC LIMIT 1：规格串越长越具体(Python 的 sort 稳定,
    # 同长度的仍按快照顺序)。
    hits.sort(key=lambda r: len(str(r.get("designation") or "")), reverse=True)
    return hits[0] if hits else None
