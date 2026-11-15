"""离线验证 base_url_no_proxy：主 / 子 agent 可各自选择 base_url 是否走全局代理。

验证内容：
1. ModelConfig 默认 base_url_no_proxy=False（旧配置兼容）；
2. 开启时 _init_model 生成的 ChatOpenAI 持有 trust_env=False 的 httpx client，
   关闭时保持默认（http_client/http_async_client 为 None，由 openai 默认 client 处理）；
3. 真实代理环境（HTTP(S)_PROXY 指向不可达地址）下：
   - trust_env=True 的 httpx client 请求本地端点失败（确实走了代理）；
   - trust_env=False 的 httpx client 请求成功（绕过了代理）；
4. 端到端：同一个本地 OpenAI 兼容端点，
   - 主 agent 配置（base_url_no_proxy=False）调用失败；
   - 子 agent 配置（base_url_no_proxy=True）调用成功；
   证明主 / 子 agent 可以一个走代理、一个不走。

全部离线：本地起一个假 OpenAI 端点，不连真实模型、不碰数据库。

运行：
    venv\\Scripts\\python.exe scripts\\verify_base_url_no_proxy.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config.models import ModelConfig  # noqa: E402
from app.runtime.graph_builder import _init_model  # noqa: E402

PROXY_KEYS = [
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
    "NO_PROXY", "no_proxy",
]


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """最小 OpenAI Chat Completions 响应，只用于验证请求是否真的到达本地。"""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        payload = {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "created": 0,
            "model": "local-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "local-ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: ANN002
        pass


def _set_bad_proxy_env() -> None:
    """把代理指向不可达端口，模拟「开了全局代理但代理连不上/不该走」的环境。"""
    for key in PROXY_KEYS:
        os.environ.pop(key, None)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ[key] = "http://127.0.0.1:1"


def _restore_env(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _make_local_model(base_url: str, no_proxy: bool):
    cfg = ModelConfig(
        provider_mode="openai_compatible",
        provider_name="local",
        base_url=base_url,
        base_url_no_proxy=no_proxy,
    )
    return _init_model("local-model", "test-key", cfg)


def main() -> None:
    # --- 1. 默认值：旧配置没有该字段时保持「走默认代理逻辑」 ---
    assert ModelConfig().base_url_no_proxy is False, "默认应为 False"
    print("[1] ModelConfig 默认 base_url_no_proxy=False OK")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    saved = {key: os.environ.get(key) for key in PROXY_KEYS}
    try:
        _set_bad_proxy_env()

        # --- 2. client 属性：开启时两个方向都 trust_env=False；关闭时交给默认实现 ---
        proxied = _make_local_model(base_url, no_proxy=False)
        direct = _make_local_model(base_url, no_proxy=True)

        assert proxied.http_client is None and proxied.http_async_client is None, (
            "未开启时不应注入自定义 client"
        )
        assert direct.http_client is not None, "开启后应注入同步 client"
        assert direct.http_async_client is not None, "开启后应注入异步 client"
        assert direct.http_client._trust_env is False, "同步 client 必须 trust_env=False"
        assert direct.http_async_client._trust_env is False, "异步 client 必须 trust_env=False"
        print("[2] 开启时注入 trust_env=False 的 client，未开启时保持默认 OK")

        # --- 3. httpx 层：默认 client 走代理失败，trust_env=False 直连成功 ---
        proxy_error = None
        try:
            with httpx.Client(timeout=3) as client:
                client.post(f"{base_url}/chat/completions", json={})
        except Exception as exc:  # noqa: BLE001
            proxy_error = exc
        assert proxy_error is not None, "默认 client 应该因为走代理而失败"

        with httpx.Client(trust_env=False, timeout=3) as client:
            resp = client.post(f"{base_url}/chat/completions", json={})
        assert resp.status_code == 200, resp.text
        print("[3] httpx 默认 client 走代理失败、trust_env=False 直连成功 OK")

        # --- 4. 端到端：同一本地端点，主 agent 走代理失败、子 agent 不走成功 ---
        main_failed = False
        try:
            proxied.invoke("hi")
        except Exception:  # noqa: BLE001
            main_failed = True
        assert main_failed, "主 agent（走代理）应连接失败"

        reply = direct.invoke("hi")
        assert reply.content == "local-ok", f"子 agent（不走代理）应拿到本地回复，实际 {reply.content!r}"
        print("[4] 同一端点：主 agent 走代理失败、子 agent 不走代理成功 OK")

        print("ALL OK")
    finally:
        _restore_env(saved)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
