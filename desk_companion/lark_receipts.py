"""Pure Base record identity decoding for typed v3 and legacy receipts."""


def single_record_id(value):
    if not isinstance(value, dict):
        raise ValueError('记录回执格式无效。')
    if 'record_id_list' in value:
        values = value['record_id_list']
        if not isinstance(values, list) or len(values) > 1 or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError('记录回执须包含零个或一个有效编号。')
        return values[0] if values else ''
    ident = value.get('id') or value.get('record_id') or ''
    if not isinstance(ident, str) or ident and not ident.strip():
        raise ValueError('记录编号无效。')
    return ident
