"""Shared catalog and diagnostic snapshots; independent of desktop hosts."""
from .conversation_input import CLI_CHIPS
from .usage import summarize_usage


def load_composer_options() -> dict:
    from .skill_catalog import list_skills

    try:
        skills = [
            {"id": item["id"], "label": item["id"], "description": item["description"]}
            for item in list_skills()
        ]
    except Exception as extra:
        return {"ok": False, "error": str(extra)}
    github = {"ok": True, "items": []}
    try:
        from .github import list_owned_repos

        github = {
            "ok": True,
            "items": [
                {"id": row["full_name"], "label": row["full_name"]}
                for row in list_owned_repos()
            ],
        }
    except Exception as extra:
        github = {"ok": False, "error": str(extra), "items": []}
    return {
        "ok": True,
        "skills": skills,
        "cli": [{"id": key, "label": label} for key, label in CLI_CHIPS],
        "github": github,
    }


def list_mcp_tools() -> dict:
    from .mcp_client import list_mcp_menu

    return list_mcp_menu()


def list_feishu_docs() -> dict:
    from .feishu_docs import list_my_docx
    from .wiki_connection import catalog, load_settings, enrich_catalog
    items, errors = [], []
    for get in (list_my_docx, catalog):
        try:
            items.extend(get())
        except Exception as extra:
            items.extend(getattr(extra, 'items', []))
            errors.append(str(extra))
    unique = {item["token"]: item for item in items}
    grouped = enrich_catalog(list(unique.values())) if unique else []
    return {"ok": bool(items) or not errors, "items": grouped,
            "error": "\n".join(errors), "wiki_connected": bool(load_settings().get("wiki_url"))}


def load_log_errors() -> dict:
    from .log_inspect import inspect_today_errors

    try:
        return inspect_today_errors()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def load_skills() -> dict:
    from .skill_catalog import list_skills

    try:
        return {"ok": True, "items": list_skills()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def load_usage() -> dict:
    try:
        data = summarize_usage()
        data["ok"] = True
        return data
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def load_github() -> dict:
    from .github import board_snapshot

    try:
        return board_snapshot()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
