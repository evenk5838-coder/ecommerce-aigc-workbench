#!/usr/bin/env python3
"""DeepSeek 配置与连接验证（供 .command 双击脚本调用）。

安全约定：
- Key **只从 stdin 读取**，不接受命令行参数、不回显、不写日志、不进仓库。
- 更新 `.env` 时**保留其他配置**，只替换 DeepSeek 相关的 5 行；写入后 chmod 600。
- 保存 Key **不发起任何网络请求**；验证必须显式 `verify`（会消耗官方额度）。

子命令：
    setup   从 stdin 读 Key，写入配置并检查完整性（不联网）
    status  只读检查当前配置完整性（不联网）
    verify  发一次最小请求验证连接（**付费**，需 CONFIRM=1）
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

# 本工具负责的键（其它行一律保留）
MANAGED = ("ASSISTANT_PROVIDER", "ASSISTANT_MODEL", "DEEPSEEK_API_KEY",
           "DEEPSEEK_BASE_URL", "ASSISTANT_MAX_TOKENS", "ASSISTANT_TIMEOUT_S")


def mask(key: str) -> str:
    if not key:
        return "（未设置）"
    if len(key) <= 10:
        return "***"
    return f"{key[:5]}***{key[-4:]}（长度 {len(key)}）"


def _read_env() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _parse_env(lines: list[str]) -> dict:
    out = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def read_config() -> dict:
    env = _parse_env(_read_env())
    return {
        "provider": env.get("ASSISTANT_PROVIDER", ""),
        "model": env.get("ASSISTANT_MODEL", ""),
        "base_url": env.get("DEEPSEEK_BASE_URL", "") or DEFAULT_BASE,
        "api_key": env.get("DEEPSEEK_API_KEY", ""),
        "has_ark_key": bool(env.get("ARK_API_KEY")),
    }


def check_integrity(cfg: dict | None = None) -> dict:
    cfg = cfg or read_config()
    missing, warnings = [], []
    if cfg["provider"] != "deepseek":
        missing.append("ASSISTANT_PROVIDER=deepseek")
    if not cfg["api_key"]:
        missing.append("DEEPSEEK_API_KEY")
    elif not cfg["api_key"].startswith("sk-"):
        warnings.append("Key 不以 sk- 开头，请确认是否为 DeepSeek 官方 Key")
    if not cfg["model"]:
        missing.append("ASSISTANT_MODEL")
    if not cfg["base_url"].startswith("https://api.deepseek.com"):
        warnings.append(f"base_url 不是 DeepSeek 官方地址：{cfg['base_url']}")
    return {"ok": not missing, "missing": missing, "warnings": warnings,
            "provider": cfg["provider"], "model": cfg["model"],
            "base_url": cfg["base_url"], "api_key_masked": mask(cfg["api_key"])}


def apply_config(key: str, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE,
                 env_path: Path | None = None) -> dict:
    """写入配置；保留其它行；幂等（不产生重复键）；返回掩码摘要。"""
    path = env_path or ENV_PATH
    key = (key or "").strip().strip('"').strip("'")
    if not key:
        raise ValueError("Key 为空")
    # 防污染：弹窗返回值（button/text returned）或空白一律拒绝
    if re.search(r"(button|text)\s*returned\s*:", key, re.I) or any(c.isspace() for c in key):
        raise ValueError("Key 里混入了弹窗文字或空白，请重新双击配置脚本粘贴")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{16,200}", key):
        raise ValueError("Key 格式异常（含非法字符或长度不符），请确认复制的是 DeepSeek 官方 Key")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    desired = {
        "ASSISTANT_PROVIDER": "deepseek",
        "ASSISTANT_MODEL": model,
        "DEEPSEEK_BASE_URL": base_url,
        "DEEPSEEK_API_KEY": key,
        "ASSISTANT_TIMEOUT_S": "30",
    }
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        s = line.strip()
        k = s.split("=", 1)[0].strip() if ("=" in s and not s.startswith("#")) else None
        if k in desired:
            if k not in seen:
                out.append(f"{k}={desired[k]}")
                seen.add(k)
            continue                     # 丢弃重复行
        out.append(line)
    for k, v in desired.items():
        if k not in seen:
            out.append(f"{k}={v}")
    if path.exists():
        (path.parent / (path.name + ".bak")).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)     # 600
    except OSError:
        pass
    return {"ok": True, "api_key_masked": mask(key), "model": model,
            "base_url": base_url, "env_path": str(path),
            "preserved_ark_key": "ARK_API_KEY=" in "\n".join(out)}


def verify_once() -> dict:
    """**一次**最小请求：无工具、无重试、max_tokens 很小。会消耗官方额度。"""
    from app.assistant import provider as prov
    from app.errors import AppError

    cfg = read_config()
    integ = check_integrity(cfg)
    if not integ["ok"]:
        return {"ok": False, "category": "config_incomplete",
                "http": 400, "message": "配置不完整：" + "、".join(integ["missing"]),
                "detail": integ}

    p = prov.DeepSeekChatProvider(model=cfg["model"] or None,
                                  api_key=cfg["api_key"], base_url=cfg["base_url"],
                                  timeout=20)
    os.environ["ASSISTANT_MAX_TOKENS"] = "32"      # 控制单次成本
    started = datetime.now()
    try:
        res = p.chat([{"role": "user", "content": "只回复两个字：可用"}], [])
    except AppError as e:
        return {"ok": False, "category": e.code, "http": e.http,
                "message": e.message, "detail": e.detail,
                "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "category": "UNEXPECTED", "http": 500,
                "message": f"{type(e).__name__}: {e}"}
    # 落库：把"连接级验证通过"持久化，供 /api/assistant/status 呈现第三态
    try:
        from app import db
        db.init_db()
        db.insert("assistant_usage", {
            "id": db.new_id("ausg"), "session_id": None, "kind": "verify_ok",
            "provider": "deepseek", "model": res.meta.get("model") or p.model,
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
            "tool_calls": 0, "cost_cny": None,
            "note": "connectivity:1-request", "created_at": db.now(),
        })
    except Exception:  # noqa: BLE001  写库失败不影响验证结论本身
        pass
    return {
        "ok": True, "category": "ok",
        "http": 200,
        "provider": "deepseek",
        "model": res.meta.get("model"),
        "base_url": p.base_url,
        "reply": (res.reply or "")[:80],
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000),
    }


def estimate_budget_cny() -> str:
    """一次最小请求的保守估算（按官方单价上限：输入 $0.30/M、输出 $1.20/M）。"""
    in_tok, out_tok = 80, 32
    usd = in_tok / 1e6 * 0.30 + out_tok / 1e6 * 1.20
    cny = usd * 7.2
    return f"约 ¥{cny:.4f}（≈${usd:.6f}，按官方峰值单价与 7.2 汇率保守估算）"


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "setup":
        raw = sys.stdin.read()
        key = raw.splitlines()[0] if raw.strip() else ""
        try:
            res = apply_config(key)
        except ValueError as e:
            print(json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False))
            return 2
        integ = check_integrity()
        print(json.dumps({**res, "integrity": integ}, ensure_ascii=False))
        return 0 if integ["ok"] else 3
    if cmd == "status":
        print(json.dumps(check_integrity(), ensure_ascii=False))
        return 0
    if cmd == "verify":
        if os.getenv("CONFIRM") != "1":
            print(json.dumps({"ok": False, "category": "not_confirmed",
                              "message": "未确认；验证会消耗 DeepSeek 官方额度，需 CONFIRM=1"},
                             ensure_ascii=False))
            return 4
        out = verify_once()
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 5
    if cmd == "budget":
        print(estimate_budget_cny())
        return 0
    print(json.dumps({"error": f"未知子命令：{cmd}"}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main())
