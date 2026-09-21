"""块与变换：嵌套、镜像、循环保护、深度限制（DWG 第 3 批 Spec §5）。

两条硬口径：
  · 几何**必须应用完整变换链**（父引用矩阵 × 子引用矩阵），不许直接读块定义里的原始坐标；
  · 循环引用/超深必须**截断 + 警告**，不许死循环、不许抛未捕获异常。

矩阵约定：子引用矩阵左乘父矩阵（`child @ parent`），与 ezdxf 自己的
`virtual_entities()` 结果逐点一致（已实测对齐）。`matrix=None` 表示恒等（不拷贝实体）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

WARNING_CYCLE = "block_cycle"
WARNING_DEPTH = "block_depth_exceeded"


class Expansion:
    """一次块展开的记账：叶子实体、警告、截断计数。"""

    def __init__(self, max_depth: int, max_entities: int) -> None:
        self.max_depth = int(max_depth)
        self.max_entities = int(max_entities)
        self.leaves: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
        self.truncated = 0
        self.overflow = False

    def warn(self, code: str, message: str, refs: Optional[Sequence[str]] = None) -> None:
        self.warnings.append({"code": code, "message": message, "evidence_refs": list(refs or [])})

    def push(self, item: Dict[str, Any]) -> None:
        if len(self.leaves) >= self.max_entities:
            self.overflow = True
            return
        self.leaves.append(item)


def _handle_of(entity: Any) -> str:
    try:
        return str(entity.dxf.get("handle", "") or "")
    except Exception:  # pragma: no cover - 极少数实体没有 dxf 属性容器
        return ""


def walk(container: Any, doc: Any, state: Expansion, *,
         matrix: Any = None, block_path: Tuple[Dict[str, str], ...] = (),
         seen: Optional[Set[str]] = None, depth: int = 0, space: str = "model") -> None:
    """递归展开一个容器（模型空间 / 块定义），把叶子实体与变换矩阵交给 state。"""
    seen = set(seen or ())
    for entity in container:
        kind = entity.dxftype()
        handle = _handle_of(entity)
        if kind == "INSERT":
            name = str(entity.dxf.get("name", "") or "")
            ref = "ev:B:%s" % (handle or name)
            state.push({"entity": entity, "matrix": matrix, "block_path": list(block_path),
                        "kind": "insert", "handle": handle, "block_name": name, "space": space})
            if str(name).upper() in seen:
                state.truncated += 1
                state.warn(WARNING_CYCLE,
                           "块 %s 出现循环引用，已在该层截断（Spec §5）" % name, [ref])
                continue
            if depth >= state.max_depth:
                state.truncated += 1
                state.warn(WARNING_DEPTH,
                           "块 %s 超过最大嵌套深度 %d，已截断（Spec §5）"
                           % (name, state.max_depth), [ref])
                continue
            block = doc.blocks.get(name) if hasattr(doc, "blocks") else None
            if block is None:
                state.truncated += 1
                state.warn("block_missing", "块 %s 未定义，已跳过展开" % name, [ref])
                continue
            own = entity.matrix44()
            walk(block, doc, state,
                 matrix=own if matrix is None else (own @ matrix),
                 block_path=block_path + ({"block": name, "insert_handle": handle},),
                 seen=seen | {str(name).upper()}, depth=depth + 1, space=space)
            continue
        state.push({"entity": entity, "matrix": matrix, "block_path": list(block_path),
                    "kind": kind, "handle": handle, "block_name": "", "space": space})


def apply_matrix(item: Dict[str, Any]) -> Any:
    """返回应用过变换链的实体副本；没有变换时原样返回（避免无谓拷贝）。"""
    matrix = item.get("matrix")
    entity = item["entity"]
    if matrix is None:
        return entity
    try:
        clone = entity.copy()
        clone.transform(matrix)
    except Exception:  # pragma: no cover - 个别实体类型不支持变换时退回原始几何
        return entity
    return clone


def _path_prefix(item: Dict[str, Any]) -> str:
    return "/".join(str(step.get("insert_handle") or "") for step in item.get("block_path") or [])


def entity_id_of(item: Dict[str, Any], handle: str) -> str:
    """稳定 ID：`ent:<space>:<父引用 handle>/<子实体 handle>`（Spec §3.1）。"""
    prefix = _path_prefix(item)
    body = "%s/%s" % (prefix, handle) if prefix else str(handle or "")
    return "ent:%s:%s" % (str(item.get("space") or "model"), body)


def evidence_ref_of(item: Dict[str, Any], handle: str) -> str:
    prefix = _path_prefix(item)
    body = "%s/%s" % (prefix, handle) if prefix else str(handle or "")
    return "ev:E:%s" % body
