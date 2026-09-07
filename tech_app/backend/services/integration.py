"""
组装与整合服务 —— CPQ 技术工艺 2.2。

2.1 把图纸拆成零件并逐件出工艺与成本；2.2 把这些零件装回一台整机，产出四样东西：

  1. 整合图纸  —— 装配图/爆炸图/接线图，作为下面三步的视觉输入(走多模态模型)。
  2. 参数推荐  —— 输入是**用户在本步输入的需求 + 整合图纸 + 2.1 已确认的零件**，
                  三者缺一不可：只看零件推不出整机的电气/性能指标，只看需求推不出
                  实际尺寸与用量。
  3. 组装工艺  —— 先查企业工艺库的**组装路线**，再让模型在库内工序上排产。
  4. 成本测算  —— 以 2.1 各零件已测算的单件成本为底，叠加组装工时、耗材与整机费率。

与 2.1 的一致之处是刻意的：工艺沿用 ProcessPlan、成本沿用 CostAnalysis，因此
process.compute / cost.compute 的确定性重算、前端 inline-analysis 的渲染、
3.1 的汇总导出全都能直接复用，不必为"整机"另开一条分支。
"""
from __future__ import annotations

import json
from typing import Callable, List, Optional, Tuple

from ..config import DATA_DIR
from ..models.cost import CostAnalysis, WebSource
from ..models.integration import (
    IntegrationParam, IntegrationParamFillPlan, IntegrationParamPlan, IntegrationPlan)
from ..models.ir import DesignIR
from ..models.process import ProcessOutline, ProcessPlan, ProcessStep
from ..storage import store
from . import llm_client as claude_client
from . import cost as cost_svc
from . import process as process_svc
from . import process_lookup
from . import product_params

ProgressFn = Optional[Callable[[str], None]]

# 整机在工艺/成本库里的"零件编号"。2.1 的零件是 P-00x，这里固定用 ASSY，
# 两者共用同一批 store.save_process / save_cost 接口而不会互相覆盖。
ASSEMBLY_PART_ID = "ASSY"


# --------------------------------------------------------------------------- #
# 输入上下文：需求 + 零件 + 已有工艺/成本
# --------------------------------------------------------------------------- #
def _part_line(part: dict) -> str:
    """一行写完一个零件：编号、名称、数量、材料、包络尺寸、角色。"""
    material = part.get("material") or {}
    spec = material.get("spec") if isinstance(material, dict) else material
    dims: List[str] = []
    for feature in (part.get("features") or [])[:3]:
        values = [feature.get(k) for k in ("length", "width", "thickness", "height", "diameter")]
        numbers = [f"{v:g}" for v in values if isinstance(v, (int, float))]
        if numbers:
            dims.append(f"{feature.get('type') or '特征'} {'×'.join(numbers)}")
    parts = [f"{part.get('part_id')} {part.get('name') or ''}", f"数量 {part.get('quantity') or 1}"]
    if spec:
        parts.append(f"材料 {spec}")
    if dims:
        parts.append("尺寸 " + "；".join(dims))
    if part.get("role"):
        parts.append(f"角色 {part['role']}")
    return "  - " + " · ".join(parts)


def _requirement_brief(requirement: Optional[dict]) -> str:
    """1.x 已确认的工艺评估需求。字段缺失就跳过，不编。"""
    if not requirement:
        return ""
    fields = [
        ("product_name", "产品名称"), ("customer_name", "客户"), ("quantity", "需求数量"),
        ("delivery_date", "交期"), ("technical_requirements", "技术要求"),
        ("quality_requirements", "质量要求"), ("special_requirements", "特殊要求"),
    ]
    lines = [f"  - {label}: {requirement[key]}"
             for key, label in fields if str(requirement.get(key) or "").strip()]
    return "【1.x 已确认的工艺评估需求】\n" + "\n".join(lines) if lines else ""


def _existing_cost_brief(project_id: str, ir: DesignIR) -> str:
    """2.1 已经算出来的零件单件成本。整机成本必须以它为底，不能重新估一遍。"""
    lines: List[str] = []
    for part in ir.parts:
        saved = store.load_cost(project_id, part.part_id)
        if not saved:
            continue
        summary = cost_svc.compute(saved)
        total = summary.get("computed_total")
        if total is None:
            continue
        lines.append(f"  - {part.part_id} {part.name}：单件 {total:.2f} 元"
                     f"（核算批量 {saved.get('quantity') or 1}）")
    if not lines:
        return ""
    return ("【2.1 已测算的零件单件成本(必须原样引用为整机的零件成本项，不要重新估价)】\n"
            + "\n".join(lines))


def _existing_process_brief(project_id: str, ir: DesignIR) -> str:
    """2.1 各零件的工艺路线末道工序 —— 组装工艺应当接在它们之后，不要重复零件加工。"""
    lines: List[str] = []
    for part in ir.parts:
        plan = store.load_process(project_id, part.part_id)
        steps = (plan or {}).get("steps") or []
        if not steps:
            continue
        tail = "、".join(str(step.get("name") or "") for step in steps[-2:])
        lines.append(f"  - {part.part_id} {part.name}：共 {len(steps)} 道，末道 {tail}")
    if not lines:
        return ""
    return ("【2.1 各零件已排定的工艺(组装工序接在这些之后，不要重复零件本身的加工)】\n"
            + "\n".join(lines))


