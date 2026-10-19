import json
import io
import tempfile
import time
import unittest
import tomllib
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from desk_companion import release_info as info
from desk_companion import release_notify as notices


class UpdateTests(unittest.TestCase):
    def response(self, value):
        result = Mock()
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=False)
        result.read.return_value = json.dumps(value).encode()
        return result

    def test_renamed_repository_and_legacy_urls_keep_exact_release_boundary(self):
        self.assertEqual(info.REPOSITORY, "liukun158959-beep/tebb")
        for repository in ("liukun158959-beep/tebb", "liukun158959-beep/ZhiXing"):
            url = f"https://github.com/{repository}/releases/tag/v0.3.0"
            self.assertTrue(info.safe_release_url(url, tag="v0.3.0"))
            self.assertFalse(info.safe_release_url(url, tag="v0.3.1"))
            release = {"tag_name": "v0.3.0", "html_url": url}
            with patch.object(info.urllib.request, "urlopen", return_value=self.response(release)) as request:
                self.assertTrue(info.check_updates()["ok"])
            self.assertEqual(request.call_args.args[0].full_url,
                             "https://api.github.com/repos/liukun158959-beep/tebb/releases/latest")
        for url in ("https://github.com/liukun158959-beep/tebb-other/releases/tag/v0.3.0",
                    "https://github.com/other/tebb/releases/tag/v0.3.0",
                    "https://github.com.evil.invalid/liukun158959-beep/tebb/releases/tag/v0.3.0",
                    "https://user@github.com/liukun158959-beep/tebb/releases/tag/v0.3.0",
                    "http://github.com/liukun158959-beep/tebb/releases/tag/v0.3.0",
                    info.RELEASES_URL + "/tag/v0.3.0?download=1",
                    info.RELEASES_URL + "/tag/v0.3.0#fragment",
                    info.RELEASES_URL + "/other/tag/v0.3.0",
                    info.RELEASES_URL + "/tag/v0.3.0/extra"):
            with self.subTest(url=url):
                self.assertFalse(info.safe_release_url(url, tag="v0.3.0"))

    def test_legacy_latest_redirect_is_accepted_only_for_exact_version_path(self):
        response = self.response({})
        opener = Mock()
        opener.open.return_value = response
        for path, valid in (("tag/v0.3.0", True), ("other/tag/v0.3.0", False)):
            response.geturl.return_value = "https://github.com/liukun158959-beep/ZhiXing/releases/" + path
            with patch.object(info.urllib.request, "urlopen", side_effect=TimeoutError), \
                 patch.object(info.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(info.check_updates()["ok"], valid)

    def test_stable_release_comparison_and_untrusted_links(self):
        current = info.load_manifest()["version"].split(".")
        newer = "v" + ".".join([*current[:-1], str(int(current[-1]) + 1)])
        base = {"draft": False, "prerelease": False, "tag_name": newer, "html_url": info.RELEASES_URL+"/tag/"+newer, "body": "更新说明<!-- hidden receipt -->"}
        with patch.object(info.urllib.request, "urlopen", return_value=self.response(base)):
            result = info.check_updates()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "available")
        self.assertNotIn("hidden", result["notes"])
        for change in ({"prerelease": True}, {"draft": True}, {"html_url": "https://github.com.evil.invalid/releases/v0.3.0"}, {"tag_name": "nightly"}):
            with patch.object(info.urllib.request, "urlopen", return_value=self.response({**base, **change})):
                self.assertFalse(info.check_updates()["ok"])

    def test_rate_limit_uses_formal_release_redirect_not_unpublished_main(self):
        opener = Mock()
        response = self.response({})
        response.geturl.return_value = info.RELEASES_URL+"/tag/v0.2.1"
        opener.open.return_value = response
        with patch.object(info.urllib.request, "urlopen", side_effect=HTTPError("api",403,"limit",{},None)), \
             patch.object(info.urllib.request, "build_opener", return_value=opener):
            result = info.check_updates()
        self.assertTrue(result["ok"])
        self.assertEqual(result["latest_version"], "0.2.1")
        self.assertEqual(result["status"], "ahead")
        with self.assertRaises(ValueError):
            info.ReleaseRedirect().redirect_request(Mock(),None,302,"",{},"https://example.invalid/update")

    def test_release_version_and_image_are_packaged_consistently(self):
        root = Path(__file__).resolve().parents[1]
        manifest = info.load_manifest()
        self.assertTrue((info.MANIFEST.parent/manifest["image"]).is_file())
        for file in ("client/package.json", "client/src-tauri/tauri.conf.json"):
            self.assertEqual(json.loads((root/file).read_text(encoding="utf-8"))["version"], manifest["version"])
        for file, section in (("pyproject.toml", "project"), ("client/src-tauri/Cargo.toml", "package")):
            self.assertEqual(tomllib.loads((root/file).read_text(encoding="utf-8"))[section]["version"], manifest["version"])


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name)/"poster.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        self.manifest = info.load_manifest()
        self.release = {"id": 123, "body": "发行说明", "tag_name": self.manifest["tag"], "published_at": "2026-10-08T00:00:00Z",
                        "draft": False, "prerelease": False, "html_url": info.RELEASES_URL+"/tag/"+self.manifest["tag"]}
        self.calls=[]
        def request(url, **kwargs):
            self.calls.append((url,kwargs))
            if "auth/v3" in url: return {"tenant_access_token":"test-token"}
            if url.endswith("/images"): return {"data":{"image_key":"test-image"}}
            if "im/v1/messages?receive_id_type" in url: return {"data":{"message_id":"test-message"}}
            if "api.github.com" in url: return {"body":kwargs["data"]["body"]}
            self.fail(url)
        self.notify = notices.Notifier("test-gh", "test-app", "test-secret", "test-chat", request=request)

    def test_sent_receipt_survives_new_runner_and_does_not_expose_ids(self):
        result = self.notify.send(self.release,self.manifest,self.image)
        self.assertEqual(result["state"],"sent")
        self.assertEqual(notices.receipt(self.release["body"],"test-chat")["state"],"sent")
        for secret in ("test-secret","test-chat","test-message","test-image"):
            self.assertNotIn(secret,self.release["body"])
        self.calls.clear()
        self.assertEqual(self.notify.send(self.release,self.manifest,self.image)["state"],"already_sent")
        self.assertFalse(self.calls)

    def test_http_permission_error_is_confirmed_and_does_not_expose_response(self):
        response = HTTPError("https://open.feishu.cn/example", 400, "bad request", {},
                             io.BytesIO(b'{"code":230027,"msg":"sensitive-fixture"}'))
        with patch.object(notices.urllib.request, "urlopen", side_effect=response):
            with self.assertRaises(notices.RemoteError) as error:
                notices.json_request("https://open.feishu.cn/example")
        self.assertTrue(error.exception.confirmed)
        self.assertIn("230027", str(error.exception))
        self.assertNotIn("sensitive-fixture", str(error.exception))

    def test_timeout_keeps_pending_and_retries_with_same_uuid(self):
        original=self.notify.request
        def fail_message(url,**kwargs):
            if "receive_id_type" in url:
                self.calls.append((url,kwargs))
                raise notices.RemoteError("网络结果未知")
            return original(url,**kwargs)
        self.notify.request=fail_message
        with self.assertRaises(notices.RemoteError): self.notify.send(self.release,self.manifest,self.image)
        self.assertEqual(notices.receipt(self.release["body"],"test-chat")["state"],"pending")
        first=self.calls[-1][1]["data"]["uuid"]
        self.assertEqual(first, str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"liukun158959-beep/ZhiXing:{self.release['tag_name']}:test-chat")))
        self.notify.request=original
        self.notify.send(self.release,self.manifest,self.image)
        sent=[kw["data"]["uuid"] for url,kw in self.calls if "receive_id_type" in url]
        self.assertEqual(sent,[first,first])

    def test_legacy_release_url_keeps_receipt_and_new_api_repository(self):
        self.release["html_url"] = "https://github.com/liukun158959-beep/ZhiXing/releases/tag/" + self.manifest["tag"]
        self.assertEqual(self.notify.send(self.release, self.manifest, self.image)["state"], "sent")
        github_urls = [url for url, _ in self.calls if "api.github.com" in url]
        self.assertTrue(github_urls)
        self.assertTrue(all(url.startswith("https://api.github.com/repos/liukun158959-beep/tebb/") for url in github_urls))
        self.calls.clear()
        self.release["html_url"] = info.RELEASES_URL + "/tag/" + self.manifest["tag"]
        self.assertEqual(self.notify.send(self.release, self.manifest, self.image)["state"], "already_sent")
        self.assertFalse(self.calls)

    def test_old_pending_is_verified_before_resend(self):
        self.notify.record(self.release,"pending",int(time.time())-7200)
        with patch.object(self.notify,"verify_pending",return_value=True) as verify, patch.object(self.notify,"upload_image") as upload:
            self.assertEqual(self.notify.send(self.release,self.manifest,self.image)["state"],"verified_sent")
        verify.assert_called_once()
        upload.assert_not_called()

    def test_manual_reconciliation_requires_pending_and_never_resends_confirmed_sent(self):
        with self.assertRaises(ValueError):
            self.notify.send(self.release, self.manifest, self.image, reconcile="confirmed_absent")
        self.notify.record(self.release, "pending", int(time.time()) - 7200)
        with patch.object(self.notify, "verify_pending", side_effect=notices.RemoteError("permission denied")):
            self.assertEqual(self.notify.send(self.release, self.manifest, self.image, reconcile="confirmed_sent")["state"],
                             "manually_verified_sent")
        self.assertFalse(any("receive_id_type" in url for url, _ in self.calls))
        self.notify.record(self.release, "pending", int(time.time()) - 7200)
        with patch.object(self.notify, "verify_pending", side_effect=notices.RemoteError("permission denied")):
            self.assertEqual(self.notify.send(self.release, self.manifest, self.image, reconcile="confirmed_absent")["state"], "sent")

    def test_draft_prerelease_and_wrong_manifest_never_send(self):
        for change in ({"draft":True},{"prerelease":True},{"tag_name":"v0.0.1"},{"published_at":""},
                       {"html_url": info.RELEASES_URL + "/tag/v0.0.1"},
                       {"html_url": "https://github.com/other/tebb/releases/tag/" + self.manifest["tag"]}):
            with self.assertRaises(ValueError): self.notify.send({**self.release,**change},self.manifest,self.image)
        self.assertFalse(self.calls)

    def test_history_verification_matches_exact_version_and_requires_complete_pagination(self):
        def request(url, **kwargs):
            if "bot/v3" in url: return {"bot": {"open_id": "test-bot"}}
            return {"data": {"has_more": False, "items": [{"sender": {"id": "test-bot"}, "msg_type": "interactive",
                "body": {"content": "ZhiXing v0.2.20"}}]}}
        self.notify.request = request
        self.assertFalse(self.notify.verify_pending(self.release, 1))
        for brand in ("tebb", "ZhiXing", "DeskCompanion"):
            content = brand + " " + self.release["tag_name"]
            self.notify.request = lambda url, **kw: {"bot": {"open_id": "test-bot"}} if "bot/v3" in url else {
                "data": {"has_more": False, "items": [{"sender": {"id": "test-bot"}, "msg_type": "interactive",
                          "body": {"content": content}}]}}
            self.assertTrue(self.notify.verify_pending(self.release, 1), brand)
        self.notify.request = lambda url, **kw: {"bot": {"open_id": "test-bot"}} if "bot/v3" in url else {
            "data": {"has_more": True, "items": [], "page_token": "same"}}
        with self.assertRaises(notices.RemoteError): self.notify.verify_pending(self.release, 1)

    def test_card_has_image_five_updates_one_focal_title_and_download_links(self):
        card=notices.release_card(self.manifest,"image",self.release["html_url"])
        self.assertEqual(card["header"]["title"]["content"], self.manifest.get("product_name", "知行 · ZhiXing") + " " + self.manifest["tag"])
        historical = {k:v for k,v in self.manifest.items() if k != "product_name"}
        self.assertIn("ZhiXing", notices.release_card(historical,"image",self.release["html_url"])["header"]["title"]["content"])
        for legacy in (False, True):
            renamed = notices.release_card({**self.manifest, "product_name": "tebb"}, "image", self.release["html_url"], legacy=legacy)
            self.assertEqual(renamed["header"]["title"]["content"], "tebb " + self.manifest["tag"])
        blocks=card["body"]["elements"]
        self.assertEqual(len(blocks),3)
        self.assertEqual(blocks[0]["columns"][0]["elements"][0]["tag"],"img")
        self.assertEqual(len(blocks[1]["columns"][0]["elements"]),5)
        raw=json.dumps(card,ensure_ascii=False)
        self.assertEqual(raw.count('"heading-3"'),1)
        self.assertNotIn('"font_color"',raw)
        self.assertIn("下载新版",raw)
        self.assertIn("完整更新日志",raw)


if __name__=="__main__": unittest.main()
