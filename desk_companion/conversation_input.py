"""Turn selection validation and prompt composition, shared by all entry points."""

CLI_CHIPS = (
    ("get_today_agenda", "今日日程"),
    ("get_open_tasks", "未完成待办"),
    ("github_status", "GitHub 状态"),
    ("github_recent", "GitHub 近况"),
    ("github_roadmap", "GitHub 路线图"),
    ("web_search", "联网搜索"),
    ("read_video", "视频信息与字幕"),
    ("read_video_transcript", "视频字幕原文"),
    ("execute_command", "沙箱执行命令"),
    ("read_terminal_output", "命令执行记录"),
)
NEED_REPO_TOOLS = {"github_recent", "github_roadmap"}


def compose_turn(text: str, chips: dict) -> str:
    body = (text or "").strip()
    skills = chips.get("skills") or []
    cli = chips.get("cli") or []
    github = chips.get("github") or ""
    docs = chips.get("docs") or []
    mcp = chips.get("mcp") or []
    attachments = chips.get("attachments") or []
    extra_keys = set(chips) - {"skills", "cli", "github", "retro", "docs", "mcp", "attachments"}
    if extra_keys:
        raise RuntimeError(f"不认识的点选：{', '.join(sorted(extra_keys))}。")
    if skills and (
        type(skills) is not list
        or any(type(x) is not str or not x.strip() for x in skills)
    ):
        raise RuntimeError("技能点选必须是非空字符串列表。")
    if cli and (
        type(cli) is not list or any(type(x) is not str or not x.strip() for x in cli)
    ):
        raise RuntimeError("CLI 点选必须是非空字符串列表。")
    if github and type(github) is not str:
        raise RuntimeError("仓库点选必须是字符串。")
    allowed_cli = {key for key, _label in CLI_CHIPS}
    for name in cli:
        if name not in allowed_cli:
            raise RuntimeError(f"没有 CLI 工具 {name!r}。")
    if any(name in NEED_REPO_TOOLS for name in cli) and not str(github).strip():
        raise RuntimeError("选了 GitHub 近况或路线图时必须同时选一个已列出的仓库。")
    _check_doc_picks(docs)
    _check_mcp_picks(mcp)
    if type(attachments) is not list or len(attachments) > 50 or any(not isinstance(x, str) for x in attachments):
        raise RuntimeError("本地附件必须为最多 50 个资料编号。")
    if not body and not skills and not cli and not github and not docs and not mcp and not attachments:
        raise RuntimeError("输入框是空的。")
    if not skills and not cli and not github and not docs and not mcp and not attachments:
        return body
    lines = ["【本轮指定】"]
    for name in skills:
        lines.append(
            f"- 技能 {name.strip()} 的正文已附在本轮【技能正文】，按正文执行，不要再调用 read_skill。"
        )
    for name in cli:
        lines.append(f"- 必须调用工具 {name.strip()}")
    if str(github).strip():
        lines.append(
            f"- GitHub 仓库 {github.strip()}；github_recent / github_roadmap 必须用这个仓库"
        )
    for doc in docs:
        lines.append(
            f"- 飞书文档《{_doc_title_link(doc)}》的正文已附在本轮【文档正文】，"
            "只根据正文回答，不要编文档里没有的内容。"
        )
    if attachments:
        from .local_sources import get
        for ident in attachments:
            source = get(ident)
            lines.append(f'- 本地附件《{source["name"]}》（编号 {ident}），资料见本轮【本地附件】。')
    from .mcp_client import mcp_fn_name

    for item in mcp:
        lines.append(f"- 必须调用工具 {mcp_fn_name(item['server'].strip(), item['tool'].strip())}")
    prefix = "\n".join(lines)
    if body:
        return prefix + "\n\n" + body
    return prefix + "\n\n按上面的指定执行。"



def _doc_title_link(doc: dict) -> str:
    """标题写成超链接。没有 http(s) 地址就只留标题，不编一个打不开的网址。"""
    label = doc["label"].strip().replace("[", "［").replace("]", "］")
    ident = doc["id"].strip()
    if not _openable_doc_url(ident):
        return label
    href = ident.replace("(", "%28").replace(")", "%29")
    return f"[{label}]({href})"


def _openable_doc_url(url: str) -> bool:
    if not url or len(url) > 2000 or any(ch.isspace() for ch in url):
        return False
    lower = url.lower()
    if not (lower.startswith("https://") or lower.startswith("http://")):
        return False
    rest = url.split("://", 1)[1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return bool(host) and "@" not in host


def _check_doc_picks(docs) -> None:
    if not docs:
        return
    if type(docs) is not list:
        raise RuntimeError("飞书文档点选必须是列表。")
    for item in docs:
        label = item.get("label") if isinstance(item, dict) else None
        ident = item.get("id") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or set(item) - {"id", "label"}
            or type(ident) is not str
            or not ident.strip()
            or any(ch.isspace() for ch in ident)
            or type(label) is not str
            or not label.strip()
        ):
            raise RuntimeError("飞书文档点选必须带文档地址和标题。")


def _check_mcp_picks(mcp) -> None:
    if not mcp:
        return
    if type(mcp) is not list:
        raise RuntimeError("MCP 点选必须是列表。")
    for item in mcp:
        server = item.get("server") if isinstance(item, dict) else None
        tool = item.get("tool") if isinstance(item, dict) else None
        label = item.get("label") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or set(item) - {"server", "tool", "label"}
            or type(server) is not str
            or not server.strip()
            or any(ch.isspace() for ch in server)
            or type(tool) is not str
            or not tool.strip()
            or any(ch.isspace() for ch in tool)
            or (label is not None and (type(label) is not str or not label.strip()))
        ):
            raise RuntimeError("MCP 点选必须带服务器名和工具名。")