def build_context(project_id: str, ir: Optional[DesignIR], plan: IntegrationPlan,
                  *, with_cost: bool = False, with_process: bool = False) -> str:
    """把三路输入拼成一段上下文。缺哪一路就少哪一段，不用占位符糊弄模型。"""
    blocks: List[str] = []
    if ir:
        head = [f"设备/总成名称: {ir.device_name}"]
        if ir.design_intent:
            head.append(f"设计意图: {ir.design_intent}")
        if ir.overall_dims:
            head.append(f"整体外形: {ir.overall_dims}")
        if ir.assembly_notes:
            head.append(f"装配说明: {ir.assembly_notes}")
        blocks.append("【2.1 图纸解析结论】\n" + "\n".join(head))
        if ir.parts:
            blocks.append("【2.1 已确认的零件清单】\n"
                          + "\n".join(_part_line(p.model_dump()) for p in ir.parts))
        if ir.standard_parts:
            blocks.append("【2.1 识别出的标准件】\n" + "\n".join(
                f"  - {sp.spec} ×{sp.quantity}" for sp in ir.standard_parts))

    requirement = _requirement_brief(store.load_requirement(project_id))
    if requirement:
        blocks.append(requirement)
    if with_process and ir:
        brief = _existing_process_brief(project_id, ir)
        if brief:
            blocks.append(brief)
    if with_cost and ir:
        brief = _existing_cost_brief(project_id, ir)
        if brief:
            blocks.append(brief)

    if plan.drawings:
        blocks.append("【本步上传的整合图纸】\n" + "\n".join(
            f"  - {d.filename}{'：' + d.note if d.note else ''}" for d in plan.drawings))
    if plan.requirement_note.strip():
        blocks.append("【用户在本步输入的整合需求(优先采用)】\n" + plan.requirement_note.strip())
    if not blocks:
        blocks.append("(暂无结构化输入。请只依据用户补充说明作答，并把缺失输入写进 open_questions)")
    return "\n\n".join(blocks)


def drawing_blocks(project_id: str, plan: IntegrationPlan) -> List[dict]:
    """把整合图纸读成模型输入块。读不到的文件跳过 —— 少一张图不该让整步失败。"""
    blocks: List[dict] = []
    for drawing in plan.drawings:
        path = store.integration_drawing_file(project_id, drawing.filename)
        if not path or not path.exists():
            continue
        blocks.extend(claude_client.attachment_blocks([(drawing.filename, path.read_bytes())]))
    return blocks


# --------------------------------------------------------------------------- #
# ① 参数推荐
# --------------------------------------------------------------------------- #
# 注意：Qwen/兼容网关那条路是 response_format=json_object，**不会把 schema 发给模型**
# （见 services/qwen_client.py::run）。模型只看得到这段提示词，所以**每一个字段名都得
# 在这里写出来**。早先只写了 params/interfaces/part_refs 三个顶层键，唯独给 part_refs
# 列了条目字段 —— 结果就是 part_refs 有 7 行、params 和 interfaces 全空：模型按自己
# 猜的键名输出，条目校验不过，修复几轮后干脆回了空数组。下面的 JSON 骨架不能省。
PARAMS_SYSTEM_PROMPT = """你是资深总装/系统集成工程师。给定一台整机的设计意图、已确认的零件清单、
客户需求，以及装配图/爆炸图，请输出「整机参数与整合方案」。

**只返回一个 JSON 对象，字段名与下面完全一致（英文键名，一个都不能改）：**

{
  "assembly_name": "整机/总成名称",
  "product_family": "li_primary | li_ion_pack | ess | pv_module | other",
  "assembly_category": "整机在企业零部件库里的类别，如 电池包；拿不准填空字符串",
  "material_spec": "整机层面新增的主材/耗材牌号；没有就填空字符串",
  "summary": "整机构型与整合思路，一两句话",
  "params": [
    {"name": "参数名", "value": "参数值", "unit": "单位", "param_code": "字典字段编码",
     "basis": "这个值怎么来的", "source": "需求|整合图纸|零件汇总|工程推荐", "confidence": 0.8}
  ],
  "interfaces": [
    {"name": "上壳↔下壳", "parts": ["P-001", "P-002"], "method": "螺接",
     "spec": "M3×8 自攻钉 ×4", "control": "扭矩 0.6 N·m", "note": "备注，可空"}
  ],
  "part_refs": [
    {"part_id": "P-001", "name": "上壳", "quantity": 1, "role": "外壳", "source": "2.1"}
  ],
  "assumptions": ["关键假设"],
  "open_questions": [{"field": "涉及字段", "reason": "为何不确定", "guess": "最佳猜测，可空"}]
}

要求:
0. **product_family** 决定哪些参数适用。判错会推荐出别的产品线的参数（比如给电池包填"边框膜厚"）。
1. **params 是本次最重要的产出，绝不能是空数组。**
   必须逐项覆盖后面「报价成品参数字典」里本产品族适用的**每一个字段**（那是报价测算单上
   成品行要填的东西）：name 原样抄字典里的名称，param_code 原样抄字典里的字段编码。
   每条都要写 basis 与 source；**由零件尺寸叠加得到的值，basis 必须写出算式**，
   如 "上壳4 + 下壳22.5 = 26.5"。
   实在推不出来的字段，也要给出这一条，value 填空字符串，并把原因写进 open_questions ——
   不要整条省略，报价那边靠字段齐不齐来判断能不能开单。
   字典之外、对本产品确实重要的参数照常补充，param_code 填空字符串。
2. **interfaces**：逐对写清楚谁和谁连、用什么方式连、紧固件或配合规格、控制要点。
   这是下一步组装工艺的输入，宁可少写也不要编造图上没有的连接。
3. **part_refs**：整机单台用到的零件与用量。part_id 必须取自给定的零件清单；
   图上看得出、但 2.1 没拆出来的件，part_id 填空字符串并在 name 里写明，source 写 "整合图纸新增"。
4. 只依据给定信息推断，**不要臆造精确数值**；由推断得到的值把 confidence 降下来。
5. 全程中文（英文键名除外）。confidence 输出 0~1 的数值，quantity 输出整数，
   不要写"高/中/低"或"约 4 个"。所有字段都要出现，没有内容就填空字符串或空数组。"""


