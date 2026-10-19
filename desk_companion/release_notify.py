"""正式 Release 图文通知：发布说明中的非敏感回执跨 runner 保留，未知发送结果先核实。"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
import urllib.request
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError

from .release_info import REPOSITORY, safe_release_url, version_tuple

# Keep the original identity across repository renames so pending retries use the same UUID.
NOTIFICATION_NAMESPACE = "liukun158959-beep/ZhiXing"


class RemoteError(RuntimeError):
    def __init__(self, message, confirmed=False):
        super().__init__(message)
        self.confirmed = confirmed


def json_request(url, *, method="GET", data=None, token="", content_type="application/json"):
    headers = {"User-Agent": "tebb release notification", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = content_type
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=30) as response:
            value = json.loads(response.read(1_000_000))
    except HTTPError as exc:
        try:
            value = json.loads(exc.read(1_000_000))
        except (ValueError, OSError):
            value = {}
        if not value.get("code"):
            raise RemoteError(f"平台请求被拒绝（HTTP {exc.code}），请检查发布权限和配置。",
                              confirmed=400 <= exc.code < 500) from None
    except Exception:
        raise RemoteError("平台请求结果未确认，未自动重复发送；请检查发布任务并补跑。") from None
    if value.get("code", 0) != 0:
        code = value["code"]
        raise RemoteError(f"飞书请求被拒绝（错误码 {code}），请检查应用权限、目标群和卡片格式。",
                          confirmed=isinstance(code, int) and code > 0 and code not in {500, 502, 503, 504})
    return value


def release_card(manifest, image_key, url, *, legacy=False):
    if not safe_release_url(url):
        raise ValueError("更新卡片链接不是项目发布页。")
    def md(value):
        text = escape(str(value), quote=False)
        for ch in "*_[]": text = text.replace(ch, f"&#{ord(ch)};")
        return text
    # Old manifests and posters retain their original published brand.
    heading = manifest.get("product_name", "知行 · ZhiXing") + " " + manifest["tag"]
    paragraphs = [{"tag": "markdown", "content": f"**{md(item['title'])}**\n{md(item['description'])}"}
                  for item in manifest["highlights"]]
    buttons = [{"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": kind,
                "behaviors": [{"type": "open_url", "default_url": href}]} for label, kind, href in
               (("下载新版", "primary_filled", url), ("完整更新日志", "default", url))]
    def group(elements, background="default"):
        return {"tag": "column_set", "flex_mode": "none", "columns": [{"tag": "column", "width": "weighted", "weight": 1,
                "background_style": background, "padding": "12px", "elements": elements}]}
    if legacy:
        # 仅确定的卡片格式拒绝后可降级，网络超时不能另发第二张。
        return {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": heading}, "template": "blue"},
                "elements": [{"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": manifest["title"]}},
                {"tag": "div", "text": {"tag": "lark_md", "content": "**"+md(manifest["summary"])+"**"}},
                *[{"tag": "div", "text": {"tag": "lark_md", "content": p["content"]}} for p in paragraphs],
                {"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "下载新版与完整日志"}, "type": "primary", "url": url}]}]}
    return {"schema": "2.0", "config": {"update_multi": True, "width_mode": "default", "summary": {"content": heading+" · 更新一览"}},
            "header": {"title": {"tag": "plain_text", "content": heading}, "subtitle": {"tag": "plain_text", "content": manifest["date"]+" · 正式版本更新一览"}, "template": "blue"},
            "body": {"direction": "vertical", "padding": "12px", "vertical_spacing": "12px", "elements": [
                group([{"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": manifest["title"]}, "scale_type": "fit_horizontal"},
                       {"tag": "markdown", "text_size": "heading-3", "content": "**"+md(manifest["summary"])+"**"}], "blue-50"),
                group(paragraphs), group([{"tag": "markdown", "text_size": "notation", "content": "<font color='grey'>Windows 便携版 · 关闭旧程序后运行新版，个人数据保留。</font>"},
                      {"tag": "column_set", "flex_mode": "none", "columns": [{"tag": "column", "width": "weighted", "weight": 1, "elements": [b]} for b in buttons]}])]}}


def receipt_pattern(chat_id):
    target = hashlib.sha256(chat_id.encode()).hexdigest()[:16]
    return re.compile(r"<!-- desk-companion-feishu:" + target + r":(\{[^\n]*\}) -->")


def receipt(body, chat_id):
    match = receipt_pattern(chat_id).search(body)
    if not match: return {}
    try: return json.loads(match.group(1))
    except ValueError: raise RemoteError("发布通知回执无效，请先核实群消息。") from None


class Notifier:
    def __init__(self, github_token, app_id, secret, chat_id, request=json_request):
        self.github_token, self.app_id, self.secret, self.chat_id = github_token, app_id, secret, chat_id
        self.request, self.token = request, ""
        if not all((github_token, app_id, secret, chat_id)):
            raise ValueError("请配置 GitHub Token 和飞书发布通知 Secrets。")

    def github(self, path, method="GET", data=None):
        return self.request(f"https://api.github.com/repos/{REPOSITORY}/"+path, method=method, data=data, token=self.github_token)

    def feishu(self, path, method="GET", data=None, content_type="application/json"):
        return self.request("https://open.feishu.cn/open-apis/"+path, method=method, data=data, token=self.token, content_type=content_type)

    def authenticate(self):
        result = self.feishu("auth/v3/tenant_access_token/internal", "POST", {"app_id": self.app_id, "app_secret": self.secret})
        self.token = result.get("tenant_access_token", "")
        if not self.token: raise RemoteError("未取得机器人发送身份，请检查发布通知凭据。")

    def record(self, release, state, started=None):
        body = release.get("body") or ""
        previous = receipt(body, self.chat_id)
        payload = {"tag": release["tag_name"], "state": state, "started": started or previous.get("started") or int(time.time())}
        # 不在公开 Release 中保存凭据、会话 ID、图片 key 或消息 ID。
        marker = receipt_pattern(self.chat_id).pattern.split(":")[1]
        body = receipt_pattern(self.chat_id).sub("", body).rstrip()
        body += "\n\n<!-- desk-companion-feishu:"+marker+":"+json.dumps(payload, separators=(",", ":"))+" -->"
        result = self.github(f"releases/{release['id']}", "PATCH", {"body": body})
        release["body"] = result.get("body", body)

    def verify_pending(self, release, started):
        bot = self.feishu("bot/v3/info").get("bot", {})
        identities = {self.app_id, bot.get("open_id"), bot.get("app_id")}-{None, ""}
        page = ""
        for _ in range(30):
            params = {"container_id_type": "chat", "container_id": self.chat_id, "start_time": str(max(0, int(started)-60)),
                      "sort_type": "ByCreateTimeAsc", "page_size": 50, "card_msg_content_type": "user_card_content"}
            if page: params["page_token"] = page
            data = self.feishu("im/v1/messages?"+urlencode(params)).get("data", {})
            for message in data.get("items", []):
                sender = message.get("sender", {})
                if sender.get("id") in identities and message.get("msg_type") == "interactive" and not message.get("deleted"):
                    body = json.dumps(message.get("body", {}), ensure_ascii=False)
                    if re.search(r"(?:tebb|ZhiXing|DeskCompanion) " + re.escape(release["tag_name"]) + r"(?![\w.])", body, re.I): return True
            if not data.get("has_more"): return False
            next_page = data.get("page_token")
            if not next_page or next_page == page: break
            page = next_page
        raise RemoteError("群消息核实未完成，保留待确认回执；请人工检查后补跑。")

    def upload_image(self, path):
        image = path.read_bytes()
        if not image.startswith(b"\x89PNG\r\n\x1a\n"): raise ValueError("版本主题图不是有效 PNG。")
        boundary = "desk-release-"+uuid.uuid4().hex
        data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image_type\"\r\n\r\nmessage\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"release.png\"\r\nContent-Type: image/png\r\n\r\n").encode()+image+f"\r\n--{boundary}--\r\n".encode()
        result = self.feishu("im/v1/images", "POST", data, "multipart/form-data; boundary="+boundary)
        return result["data"]["image_key"]

    def send(self, release, manifest, image, *, reconcile="auto"):
        version_tuple(release["tag_name"])
        if release.get("draft") or release.get("prerelease") or not release.get("published_at"):
            raise ValueError("仅正式发布成功的版本可以发送通知。")
        if release["tag_name"] != manifest["tag"]:
            raise ValueError("版本说明与 GitHub Release 不一致。")
        if not safe_release_url(release.get("html_url") or "", tag=release["tag_name"]):
            raise ValueError("版本通知链接与正式 tag 不一致。")
        previous = receipt(release.get("body") or "", self.chat_id)
        if previous and previous.get("tag") != release["tag_name"]:
            raise RemoteError("通知回执与版本不一致，未发送。")
        if previous and previous.get("state") not in {"sent", "pending", "retryable"}:
            raise RemoteError("通知回执状态无效，未发送。")
        if reconcile not in {"auto", "confirmed_absent", "confirmed_sent"}:
            raise ValueError("未知的人工核实选项。")
        if previous.get("state") == "sent": return {"state": "already_sent"}
        if reconcile != "auto":
            if previous.get("state") != "pending":
                raise ValueError("人工核实仅适用于发送结果未确认的 pending 回执。")
            if reconcile == "confirmed_sent":
                self.record(release, "sent")
                return {"state": "manually_verified_sent"}
        self.authenticate()
        started = previous.get("started") or int(time.time())
        if previous.get("state") == "pending" and time.time()-started >= 3500:
            if reconcile == "auto" and self.verify_pending(release, started):
                self.record(release, "sent", started)
                return {"state": "verified_sent"}
            # API 完整核实或操作者明确确认没有旧消息后，才开始新的幂等窗口。
            started = int(time.time())
        key = self.upload_image(image)
        self.record(release, "pending", started)
        message_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{NOTIFICATION_NAMESPACE}:{release['tag_name']}:{self.chat_id}"))
        for legacy in (False, True):
            card = release_card(manifest, key, release["html_url"], legacy=legacy)
            try:
                result = self.feishu("im/v1/messages?receive_id_type=chat_id", "POST", {
                    "receive_id": self.chat_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False), "uuid": message_uuid})
            except RemoteError as exc:
                if exc.confirmed:
                    self.record(release, "retryable", started)
                    if not legacy and "230099" in str(exc):
                        self.record(release, "pending", started)
                        continue
                raise
            mid = result.get("data", {}).get("message_id")
            if not mid: raise RemoteError("消息发送没有确认回执，请核实后补跑。")
            self.record(release, "sent", started)
            return {"state": "sent", "message_id": mid}
        raise RemoteError("版本卡片未发送，请查看发布任务。")
