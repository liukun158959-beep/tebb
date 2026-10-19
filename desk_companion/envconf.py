"""本项目 .env：模型配置。保存时保留其它配置与注释。"""
from __future__ import annotations

from .paths import data_root

from pathlib import Path

LLM_KEYS = ("ATLAS_API_KEY", "ATLAS_BASE_URL", "ATLAS_MODEL")
ENV_KEYS = LLM_KEYS
KNOWN_KEYS = LLM_KEYS


def env_path() -> Path:
    return data_root() / ".env"


def parse_env_file() -> dict[str, str]:
    path = env_path()
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in KNOWN_KEYS:
            out[key] = value.strip().strip('"').strip("'")
    return out


def public_llm_env() -> dict:
    data = parse_env_file()
    key = data.get("ATLAS_API_KEY") or ""
    return {
        "base_url": data.get("ATLAS_BASE_URL") or "",
        "model": data.get("ATLAS_MODEL") or "",
        "has_key": bool(key),
    }


def require_llm_env() -> dict[str, str]:
    data = parse_env_file()
    missing = [key for key in LLM_KEYS if not data.get(key)]
    if missing:
        raise RuntimeError(
            "本项目 .env 缺少 "
            + ", ".join(missing)
            + "。打开主窗「设置」填写 API 地址、模型名和 Key。"
        )
    return {key: data[key] for key in LLM_KEYS}


def validate_llm_env(*, api_key: str, base_url: str, model: str) -> dict[str, str]:
    """Normalize and validate connection fields without writing configuration."""
    from .model_validation import validate_base_url
    api_key = (api_key or "").strip()
    base_url = (base_url or "").strip()
    model = (model or "").strip()
    if not api_key:
        raise RuntimeError("API Key 不能为空。")
    if not base_url:
        raise RuntimeError("API 地址不能为空。")
    if not model:
        raise RuntimeError("模型名不能为空。")
    if any(ch in api_key for ch in "\n\r") or any(ch in base_url for ch in "\n\r") or any(
        ch in model for ch in "\n\r"
    ):
        raise RuntimeError("配置不能包含换行。")
    base_url = validate_base_url(base_url)
    return {"api_key": api_key, "base_url": base_url, "model": model}


def write_llm_env(*, api_key: str, base_url: str, model: str) -> None:
    values = validate_llm_env(api_key=api_key, base_url=base_url, model=model)
    lines = [
        f"ATLAS_API_KEY={values['api_key']}",
        f"ATLAS_BASE_URL={values['base_url']}",
        f"ATLAS_MODEL={values['model']}",
    ]
    path = env_path()
    preserved = []
    if path.is_file():
        for raw in path.read_text("utf-8").splitlines():
            key = raw.strip().removeprefix("export ").partition("=")[0].strip()
            if key not in LLM_KEYS:
                preserved.append(raw)
    lines = preserved + lines
    path.parent.mkdir(parents=True, exist_ok=True)
    env_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
