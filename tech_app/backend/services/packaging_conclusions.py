"""Conservative per-part reuse; never mutate or repin a historical conclusion."""
import copy


def _input(row):
    value = copy.deepcopy(row)
    # Rendering-only data: not dimensions, bindings, materials or process inputs.
    for key in ('thumbnail', 'thumbnail_url', 'preview_url'):
        value.pop(key, None)
    return value


def part_input_fingerprint(row, doc):
    from .packaging_semantics.model import canonical_json, json_safe, sha256_hex

    source = {'row': _input(row), 'derived_from_drawing': doc.get('derived_from_drawing') is True,
              'auto_geometry_inputs': doc.get('auto_geometry_inputs') is not False,
              'uses_demo_inputs': doc.get('poc_demo') is True and (row.get('poc_inputs') or {}).get('mock') is True}
    return sha256_hex(canonical_json(json_safe(source)))


def usable(project_id, record, current, *, current_row=None):
    if not record or not current or not current.get('business_parts_hash'):
        return False
    code = record.get('part_code') or record.get('business_part_code')
    fingerprint = record.get('business_part_input_fingerprint')
    if fingerprint:
        row = current_row if current_row is not None else next(
            (row for row in current.get('business_parts', []) if row.get('business_part_code') == code), None)
        return row is not None and part_input_fingerprint(row, current) == fingerprint
    if record.get('business_parts_hash') == current['business_parts_hash']:
        return True
    if not record.get('business_parts_id'):
        return False
    from . import packaging_parts
    old = packaging_parts.load_business_parts(project_id, record['business_parts_id'])
    if not old or old.get('business_parts_hash') != record.get('business_parts_hash'):
        return False
    before = [row for row in old.get('business_parts', []) if row.get('business_part_code') == code]
    after = [row for row in current.get('business_parts', []) if row.get('business_part_code') == code]
    return bool(len(before) == len(after) == 1 and _input(before[0]) == _input(after[0]))
