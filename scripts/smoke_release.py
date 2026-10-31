"""用发布包的 Python 和真实 WS/API，在空白数据目录里验证配置到流式回复。"""
import argparse
import asyncio
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.asyncio.client import connect


class ModelServer(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(data)
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        self.send_response(200)
        if data.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta in [{"role": "assistant", "content": "发布包对话正常。"}, {}]:
                chunk = {"id": "smoke", "object": "chat.completion.chunk", "created": 1, "model": "release-smoke",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": "stop" if not delta else None}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
                if delta:
                    time.sleep(.5)
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "smoke", "object": "chat.completion", "created": 1,
                "model": "release-smoke", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "连通正常"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode())


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def check(port, model_port, root, restored=False,token='release-smoke',expected_guide_show=False):
    url = f"ws://127.0.0.1:{port}/ws?token={token}"

    async def rpc(method, args=None):
        async with connect(url, proxy=None) as ws:
            await ws.send(json.dumps({"type": "rpc", "id": method, "method": method, "args": args or {}}))
            envelope = json.loads(await asyncio.wait_for(ws.recv(), 30))["result"]
            assert envelope["ok"], envelope
            result = envelope["result"]
            assert result["ok"], result
            return result

    if restored:
        assert (await rpc("load_models"))["items"][0]["model"] == "release-smoke", "Saved model was not restored"
        assert (await rpc("load_model_network"))["mode"] == "direct", "Saved network mode was not restored"
        assert (await rpc("load_onboarding"))["show"] is expected_guide_show, "Saved onboarding progress differs from expected fixture"
        assert (await rpc("load_chat_log"))["items"], "Saved history was not restored"
        print("Restart preserves saved model, expected guide progress and history: OK")
        return
    assert not (await rpc("load_models"))["items"]
    setup = await rpc("load_onboarding")
    assert setup["show"] and setup["data_dir"] == str(root)
    assert setup["checks"]["pet"], "Every release includes the built-in circle pet"
    assert not setup["checks"]["live2d"], "Private Live2D assets must not ship"
    assert (await rpc("load_notebook"))["sessions"] == []
    await rpc("save_model_network", {"payload": {"mode": "direct"}})
    models = await rpc("save_model_entry", {"payload": {"base_url": f"http://127.0.0.1:{model_port}/v1",
                        "model": "release-smoke", "api_key": "release-smoke-only"}})
    assert "release-smoke-only" not in json.dumps(models)
    assert (root / "models.json").is_file() and (root / ".env").is_file()
    await rpc("test_model", {"payload": {"id": models["active"], "base_url": f"http://127.0.0.1:{model_port}/v1", "model": "release-smoke"}})
    async with connect(url, proxy=None) as ws:
        await ws.send(json.dumps({"type": "chat", "text": "验证发布包", "chips": {}, "model_id": models["active"],
                                 "sampling": {"reasoning_effort": "low", "temperature": 0.5, "top_p": 1}}))
        frames = []
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
            frames.append(msg)
            if msg["type"] == "token":
                in_flight = await rpc("list_debug_calls")
                assert any(call["state"] == "receiving" for call in in_flight["items"]), "Live SDK recording must remain queryable"
            if msg["type"] in {"done", "error"}:
                break
        assert frames[-1] == {"type": "done", "data": "发布包对话正常。"}, frames
        assert any(frame["type"] == "token" for frame in frames)
    assert ModelServer.requests[-1]["temperature"] == 0.5
    # Exercise the shipped SDK recorder, not a source-tree fixture.
    calls = (await rpc("list_debug_calls"))["items"]
    recorded = []
    for call in calls:
        detail = await rpc("get_debug_call", {"call_id": call["id"]})
        if any("验证发布包" in str(m.get("content") or "") for m in detail["request"].get("messages", [])):
            recorded.append(detail)
    assert recorded and recorded[0]["response_raw"]
    assert recorded[0]["response"]["choices"][0]["message"]["content"] == "发布包对话正常。"
    folder = root / "local-smoke"
    folder.mkdir()
    document = folder / "中文笔记.txt"
    document.write_text("发布包本地文件读取正常。", encoding="utf-8")
    staged = await rpc("stage_local_sources", {"paths": [str(document)], "kind": "file"})
    page = await rpc("read_local_source", {"source_id": staged["sources"][0]["id"]})
    assert "发布包本地文件读取正常。" in page["source"]["text"]
    grouped = await rpc("stage_local_sources", {"paths": [str(folder)], "kind": "folder"})
    assert grouped["sources"][0]["files"][0]["name"] == document.name
    print("Bundled full LLM request/response recording and local file/folder reading: OK")
    await rpc("complete_onboarding")
    assert not (await rpc("load_onboarding"))["show"]
    assert (await rpc("load_chat_log"))["items"]
    assert (await rpc("load_skills"))["items"]
    async with connect(url + "-wrong", proxy=None) as ws:
        from websockets.exceptions import ConnectionClosedError
        try:
            await asyncio.wait_for(ws.recv(), 5)
            raise AssertionError("错误 token 被接受")
        except ConnectionClosedError as exc:
            assert exc.rcvd.code == 4401
    assets = root / "assets/skins/kaltsit"
    assets.mkdir(parents=True)
    (assets / "kaltsit.model3.json").write_text("{}", encoding="utf-8")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/pet-assets/{token}/skins/kaltsit/kaltsit.model3.json") as response:
        assert response.status == 200 and response.read() == b"{}"
    print("Empty-profile startup, model save/test, bundled Atlas streaming/sampling, history and guide completion: OK")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    package = args.package.resolve()
    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelServer)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="desk-release-smoke-") as directory:
            root = Path(directory).resolve()
            port = free_port()
            env = dict(os.environ, DESK_COMPANION_DATA_DIR=str(root), DESK_COMPANION_ASSET_DIR=str(root / "assets"), PYTHONUTF8="1")
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            for restored in [False, True]:
                with (root / "smoke.log").open("ab") as log:
                    process = subprocess.Popen([str(package / "runtime/python.exe"), "-u", "-m", "desk_companion.local_api.server",
                                                "--port", str(port), "--token", "release-smoke"], cwd=root, env=env,
                                               stdout=log, stderr=log, creationflags=0x08000000)
                    try:
                        for _ in range(150):
                            if process.poll() is not None:
                                raise RuntimeError((root / "smoke.log").read_text(encoding="utf-8"))
                            try:
                                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                                    if response.status == 200:
                                        break
                            except OSError:
                                time.sleep(0.1)
                        else:
                            raise RuntimeError("发布包的本地后端未就绪")
                        asyncio.run(check(port, model.server_port, root, restored))
                    finally:
                        process.terminate()
                        process.wait(timeout=10)
    finally:
        model.shutdown()
        model.server_close()


if __name__ == "__main__":
    main()
