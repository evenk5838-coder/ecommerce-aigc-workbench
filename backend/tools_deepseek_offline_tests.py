#!/usr/bin/env python3
"""DeepSeek 通道 · 离线测试（零网络、零费用、零业务库写入）。

覆盖：
1. 请求确实发往 DeepSeek 官方（host + path）
2. 使用 DEEPSEEK_API_KEY，未误用 ARK_API_KEY
3. 非思考模式参数正确（thinking.type=disabled）
4. 对话与工具调用/工具结果解析正确
5. 配置不完整 → 明确报错（不是"Key 有问题"）
6. 错误分类：Key 无效 / 余额不足 / 模型不可用 / 超时 / 网络
7. Ark 通道未受影响（仍用 ARK Key 与方舟地址）
8. 配置写入：保留其它配置、幂等、权限 600、Key 不回显
9. 原生图/视频手动路径不受影响

用法：<venv>/bin/python backend/tools_deepseek_offline_tests.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import traceback
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, db, main, providers_ark, worker  # noqa: E402
from app.assistant import provider as prov, service, tools  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "deepseek接入-20261024"
REF_IMG = "asset_d73231ab405540b68eb6"

_ORIG_DS_POST = prov._deepseek_post          # 保存真实实现，供错误映射用例恢复

ARK_KEY = "ark-FAKEKEY-for-offline-test-only"
DS_KEY = "sk-FAKEKEY-for-offline-test-only"


def case(name: str, fn) -> None:
    try:
        d = fn() or {}
        RESULTS.append({"case": name, "ok": True, "detail": d})
        print(f"  OK  {name}" + (f"  {d}" if d else ""))
    except AssertionError as e:
        RESULTS.append({"case": name, "ok": False, "error": str(e)})
        print(f" FAIL {name} → {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"case": name, "ok": False, "error": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-500:]})
        print(f" ERR  {name} → {type(e).__name__}: {e}")


def expect(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http, f"期望 HTTP {http}，实际 {e.http}"
        return {"code": e.code, "http": e.http, "message": e.message[:70]}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def setup_env() -> None:
    os.environ["ASSISTANT_PROVIDER"] = "deepseek"
    os.environ["DEEPSEEK_API_KEY"] = DS_KEY
    os.environ["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
    os.environ["ASSISTANT_MODEL"] = "deepseek-flash"
    os.environ["ARK_API_KEY"] = ARK_KEY
    os.environ["ARK_BASE_URL"] = "https://ark.cn-beijing.volces.com/api/v3"
    os.environ.pop("ASSISTANT_MAX_TOKENS", None)


def make_db_copy() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ds-offline-"))
    shutil.copy2(config.DB_PATH, tmp / "app.db")
    config.DB_PATH = tmp / "app.db"
    db.init_db()

    def _guard(*a, **k):
        raise AssertionError("离线测试禁止发起真实网络请求")
    providers_ark._request = _guard  # type: ignore[assignment]
    return tmp


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    setup_env()
    tmp = make_db_copy()
    print(f"隔离库：{tmp / 'app.db'}（真实库未改）\n")

    print("=== 1. 请求发往 DeepSeek 官方，且只用 DeepSeek Key ===")

    def request_target_and_key():
        captured = {}

        def fake_post(url, body, api_key, timeout):  # noqa: ANN001
            captured.update({"url": url, "body": body, "key": api_key, "timeout": timeout})
            return {"choices": [{"message": {"content": "可用"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3}}
        prov._deepseek_post = fake_post  # type: ignore[assignment]
        p = prov.DeepSeekChatProvider()
        r = p.chat([{"role": "user", "content": "只回复两个字：可用"}], [])
        assert captured["url"] == "https://api.deepseek.com/chat/completions", captured["url"]
        assert captured["key"] == DS_KEY, "未使用 DEEPSEEK_API_KEY"
        assert captured["key"] != ARK_KEY, "误用了 ARK_API_KEY"
        assert captured["body"]["model"] == "deepseek-flash", captured["body"]["model"]
        assert r.reply == "可用" and r.input_tokens == 12
        return {"url": captured["url"], "model": captured["body"]["model"],
                "key_source": "DEEPSEEK_API_KEY", "reply": r.reply}
    case("URL=api.deepseek.com/chat/completions，Key=DEEPSEEK_API_KEY", request_target_and_key)

    def non_thinking_and_max_tokens():
        captured = {}

        def fake_post(url, body, api_key, timeout):  # noqa: ANN001
            captured.update(body)
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}
        prov._deepseek_post = fake_post  # type: ignore[assignment]
        os.environ["ASSISTANT_MAX_TOKENS"] = "32"
        try:
            prov.DeepSeekChatProvider().chat([{"role": "user", "content": "x"}], [])
        finally:
            os.environ.pop("ASSISTANT_MAX_TOKENS", None)
        assert captured["thinking"] == {"type": "disabled"}, captured.get("thinking")
        assert captured.get("max_tokens") == 32, captured.get("max_tokens")
        assert "stream" not in captured or captured["stream"] is False
        return {"thinking": captured["thinking"], "max_tokens": captured["max_tokens"]}
    case("非思考模式（thinking=disabled）+ max_tokens 可控", non_thinking_and_max_tokens)

    def context_role_folded():
        captured = {}

        def fake_post(url, body, api_key, timeout):  # noqa: ANN001
            captured.update(body)
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}
        prov._deepseek_post = fake_post  # type: ignore[assignment]
        prov.DeepSeekChatProvider().chat(
            [{"role": "system", "content": "s"},
             {"role": "context", "content": {"intent": "generate"}},
             {"role": "user", "content": "hi"}], [])
        roles = [m["role"] for m in captured["messages"]]
        assert "context" not in roles, roles
        assert all(r in ("system", "user", "assistant", "tool") for r in roles), roles
        return {"roles": roles}
    case("内部 context 角色折叠为 system（DeepSeek 只认标准 role）", context_role_folded)

    def tool_calls_parse():
        def fake_post(url, body, api_key, timeout):  # noqa: ANN001
            return {"choices": [{"message": {"content": "",
                                             "tool_calls": [{"id": "call_x1", "type": "function",
                                                             "function": {"name": "list_models",
                                                                          "arguments": '{"kind":"image"}'}}]},
                                 "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 8}}
        prov._deepseek_post = fake_post  # type: ignore[assignment]
        r = prov.DeepSeekChatProvider().chat([{"role": "user", "content": "有哪些图片模型"}],
                                             tools.tool_specs())
        assert len(r.tool_calls) == 1
        tc = r.tool_calls[0]
        assert (tc.name, tc.arguments, tc.id) == ("list_models", {"kind": "image"}, "call_x1"), tc
        return {"tool": tc.name, "args": tc.arguments, "id": tc.id}
    case("工具调用解析（name/arguments/id）", tool_calls_parse)

    def tool_result_roundtrip():
        captured = {}

        def fake_post(url, body, api_key, timeout):  # noqa: ANN001
            captured.update(body)
            if "tools" in body and not any(m["role"] == "tool" for m in body["messages"]):
                return {"choices": [{"message": {"content": "",
                                                 "tool_calls": [{"id": "call_y1", "type": "function",
                                                                 "function": {"name": "list_templates",
                                                                              "arguments": "{}"}}]},
                                     "finish_reason": "tool_calls"}], "usage": {}}
            return {"choices": [{"message": {"content": "共 4 个模板"}, "finish_reason": "stop"}],
                    "usage": {}}
        prov._deepseek_post = fake_post  # type: ignore[assignment]
        s = service.ensure_session(None, "p-ds", "generate")
        stub = prov.DeepSeekChatProvider()
        out = service.chat(session_id=s["id"], project_id="p-ds",
                           message="有哪些模板", provider=stub)
        msgs = captured["messages"]
        asst = [m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")]
        tmsg = [m for m in msgs if m.get("role") == "tool"]
        assert asst and tmsg, msgs
        assert tmsg[0]["tool_call_id"] == "call_y1", tmsg[0]
        assert out["tool_calls"] and out["tool_calls"][0]["ok"] is True
        return {"tool_call_id": tmsg[0]["tool_call_id"], "reply": out["reply"]}
    case("工具结果按 role=tool(tool_call_id) 回传并继续对话", tool_result_roundtrip)

    print("\n=== 2. 配置不完整 → 如实报错 ===")

    def missing_key():
        p = prov.DeepSeekChatProvider(api_key="")
        d = expect("ASSISTANT_CONFIG_INCOMPLETE",
                   lambda: p.chat([{"role": "user", "content": "x"}], []), 400)
        assert "DEEPSEEK_API_KEY" in json.dumps(d, ensure_ascii=False)
        assert p.available() is False
        return d
    case("缺 Key → ASSISTANT_CONFIG_INCOMPLETE（而非 Key 无效）", missing_key)

    def missing_model():
        p = prov.DeepSeekChatProvider(api_key=DS_KEY, model=" ")
        d = expect("ASSISTANT_CONFIG_INCOMPLETE",
                   lambda: p.chat([{"role": "user", "content": "x"}], []), 400)
        return d
    case("缺型号 → ASSISTANT_CONFIG_INCOMPLETE", missing_model)

    def status_incomplete():
        saved = os.environ.pop("DEEPSEEK_API_KEY")
        try:
            st = prov.provider_status()
        finally:
            os.environ["DEEPSEEK_API_KEY"] = saved
        assert st["state"] == "misconfigured", st
        assert "DEEPSEEK_API_KEY" in st["note"], st["note"]
        assert st["verified"] is False
        return {"state": st["state"], "note": st["note"][:40]}
    case("/status 在缺 Key 时报 misconfigured（附缺失项）", status_incomplete)

    print("\n=== 3. 错误分类（Key/余额/模型/超时/网络）===")

    import io as _io
    import urllib.request as _ur

    def with_urlopen(raiser, fn):
        """替换 urllib.request.urlopen（真正走 _deepseek_post 的错误映射代码）。"""
        prov._deepseek_post = _ORIG_DS_POST     # 先恢复真实实现（前面用例替换过）
        orig = _ur.urlopen
        _ur.urlopen = raiser
        try:
            return fn()
        finally:
            _ur.urlopen = orig

    def _http_raiser(code: int, body: dict):
        def raiser(req, timeout=None):  # noqa: ANN001
            raise urllib.error.HTTPError(getattr(req, "full_url", "https://api.deepseek.com"),
                                         code, "err", {},
                                         _io.BytesIO(json.dumps(body).encode()))
        return raiser

    def err_key():
        return with_urlopen(
            _http_raiser(401, {"error": {"message": "Authentication Fails, Your api key is invalid"}}),
            lambda: expect("ASSISTANT_KEY_INVALID",
                           lambda: prov.DeepSeekChatProvider().chat(
                               [{"role": "user", "content": "x"}], []), 401))
    case("401 → ASSISTANT_KEY_INVALID（并提示 Key）", err_key)

    def err_balance():
        return with_urlopen(
            _http_raiser(402, {"error": {"message": "Insufficient Balance"}}),
            lambda: expect("ASSISTANT_INSUFFICIENT_BALANCE",
                           lambda: prov.DeepSeekChatProvider().chat(
                               [{"role": "user", "content": "x"}], []), 402))
    case("402 → ASSISTANT_INSUFFICIENT_BALANCE（余额，不是 Key）", err_balance)

    def err_model():
        return with_urlopen(
            _http_raiser(404, {"error": {"message": "Model Not Exist"}}),
            lambda: expect("ASSISTANT_MODEL_UNAVAILABLE",
                           lambda: prov.DeepSeekChatProvider().chat(
                               [{"role": "user", "content": "x"}], []), 404))
    case("404 → ASSISTANT_MODEL_UNAVAILABLE（模型名，不是 Key）", err_model)

    def err_timeout():
        def raiser(req, timeout=None):  # noqa: ANN001
            raise socket.timeout("timed out")
        return with_urlopen(raiser, lambda: expect(
            "ASSISTANT_TIMEOUT",
            lambda: prov.DeepSeekChatProvider().chat([{"role": "user", "content": "x"}], []), 504))
    case("超时 → ASSISTANT_TIMEOUT（不自动重试）", err_timeout)

    def err_network():
        def raiser(req, timeout=None):  # noqa: ANN001
            raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        return with_urlopen(raiser, lambda: expect(
            "ASSISTANT_NETWORK_ERROR",
            lambda: prov.DeepSeekChatProvider().chat([{"role": "user", "content": "x"}], []), 503))
    case("网络不通 → ASSISTANT_NETWORK_ERROR", err_network)

    print("\n=== 4. Ark 通道未受影响（仍用 ARK Key/方舟地址）===")

    def ark_unaffected():
        captured = {}

        def fake_ark(method, path, body=None, timeout=None):  # noqa: ANN001
            captured.update({"method": method, "path": path, "body": body})
            return {"choices": [{"message": {"content": "方舟可用"}}], "usage": {}}
        providers_ark._request = fake_ark  # type: ignore[assignment]
        p = prov.ArkChatProvider(model="doubao-test")
        r = p.chat([{"role": "user", "content": "x"}], [])
        assert captured["path"] == "/chat/completions"
        assert captured["body"]["model"] == "doubao-test"
        assert "thinking" not in captured["body"], "方舟请求不应带 DeepSeek 参数"
        assert providers_ark.configured() is True   # 用的是 ARK_API_KEY
        assert r.reply == "方舟可用"
        return {"path": captured["path"], "ark_key_present": True,
                "thinking_param": "absent"}
    case("Ark 仍走方舟路径、使用 ARK_API_KEY、无 DeepSeek 专有参数", ark_unaffected)

    print("\n=== 5. 配置写入（保留其它配置 / 幂等 / 600 / 不回显）===")

    def config_write():
        from tools_deepseek import apply_config, check_integrity  # noqa: PLC0415
        d = Path(tempfile.mkdtemp(prefix="ds-env-"))
        envf = d / ".env"
        envf.write_text("APP_PORT=8100\nARK_API_KEY=ark-keep-me\n# 注释保留\n", encoding="utf-8")
        res1 = apply_config("sk-test-1234567890", env_path=envf)
        text1 = envf.read_text(encoding="utf-8")
        res2 = apply_config("sk-test-1234567890", env_path=envf)
        text2 = envf.read_text(encoding="utf-8")
        assert "ARK_API_KEY=ark-keep-me" in text2, "误删其它配置"
        assert "# 注释保留" in text2
        assert text2.count("DEEPSEEK_API_KEY=") == 1, "产生重复键"
        assert text1 == text2, "写入不幂等"
        assert "sk-test-1234567890" not in json.dumps(res1, ensure_ascii=False), "返回值泄露 Key"
        mode = stat.S_IMODE(envf.stat().st_mode)
        assert mode == 0o600, oct(mode)
        integ = check_integrity({"provider": "deepseek", "model": "deepseek-flash",
                                 "base_url": "https://api.deepseek.com",
                                 "api_key": "sk-test-1234567890", "has_ark_key": True})
        assert integ["ok"] is True, integ
        return {"preserved_ark": True, "idempotent": True, "mode": oct(mode),
                "masked": res1["api_key_masked"]}
    case("写入保留 Ark 配置、幂等、600 权限、返回值不含明文 Key", config_write)

    print("\n=== 6. 原生图片/视频与手动操作不受影响 ===")

    def manual_unaffected():
        from app import providers_ark as pa
        # 恢复守卫，避免误触网
        def _guard(*a, **k):
            raise AssertionError("不应调用云端")
        pa._request = _guard  # type: ignore[assignment]
        t = main.create_task(main.TaskIn(kind="image", model_id="doubao-seedream-5-0-flash-260915",
                                        input_asset_id=REF_IMG,
                                        params={"prompt": "白底主图",
                                                "generation_mode": "image_to_image",
                                                "size": "2048x2048"}))
        assert t["status"] == "queued"
        r = worker.cancel(t["id"])
        assert r["result"] == "canceled"
        est = main.estimate_task(main.TaskIn(kind="video",
                                            model_id="doubao-seedance-1-0-pro-fast-251015",
                                            input_asset_id="asset_cd91e2f9dda14123a622",
                                            template_id="tpl_product_motion_5s",
                                            template_fields={"motion": "推近特写", "duration": 5}))
        assert est["effective"]["duration"] == 5
        assert est["side_effects"]["task_created"] is False
        return {"manual_task": "queued→canceled", "estimate_ok": True,
                "estimate_cost": est["estimated_cost_cny"]}
    case("手动提交/取消/预估正常（助手通道不影响生成侧）", manual_unaffected)

    def assistant_not_default():
        os.environ["ASSISTANT_PROVIDER"] = "none"
        prov.set_provider(None)
        try:
            st = prov.provider_status()
            assert st["available"] is False and st["state"] == "not_configured", st
        finally:
            os.environ["ASSISTANT_PROVIDER"] = "deepseek"
            prov.set_provider(None)
        return {"default_state": "not_configured"}
    case("默认仍为未配置（不会自行切到 DeepSeek）", assistant_not_default)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "DeepSeek 官方通道接入 · 离线验证",
        "network_calls": 0, "paid_calls": 0, "real_llm_calls": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": ("本次全部为离线/模拟验证（网络函数被替换）。"
                       "未调用 DeepSeek 真实接口，未被计费；真实连通性仍需一次独立验证。"),
    }
    path = OUT / "离线测试结果.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("真实网络调用 0 次｜付费 0 次｜真实业务库未改动")
    print(f"证据：{path}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