def recommend_params(project_id: str, ir: Optional[DesignIR], plan: IntegrationPlan,
                     attachments: Optional[List[Tuple[str, bytes]]] = None,
                     progress: ProgressFn = None) -> IntegrationParamPlan:
    _report(progress, "汇总 2.1 零件、1.x 需求与本步整合图纸")
    content = [claude_client.text_block(build_context(project_id, ir, plan))]
    dictionary = product_params.as_prompt()
    if dictionary:
        _report(progress, f"载入报价成品参数字典（{len(product_params.spec()['fields'])} 个字段 / "
                          f"{len(product_params.families())} 个产品族）")
        content.append(claude_client.text_block(dictionary))
    content.extend(drawing_blocks(project_id, plan))
    content.extend(claude_client.attachment_blocks(attachments))
    _report(progress, "正在调用模型推荐整机参数与整合方案")
    result = claude_client.run(PARAMS_SYSTEM_PROMPT, content, IntegrationParamPlan)
    if not result.assembly_name:
        result.assembly_name = (ir.device_name if ir else "") or "整机总成"
    reconcile_part_refs(result, ir)
    result.product_family = product_params.align(result)
    _report(progress, f"参数 {len(result.params)} 条、连接 {len(result.interfaces)} 处、"
                      f"BOM {len(result.part_refs)} 行")
    _report_param_coverage(result, progress)
    return result


def _report_param_coverage(params: IntegrationParamPlan, progress: ProgressFn) -> None:
    """把"报价要的参数齐没齐"直接播到对话框里。缺口不提示等于没人知道。"""
    report = product_params.checklist(params)
    summary = report["summary"]
    if not summary["total"]:
        return
    _report(progress, f"  ↳ 产品族 {report['family_name']}：报价成品参数 "
                      f"{summary['filled']}/{summary['total']} 已给出"
                      f"（必填 {summary['required_filled']}/{summary['required_total']}）")
    rows = [field for group in report["groups"] for field in group["fields"]]
    missing = [field["name"] for field in rows if field["required"] and not field["filled"]]
    if missing:
        _report(progress, "  ↳ 报价必填项仍缺：" + "、".join(missing))
    mismatched = [f"{field['name']}({field['given_unit']}→{field['unit']})"
                  for field in rows if field.get("unit_mismatch")]
    if mismatched:
        _report(progress, "  ↳ 单位与字典不一致，需人工换算：" + "、".join(mismatched))


def reconcile_part_refs(result: IntegrationParamPlan, ir: Optional[DesignIR]) -> IntegrationParamPlan:
    """用 2.1 的零件清单校正 BOM 行：名称以 IR 为准，编号对不上的标出来。

    模型偶尔会把零件名写成自己的说法、或引用一个根本不存在的编号；人工编辑同样会。
    这里不删任何一行（可能真是图上新增的件），只把结论写进 source：

      2.1        —— 编号在 2.1 的零件清单里，名称已按 IR 对齐
      整合图纸新增 —— 没填编号，是图上看得出但 2.1 没拆出来的件
      编号待核对   —— 填了编号，但 2.1 里没有这个号

    只写 source、不往 role 里追加说明，因为 PUT 会反复调用它 —— 追加会越写越长。
    """
    known = {part.part_id: part for part in (ir.parts if ir else [])}
    for ref in result.part_refs:
        part = known.get(ref.part_id)
        if part:
            ref.name = part.name or ref.name
            ref.source = "2.1"
        else:
            ref.source = "编号待核对" if ref.part_id else "整合图纸新增"
    return result


