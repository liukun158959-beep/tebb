"""Atlas 工具：GitHub 状态、roadmap、近期活动。走本机 gh。"""
from __future__ import annotations

from .github import format_recent_text, format_roadmap_text, format_status_text


def github_status_tool(_args: dict) -> str:
    try:
        return format_status_text()
    except RuntimeError as exc:
        return str(exc)


def github_roadmap_tool(args: dict) -> str:
    name = args.get("repo") if isinstance(args, dict) else None
    try:
        return format_roadmap_text(name if type(name) is str else "")
    except RuntimeError as exc:
        return str(exc)


def github_recent_tool(args: dict) -> str:
    name = args.get("repo") if isinstance(args, dict) else None
    try:
        return format_recent_text(name if type(name) is str else "")
    except RuntimeError as exc:
        return str(exc)


STATUS_SPEC = {
    "func": github_status_tool,
    "name": "github_status",
    "description": (
        "查看本机 gh 登录态，以及当前账号下全部未归档仓库的状态和路线图总结。"
        "用户问 GitHub 连没连上、有哪些仓库、开发状态、路线图总结时必须调用。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
    "isReadOnly": True,
}

ROADMAP_SPEC = {
    "func": github_roadmap_tool,
    "name": "github_roadmap",
    "description": (
        "按仓库读取 roadmap：未关闭且带 milestone 的 GitHub issue。"
        "用户问某仓库下一步、路线图、milestone、接下来做什么时必须调用。"
        "没有带 milestone 的 issue 时把恢复指引原样告诉用户，不要编。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库短名或 owner/name，必须是当前 GitHub 账号下的未归档仓库。例如 Atlas 或 liukun158959-beep/tebb。",
            }
        },
        "required": ["repo"],
    },
    "isReadOnly": True,
}

RECENT_SPEC = {
    "func": github_recent_tool,
    "name": "github_recent",
    "description": (
        "拉取某仓库近 14 天的 commit、PR、issue。"
        "用户要总结某仓库最近、帮我回顾 GitHub、最近改了什么时必须先调用，再 read_skill github-repo-summary。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "仓库短名或 owner/name，必须是当前 GitHub 账号下的未归档仓库。",
            }
        },
        "required": ["repo"],
    },
    "isReadOnly": True,
}
