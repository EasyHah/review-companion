"""Small host-neutral configuration and one resolver for shared review records."""
import hashlib
import json
import os
from pathlib import Path

from companion_store import StoreError


def load_config(environ=None):
    env = os.environ if environ is None else environ
    home = Path(env.get('HOME',str(Path.home())))
    selected = env.get('REVIEW_COMPANION_CONFIG')
    file = Path(selected).expanduser() if selected else home/'.config/review-companion/config.json'
    if selected and not file.exists(): raise StoreError('config_missing','指定的回顾配置文件不存在')
    try: config = json.loads(file.read_text()) if file.exists() else {}
    except (ValueError,OSError): raise StoreError('config_unreadable','回顾配置无法读取；未修改原文件')
    if not isinstance(config,dict) or set(config)-{'data_root','project_data_dirs','tools'}:
        raise StoreError('invalid_config','回顾配置仅支持 data_root、project_data_dirs、tools')
    if not isinstance(config.get('project_data_dirs',{}),dict) or not isinstance(config.get('tools',{}),dict):
        raise StoreError('invalid_config','项目目录映射和工具配置必须为对象')
    if any(not isinstance(k,str) or not isinstance(v,str) or not k or not v for k,v in config.get('project_data_dirs',{}).items()):
        raise StoreError('invalid_config','项目路径与数据目录必须为非空文字')
    config['_home']=str(home)
    return config


def apply_tools(config):
    for name, variable in (('git','REVIEW_COMPANION_GIT'),('editor','REVIEW_COMPANION_EDITOR')):
        value=config.get('tools',{}).get(name)
        if value:
            if not isinstance(value,str) or any(c in value for c in '\x00\r\n') or value.startswith('-'):
                raise StoreError('invalid_config','工具路径无效')
            os.environ.setdefault(variable,value)


def _directory(value,home):
    if not isinstance(value,str) or not value or '\x00' in value:
        raise StoreError('invalid_config','数据目录必须是有效路径')
    if value.startswith('~/'): value=str(home/value[2:])
    return Path(value).expanduser().resolve()


def data_directory(repository, exact=None, config=None, environ=None):
    env=os.environ if environ is None else environ
    config=load_config(env) if config is None else config
    home=Path(config.get('_home',env.get('HOME',str(Path.home()))))
    repo=str(Path(repository).resolve())
    explicit=exact or env.get('REVIEW_COMPANION_DATA_DIR')
    if explicit: return _directory(explicit,home)
    matches=[value for key,value in config.get('project_data_dirs',{}).items() if str(Path(key).expanduser().resolve()) == repo]
    if len(set(matches))>1:raise StoreError('invalid_config','同一仓库的路径别名配置了不同数据目录，请明确选择')
    mapped=matches[0] if matches else None
    if mapped:return _directory(mapped,home)
    digest=hashlib.sha256(repo.encode()).hexdigest()
    default=_directory(config.get('data_root',str(home/'.local/share/review-companion')),home)/'projects'/digest[:24]
    if (default/'reviews.json').exists() or 'data_root' in config:return default
    # Read-only reuse of the two known older formats. Never copy or merge records.
    candidates=[home/'.local/share/review-companion-pi'/digest[:24],home/'.local/share/review-companion-claude'/'projects'/digest[:20]]
    existing=[p for p in candidates if (p/'reviews.json').exists()]
    if len(existing)>1:raise StoreError('data_directory_ambiguous','此项目同时有 Pi 与 Claude 旧回顾。请在 project_data_dirs 中明确选一个目录；未合并或覆盖记录')
    return existing[0].resolve() if existing else default