# --------------------------------------------------------------------------- #
# 整机在工艺/成本库里的检索口径
# --------------------------------------------------------------------------- #
def pseudo_part(plan: IntegrationPlan) -> dict:
    """把整机包装成一个"零件"，好复用 process_lookup / cost_lookup 这两条检索链路。

    features 留空是有意的：整机没有零件级几何特征，硬造几个只会让"库内空白"
    多出一堆不成立的条目。类别与材料从参数推荐结果里取，取不到就留空。
    """
    params = plan.params
    return {
        "part_id": ASSEMBLY_PART_ID,
        "name": (params.assembly_name if params else "") or "整机总成",
        "quantity": 1,
        "features": [],
        "material": {"spec": params.material_spec} if params and params.material_spec else None,
    }


def lookup_process(plan: IntegrationPlan, batch_size: int = 1,
                   progress: ProgressFn = None) -> dict:
    """检索库内**组装路线**。类别由参数推荐给出，没有就退回按批量召回。"""
    return process_lookup.lookup_part(
        pseudo_part(plan), batch_size=batch_size, progress=progress,
        category=(plan.params.assembly_category if plan.params else None),
    )


# --------------------------------------------------------------------------- #
# ② 组装工艺
# --------------------------------------------------------------------------- #
# 与 PARAMS_SYSTEM_PROMPT 同理：这条路不发 schema，模型只能照提示词猜键名。
# 现场就吃过一次：提示词里点了名的 blank / summary / open_questions 都回来了，
# 唯独没点名的工序数组回成了别的键，落到 ProcessOutline 里就是「0 道组装工序」，
# 再往下成本自然也是 0。顶层键必须写死在下面这个骨架里。
PROCESS_SYSTEM_PROMPT = """你是资深总装工艺工程师。给定整机参数、零件间连接关系、各零件已排定的
加工工艺，以及企业工艺库检索结果，请只编制**整机组装线的工序明细**(ProcessOutline)。

**只返回一个 JSON 对象，字段名与下面完全一致（英文键名，一个都不能改；
工序数组的键就叫 steps，不要用 operations/route/工序 之类的别名）：**

{
  "part_name": "整机/总成名称",
  "material": "整机层面的主材/耗材牌号；没有就填空字符串",
  "blank": "来料构成，如 '5 种自制件 + 2 种外购件'",
  "summary": "一句话装配思路",
  "steps": [
    {"step_no": 10, "name": "工序名称", "type": "assembly", "equipment": "设备类别",
     "duration_min": 6, "library_step_code": "沿用的库内工序编号；库里没有就填空字符串"}
  ],
  "open_questions": [{"field": "待确认项", "reason": "为何不确定"}]
}

要求:
1. 只写**组装线上的**工序：零件来料检验/配组 → 分装 → 总装 → 连接(螺接/焊接/点胶) →
   通电或功能测试 → 老化/例行试验 → 外观终检 → 贴标包装。
   **不要重复零件本身的加工工序**(那些在 2.1 已经排过了)。
2. 工序号 step_no 按 10 递增。每道只给: 工序号、名称、类型(type)、设备类别(equipment)、
   单件工时(duration_min，单位分钟，按**每台整机**计)。
3. 给了企业工艺库检索结果时，能沿用库内工序的在 library_step_code 填库内编号；
   库里没有对应工序的**留空** —— 这一项决定"已有工艺 / 缺失工艺"的划分，
   不要为了填满而套用不相干的编号。
4. 每一处 interfaces 里列出的连接都要有对应工序覆盖；覆盖不了的写进 open_questions。
5. blank 字段填"来料构成"(如 '5 种自制件 + 2 种外购件')，summary 写一句装配思路。
6. 全程中文；type 必须严格用以下英文枚举之一：
   blank、turning、milling、drilling、boring、grinding、bench、sheet_metal、
   welding、heat_treat、surface、assembly、inspection、other。
   组装工序用 assembly，检测/测试用 inspection。
   step_no 输出整数，duration_min 输出数值，不要写"约 10 分钟"这类文字。"""


def outline_process(project_id: str, ir: Optional[DesignIR], plan: IntegrationPlan,
                    library: str = "", lookup: Optional[dict] = None,
                    note: str = "", attachments: Optional[List[Tuple[str, bytes]]] = None,
                    progress: ProgressFn = None) -> Tuple[ProcessPlan, dict]:
    """返回 (整机 ProcessPlan, 工艺库覆盖情况)。与 2.1 同样只让模型出工序明细。"""
    content = [claude_client.text_block(
        build_context(project_id, ir, plan, with_process=True))]
    if plan.params:
        content.append(claude_client.text_block(_params_prompt(plan)))
    if library.strip():
        content.append(claude_client.text_block(f"【企业工艺库检索结果】\n{library.strip()}"))
    else:
        content.append(claude_client.text_block(
            "当前没有企业工艺库检索结果；library_step_code 一律留空，不得编造企业工序编号。"))
    if note.strip():
        content.append(claude_client.text_block(f"【用户补充说明(请优先采用)】\n{note.strip()}"))
    content.extend(drawing_blocks(project_id, plan))
    content.extend(claude_client.attachment_blocks(attachments))

    _report(progress, "正在调用模型编制组装工序明细")
    outline = claude_client.run(PROCESS_SYSTEM_PROMPT, content, ProcessOutline)
    return _expand(outline, plan), process_svc.library_coverage(outline, lookup)


