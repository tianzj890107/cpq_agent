"""已核对可见明细表的版本化规则；运行期只读受控JSON，不读工作簿。"""
import copy
import json
from pathlib import Path

PROFILE = 'detail_sheet/1'
RULE_PATH = Path(__file__).resolve().parents[2] / 'agent_knowledge/rules/packaging_detail_cost_rules.json'
MANIFEST = json.loads(RULE_PATH.read_text(encoding='utf-8'))
CATALOG = {r['formula_code']: r for r in MANIFEST['formulas']}
CODE_MAP = {r['cost_category']: r['formula_code'] for r in MANIFEST['formulas']
            if not r['formula_code'].endswith(('-SOFT', '-PATCH'))}
EXTRA_LABELS = {'auxiliary_fee':'覆膜固定辅助费（元/模）',
    'stamping_auxiliary_fee':'烫金固定辅助费（元/模）','mounting_rate':'裱纸千平方英寸费率',
    'die_cut_unit_fee':'啤切单模加工费（元）','groove_unit_fee':'V槽单次加工费（元）',
    'glue_area_mm2':'实际涂胶面积（平方毫米）'}


def select(category, variables=None):
    variables = variables or {}
    if category == 'glue' and variables.get('glue_area_mm2') is not None:
        return 'PKG-D-GLUE-PATCH'
    if category == 'die_cutting' and variables.get('die_cut_variant') == 'soft':
        return 'PKG-D-DIE-CUT-SOFT'
    return CODE_MAP.get(category)


def entry(code):
    result = copy.deepcopy(CATALOG[code])
    result['formula_source'] = 'builtin'
    return result


def minimum_amount(entry, variables):
    quantity = float(variables.get('quote_quantity') or 0)
    if quantity <= 0:
        return 0
    times = float(variables.get('times', 1)) if entry['cost_category'] == 'v_groove' else 1
    return float(entry.get('minimum_batch_charge') or 0) * times / quantity
