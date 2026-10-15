"""报价和工艺共用的文件资产策略；注册表同时由浏览器读取。"""
import json
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=1)
def registry():
    return json.loads((Path(__file__).resolve().parent/'tech_app/frontend/file-policy.json').read_text())


def max_bytes():return int(registry()['max_bytes'])


def _expand(items):
    groups=registry()['groups']
    return tuple(dict.fromkeys(ext for item in items for ext in (groups[item] if item in groups else [item])))


def extensions(purpose):return _expand(registry()['purposes'][purpose]['accept'])


def allowed(purpose,name):
    ext=str(name).lower() if str(name).startswith('.') and '/' not in str(name) else Path(str(name)).suffix.lower()
    return ext in extensions(purpose)


def capability(purpose,name):
    ext=Path(str(name)).suffix.lower()
    rules=registry()['purposes'][purpose]
    mode='unsupported' if not allowed(purpose,name) else 'direct' if ext in _expand(rules['direct']) else 'converted' if ext in _expand(rules['converted']) else 'save_only'
    return {'mode':mode,'label':registry()['labels'][mode],'note':registry()['notes'].get(ext,'')}


def validate(name,size,purpose='asset'):
    if not name or len(str(name))>240 or any(c in str(name) for c in ('/','\\','\x00','\r','\n')) or name in ('.','..'):
        raise ValueError('文件名无效')
    if not allowed(purpose,name):raise ValueError('此入口不支持该格式：'+str(name))
    if size<=0:raise ValueError('文件为空')
    if size>max_bytes():raise ValueError('单文件超过16MiB上限')


def primary_name(names):
    names=list(names)
    for suffixes in (registry()['groups']['cad'],registry()['groups']['raster'],registry()['groups']['native_model']+registry()['groups']['stored_model'],['.pdf']):
        for name in names:
            if Path(name).suffix.lower() in suffixes:return name
    return None