def _expand(outline: ProcessOutline, plan: IntegrationPlan) -> ProcessPlan:
    """把精简工序明细补成完整 ProcessPlan。

    补的只是能确定性推出来的部分：depends_on 取线性链(组装线本来就是一条顺序流水)，
    没有它 process.compute 算不出关键路径。不调 process.ensure_minimum_route ——
    那个函数按**零件**的加工模板补最小工序(下料、去毛刺、终检)，套到整机上会凭空
    多出几道不存在的加工工序。
    """
    steps: List[ProcessStep] = []
    previous: Optional[int] = None
    for item in sorted(outline.steps, key=lambda s: s.step_no):
        steps.append(ProcessStep(
            step_no=item.step_no, name=item.name, type=item.type,
            description="", equipment=item.equipment or "",
            duration_min=item.duration_min,
            depends_on=[previous] if previous is not None else [],
            note=(f"沿用库内工序 {item.library_step_code}" if item.library_step_code else ""),
        ))
        previous = item.step_no

    params = plan.params
    result = ProcessPlan(
        part_id=ASSEMBLY_PART_ID,
        part_name=outline.part_name or (params.assembly_name if params else "") or "整机总成",
        material=outline.material or (params.material_spec if params else None),
        blank=outline.blank,
        summary=outline.summary,
        steps=steps,
        open_questions=list(outline.open_questions),
        part_class="assembly",
    )
    result.rule_warnings = process_svc.validate_rules(result.model_dump())
    return result


def _params_prompt(plan: IntegrationPlan) -> str:
    """把参数推荐的结论回喂给工艺/成本两步 —— 它们是同一条链上的下游。"""
    params = plan.params
    if not params:
        return ""
    lines = [f"【2.2 已确认的整机参数与整合方案】整机: {params.assembly_name}"]
    if params.product_family:
        lines.append(f"产品族: {product_params.resolve_family(params.product_family)}"
                     f"（{product_params.family_name(params.product_family)}）")
    if params.assembly_category:
        lines.append(f"类别: {params.assembly_category}")
    if params.summary:
        lines.append(f"整合思路: {params.summary}")
    if params.params:
        lines.append("整机参数:")
        lines += [f"  - {p.name}: {p.value}{p.unit or ''}"
                  + (f"（依据 {p.basis}）" if p.basis else "") for p in params.params]
    if params.interfaces:
        lines.append("零件间连接:")
        lines += [f"  - {i.name}: {i.method or '方式待定'}"
                  + (f" · {i.spec}" if i.spec else "")
                  + (f" · {i.control}" if i.control else "") for i in params.interfaces]
    if params.part_refs:
        lines.append("整机 BOM(单台用量):")
        lines += [f"  - {r.part_id or '(新增)'} {r.name} ×{r.quantity}" for r in params.part_refs]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# ③ 成本测算
# --------------------------------------------------------------------------- #
COST_SYSTEM_PROMPT = """你是资深成本工程师。给定整机 BOM、组装工艺路线、企业成本库检索结果，
以及 2.1 已经算出来的**各零件单件成本**，请输出结构化的整机成本测算(CostAnalysis)。

**只返回一个 JSON 对象，字段名与下面完全一致（英文键名，一个都不能改；
明细数组的键就叫 items，不要用 cost_items/成本明细/材料明细 之类的别名）：**

{
  "part_name": "整机/总成名称",
  "quantity": 1,
  "currency": "CNY",
  "summary": "成本构成概述，一两句话",
  "items": [
    {"category": "material", "name": "零件/物料名称", "quantity": 1, "unit": "件",
     "unit_price": 3.15, "amount": 3.15, "basis": "这个价怎么来的", "source": "企业成本库|2.1 测算",
     "confidence": 0.8}
  ],
  "assumptions": ["关键假设"],
  "open_questions": [{"field": "待确认项", "reason": "为何不确定"}]
}

**本企业的成本口径：只有材料是逐项算的**，人工、制造费用、加工费用由平台按材料成本
乘固定系数推导（材料/1.13/0.791×((1-0.791)×0.3556/0.1778/0.0944)）。所以：

1. **只输出材料明细**：BOM 里每个零件/外购件各占一行，category 用 material 或 standard_part，
   quantity 填单台用量，unit_price 填单价，amount 填 数量×单价。
   已在 2.1 测算过的零件，unit_price **原样引用 2.1 给出的单件成本**，
   basis 写 "2.1 已测算单件成本 × 单台用量"，不要重新估价 —— 否则整机成本与零件页对不上。
2. **不要输出人工、制造费用、加工费、管理费、利润这些行**：平台会按上面的公式统一推导，
   你给了也会被覆盖，反而让人以为是两套口径在打架。组装工序的工时信息写进
   assumptions 供人核对即可。
3. quantity 字段填给定的核算批量(台)。
4. 缺价、缺费率的写进 open_questions 与 assumptions，**不要用一个默认值蒙混过去**。
5. 全程中文；category 必须用给定英文枚举；金额与单价输出数值，不要带单位或千分位。"""


