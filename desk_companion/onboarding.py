"""首次配置状态，仅返回配置是否存在，不读取外部账号或返回密钥。"""
import importlib.util
import json
import shutil

from .paths import asset_root, data_root
from .model_catalog import public_catalog
from .resource_lock import ResourceLock
from .video import atomic_write

LOCK = ResourceLock('onboarding')
STEPS = ('model', 'feishu', 'terminal')


def _saved():
    path = data_root() / 'onboarding.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, UnicodeError):
        return {}  # A damaged hint must not prevent the real settings from opening.


def status() -> dict:
    root = data_root()
    with LOCK:
        saved = _saved()
    models = public_catalog()
    return {
        "ok": True,
        "show": not models["items"] or (bool(saved) and not saved.get("completed")),
        "data_dir": str(root),
        "assets_dir": str(asset_root()),
        "configured": bool(models["items"]),
        "step": saved.get('step') if saved.get('step') in STEPS else 'model',
        "checks": {
            "github": shutil.which("gh") is not None,
            "feishu": shutil.which("lark-cli") is not None,
            "knowledge": importlib.util.find_spec("sentence_transformers") is not None,
            "pet": True,  # The vector pet is included with every client.
            "live2d": (asset_root() / "Core/live2dcubismcore.js").is_file()
                and (asset_root() / "skins/kaltsit/kaltsit.model3.json").is_file(),
        },
    }


def complete() -> dict:
    if not public_catalog()['items']:
        return {'ok': False, 'error': '请先在设置中保存模型，再完成引导。'}
    with LOCK:
        atomic_write(data_root() / 'onboarding.json', {'version': 2, 'completed': True, 'step': 'model'})
    return {"ok": True}


def save_progress(step) -> dict:
    if type(step) is not str or step not in STEPS:
        return {'ok': False, 'error': '未知引导步骤。'}
    with LOCK:
        atomic_write(data_root() / 'onboarding.json', {'version': 2, 'completed': False, 'step': step})
    return {'ok': True}