def analyze_cost(project_id: str, ir: Optional[DesignIR], plan: IntegrationPlan,
                 quantity: int = 1, web: bool = False, library: str = "",
                 note: str = "", attachments: Optional[List[Tuple[str, bytes]]] = None,
                 progress: ProgressFn = None) -> CostAnalysis:
    use_web = web and claude_client.WEB_SEARCH_AVAILABLE
    content = [claude_client.text_block(
        build_context(project_id, ir, plan, with_cost=True))]
    if plan.params:
        content.append(claude_client.text_block(_params_prompt(plan)))
    if plan.process and plan.process.steps:
        content.append(claude_client.text_block(_process_prompt(plan)))
    if library.strip():
        content.append(claude_client.text_block(f"【企业成本库检索结果】\n{library.strip()}"))
    content.append(claude_client.text_block(f"【核算批量】{max(1, quantity)} 台"))
    content.append(claude_client.text_block(claude_client.web_search_notice(use_web)))
    if note.strip():
        content.append(claude_client.text_block(f"【用户补充说明(请优先采用)】\n{note.strip()}"))
    content.extend(claude_client.attachment_blocks(attachments))

    _report(progress, "正在调用模型生成整机成本拆解")
    sources: list = []
    result = claude_client.run(
        COST_SYSTEM_PROMPT, content, CostAnalysis,
        extra_tools=claude_client.web_search_tools(use_web), sources_out=sources,
    )
    result.part_id = ASSEMBLY_PART_ID
    result.part_name = result.part_name or (
        plan.params.assembly_name if plan.params else "") or "整机总成"
    result.quantity = max(1, quantity)
    have = {source.url for source in result.search_sources}
    for source in sources:
        if source.get("url") and source["url"] not in have:
            result.search_sources.append(
                WebSource(title=source.get("title") or source["url"], url=source["url"]))
            have.add(source["url"])
    return result


def _process_prompt(plan: IntegrationPlan) -> str:
    steps = (plan.process.steps if plan.process else []) or []
    lines = ["【2.2 已排定的组装工艺路线(组装成本按它逐道算)】"]
    lines += [f"  {step.step_no} {step.name} · 类型={step.type.value}"
              f" · 设备={step.equipment or '未指定'}"
              f" · 工时={step.duration_min if step.duration_min is not None else '未估'}min"
              for step in steps]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
def _report(progress: ProgressFn, message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:      # 进度上报失败不能影响主流程
            pass


def load_plan(project_id: str) -> IntegrationPlan:
    saved = store.load_integration(project_id)
    plan = IntegrationPlan(**saved) if saved else IntegrationPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


def save_plan(project_id: str, plan: IntegrationPlan, author: str = "system") -> IntegrationPlan:
    plan.project_id = project_id
    store.save_integration(project_id, plan.model_dump(), author=author)
    return plan


def status(plan: IntegrationPlan) -> dict:
    """四个环节各自完成了没有。前端据此点亮步骤条，不用自己去猜字段。"""
    return {
        "drawings": len(plan.drawings),
        # has_params = 参数推荐**跑过了**，不是"推出了非空参数"。两者分开的原因：
        # 模型偶尔一条参数都给不出，而那恰恰是最需要人工补录的时候 —— 用非空判定的话，
        # 界面会把「编辑」按钮藏起来，字段反而永远填不上（这正是现场遇到的问题）。
        # 参数够不够由 param_count 与报价字典的覆盖率去说。
        "has_params": plan.params is not None,
        "param_count": len(plan.params.params) if plan.params else 0,
        # 同上：has_* 一律表示"这一环节跑过了"，条数另算。
        # 模型偶尔会回一份空的工序表/成本表，用非空判定的话界面会把「编辑」藏起来、
        # 把下一环节锁死 —— 人既补不了内容，也走不下去，只能卡在那儿。
        "has_process": plan.process is not None,
        "process_step_count": len(plan.process.steps) if plan.process else 0,
        "has_cost": plan.cost is not None,
        "cost_item_count": len(plan.cost.items) if plan.cost else 0,
        # 「整合参数」环节：成本之后的收口。required_missing 是硬指标（报价必填还缺几项），
        # params_final 是人按下的确认 —— 两者都给前端，缺口要看得见，确认要有人负责。
        "params_confirmed": bool(plan.params_confirmed),
        "process_confirmed": bool(plan.process_confirmed),
        "params_final": bool(plan.params_final),
        # 成品编码是「写入数据库」生成的，不算在 required_missing 里（那是给人补的清单），
        # 但它同样是发报价的前提 —— 单独给前端一个标志，按钮才说得清自己为什么是灰的。
        "has_material_code": bool(plan.material_writes),
        "required_missing": len(product_params.missing_required(plan.params)) if plan.params else 0,
        "confirmed": bool(plan.confirmed),
    }


# 「整合参数」的智能补全。与参数推荐是两件事：那个从零推一整份参数表，
# 这个只针对**还缺的那几项**，并且把 2.1 零件、已排工艺、已算成本一并给模型 ——
# 缺的往往正是"要把前面几步的结论汇总一下才知道"的那类（重量、最大尺寸、工作温度）。
#
# 注意这条路不发 schema（services/qwen_client.py::run 用 json_object），
# 每个字段名都必须写在提示词里。
AUTOFILL_SYSTEM_PROMPT = """你是资深总装工程师。下面给出一台整机的设计意图、零件清单、已排定的
组装工艺与已测算的成本，以及**报价成品参数里还没有值的字段**。请逐项给出建议值。

**只返回一个 JSON 对象，字段名与下面完全一致（英文键名，一个都不能改）：**

{
  "fills": [
    {"code": "字段编码（原样抄下面清单里的 code）", "value": "建议值",
     "unit": "单位，与清单一致时可不给", "basis": "这个值怎么来的", "confidence": 0.8}
  ],
  "unresolved": ["确实推不出来的字段中文名"]
}

要求：
1. **只填推得出来的**。能由零件尺寸叠加、需求原文、已排工艺或已算成本推出来的，给值并在
   basis 里说清依据（如"上壳 13.25 + 下壳 13.25 = 26.5"）；
   靠行业常识猜的，confidence 给 0.4 以下并在 basis 里写明是经验值。
2. **推不出来就放进 unresolved，不要编**。产品系列、产品型号、成品编码这类要由企业内部
   定义或系统生成的，除非资料里明确写了，否则一律 unresolved。
3. 给了取值范围（枚举）的字段，只能从给定取值里选。
4. 单位以清单给的为准；数值按那个单位写，不要换算成别的量级。
5. 全程中文；value 与 basis 都写中文，code 原样抄清单里的英文编码。"""


def autofill_params(project_id: str, ir: Optional[DesignIR], plan: IntegrationPlan,
                    note: str = "", progress: ProgressFn = None):
    """给「整合参数」里还缺的字段出一份建议值。返回 IntegrationParamFillPlan。

    只返回建议、不落库 —— 补全的值要由工艺经理过目后才写进参数表。
    """
    missing = product_params.missing_all(plan.params) if plan.params else []
    if not missing:
        return IntegrationParamFillPlan()
    lines = []
    for field in missing:
        parts = [f"  - code={field['code']}  {field['name']}"]
        if field.get("unit"):
            parts.append(f"单位 {field['unit']}")
        if field.get("required"):
            parts.append("报价必填")
        if field.get("options"):
            parts.append("取值：" + " / ".join(field["options"]))
        if field.get("example"):
            parts.append(f"示例 {str(field['example'])[:40]}")
        lines.append(" · ".join(parts))

    content = [claude_client.text_block(
        build_context(project_id, ir, plan, with_process=True, with_cost=True))]
    if plan.params:
        content.append(claude_client.text_block(_params_prompt(plan)))
    if plan.process and plan.process.steps:
        content.append(claude_client.text_block(_process_prompt(plan)))
    content.append(claude_client.text_block(
        "【还没有值的报价成品参数（逐项给建议）】\n" + "\n".join(lines)))
    if note.strip():
        content.append(claude_client.text_block(f"【用户补充说明(请优先采用)】\n{note.strip()}"))
    content.extend(drawing_blocks(project_id, plan))

    _report(progress, f"正在为 {len(missing)} 项缺失参数生成建议值")
    result = claude_client.run(AUTOFILL_SYSTEM_PROMPT, content, IntegrationParamFillPlan)
    # 只保留字典里认得、且确实还缺的字段：模型偶尔会顺手"补"已经有值的项，
    # 那会把人工填的值覆盖掉 —— 补全只该动空格子。
    allowed = {field["code"] for field in missing}
    result.fills = [fill for fill in result.fills
                    if fill.code in allowed and str(fill.value or "").strip()]
    _report(progress, f"  ↳ 给出 {len(result.fills)} 项建议、"
                      f"{len(result.unresolved)} 项确实推不出来")
    return result


# 业务库写不进去时的本地取号。编码形状与正式的一致（92022+3 位），因为报价那边
# 是**按产品行里的字符串**匹配定价/加价规则的（cpq_agent_server::_handle_markup_fill
# 里 `code in codes`），号存不存在于主数据它并不查 —— 所以临时号照样能让定价跑通。
# 但它终究不是主数据里的号，必须在参数来源上标出来，别让人拿它去对账。
_LOCAL_CODE_FILE = DATA_DIR / "_local_material_codes.json"
_LOCAL_CODE_PREFIX = "92022"


def next_local_code() -> str:
    """本地临时成品编码。跨项目递增，落在 DATA_DIR 下的一个小计数文件里。"""
    try:
        used = int(json.loads(_LOCAL_CODE_FILE.read_text(encoding="utf-8")).get("serial") or 0)
    except Exception:
        used = 0
    serial = max(1, used + 1) % 1000 or 1
    try:
        _LOCAL_CODE_FILE.write_text(json.dumps({"serial": serial}), encoding="utf-8")
    except OSError:
        pass                                  # 计数写不下也不该挡住推送
    return f"{_LOCAL_CODE_PREFIX}{serial:03d}"


def apply_material_code(plan: IntegrationPlan, number: str, name: str,
                        source: str = "主数据", basis: str = "写入主数据时生成") -> None:
    """把写库拿到的成品编码/名称回填进整机参数。

    这不是锦上添花：报价的定价与加价规则是**按成品编码匹配产品行**的
    （cpq_agent_server::_handle_markup_fill 里 `code in codes` 那道过滤），
    产品行没有编码，规则查得到、加价值却落不到产品上，最后价格 = 基础成本，
    既没有利润也没有加价 —— 而且界面上一点异常都看不出来。
    编码是写库那一刻才产生的，所以只能在这里回填。
    """
    if plan.params is None:
        plan.params = IntegrationParamPlan()
    for code, value in (("product_item_code", number), ("product_item_name", name)):
        value = str(value or "").strip()
        field = product_params.field_of(code)
        if not value or not field:
            continue
        existing = next((param for param in plan.params.params
                         if (param.param_code or product_params.match_code(param)) == code), None)
        if existing is None:
            plan.params.params.append(IntegrationParam(
                name=field["name"], value=value, param_code=code,
                basis=basis, source=source, confidence=1.0))
        else:
            existing.param_code = code
            existing.value = value
            existing.basis = basis
            existing.source = source
            existing.confidence = 1.0
    product_params.align(plan.params)


def finalize_params(plan: IntegrationPlan, values: dict) -> IntegrationParamPlan:
    """把人工补填的值合进整机参数。values 形如 {字段编码: {"value":…, "unit":…}}。

    按**字段编码**合并而不是按参数名：模型给的名字可能是"标称电压(V)"，人手填的是
    "标称电压"，按名字合并会多出一行同义参数，报价那边取到哪一条全看运气。
    编码在字典里、模型又没给过的，补一条新参数并回填字典里的中文名与单位。
    """
    if plan.params is None:
        plan.params = IntegrationParamPlan()
    by_code = {}
    for param in plan.params.params:
        code = param.param_code or product_params.match_code(param)
        if code and code not in by_code:
            param.param_code = code
            by_code[code] = param
    for code, item in (values or {}).items():
        field = product_params.field_of(code)
        if not field:
            continue                      # 字典之外的编码不收：报价那边没有这一列
        if code in product_params.GENERATED_CODES:
            continue                      # 成品编码由「写入数据库」生成，手填的号是假的
        raw = item if isinstance(item, dict) else {"value": item}
        value = str(raw.get("value") or "").strip()
        unit = str(raw.get("unit") or "").strip()
        existing = by_code.get(code)
        if existing is None:
            if not value:
                continue                  # 空值不必凭空建一行
            existing = IntegrationParam(
                name=field["name"], value=value, param_code=code,
                unit=unit or field.get("unit") or None,
                basis="工艺经理在「整合参数」中补填", source="人工补填", confidence=1.0)
            plan.params.params.append(existing)
            by_code[code] = existing
            continue
        # 已有行：只在真的给了新值时覆盖，避免一次保存把模型推出来的值抹成空。
        if value:
            existing.value = value
            if existing.source != "人工补填":
                existing.basis = "工艺经理在「整合参数」中补填"
                existing.source = "人工补填"
                existing.confidence = 1.0
        if unit:
            existing.unit = unit
        if not existing.name:
            existing.name = field["name"]
    product_params.align(plan.params)
    return plan.params


def summarize(plan: IntegrationPlan) -> dict:
    """整机层面的确定性派生量。金额与工时都由平台算，模型说了不算。"""
    process = plan.process.model_dump() if plan.process else None
    cost = plan.cost.model_dump() if plan.cost else None
    return {
        "process": process_svc.compute(process) if process else None,
        "cost": cost_svc.compute(cost) if cost else None,
    }


def payload(project_id: str, plan: IntegrationPlan) -> dict:
    """统一的接口返回体。四个 GET/PUT/任务结果都走它，前端只需认一种形状。"""
    derived = summarize(plan)
    return {
        "plan": plan.model_dump(),
        "status": status(plan),
        # 报价成品参数的对照表：报价那头要的每一项填没填、填了什么。
        # 没做参数推荐时给一份空表，前端照样能把"要填哪些"先展示出来。
        "param_checklist": product_params.checklist(plan.params) if plan.params else None,
        "param_families": product_params.families(),
        "process_validation": derived["process"],
        "cost_summary": derived["cost"],
        "process_lookup": store.load_process_lookup(project_id, ASSEMBLY_PART_ID),
        "cost_lookup": store.load_cost_lookup(project_id, ASSEMBLY_PART_ID),
        "process_coverage": store.load_process_coverage(project_id, ASSEMBLY_PART_ID),
    }
