#!/usr/bin/env python3
"""安全写入单个 Key 到本机 .env（供 .command 双击脚本调用）。

用法（由 shell 通过 stdin 传入 Key，绝不走命令行参数）：
    printf '%s' "$KEY" | python3 backend/tools_set_key.py ARK_API_KEY

行为：
- 从 stdin 读第一行作为 Key，清理首尾空白与引号；
- 拒绝被污染的输入（含 button/text returned、空白、非法字符）；
- 保留 .env 其它行，替换同名键（去重），备份为 .env.bak，权限 600；
- 只输出掩码摘要，绝不回显明文。
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

# 各厂 Key 的允许字符集（宽松但能挡住弹窗文字/空格/中文）
PATTERNS = {
    "ARK_API_KEY": re.compile(r"^ark-[A-Za-z0-9_\-]{8,200}$"),
    "DEEPSEEK_API_KEY": re.compile(r"^sk-[A-Za-z0-9_\-]{8,200}$"),
}
GENERIC = re.compile(r"^[A-Za-z0-9_\-]{16,200}$")


def mask(v: str) -> str:
    return "***" if len(v) <= 10 else f"{v[:6]}***{v[-4:]}（长度 {len(v)}）"


def validate(name: str, key: str) -> None:
    if not key:
        raise ValueError("Key 为空")
    if re.search(r"(button|text)\s*returned\s*:", key, re.I) or any(c.isspace() for c in key):
        raise ValueError("Key 里混入了弹窗文字或空白，请重新粘贴")
    pat = PATTERNS.get(name, GENERIC)
    if not pat.match(key):
        raise ValueError(f"Key 格式不符合 {name} 的要求（请确认复制完整、无多余字符）")


def apply(name: str, key: str, env_path: Path | None = None) -> dict:
    path = env_path or ENV_PATH
    key = (key or "").strip().strip('"').strip("'")
    validate(name, key)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        s = line.strip()
        k = s.split("=", 1)[0].strip() if ("=" in s and not s.startswith("#")) else None
        if k == name:
            if not replaced:
                out.append(f"{name}={key}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f"{name}={key}")
    if path.exists():
        (path.parent / (path.name + ".bak")).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        bak = path.parent / (path.name + ".bak")
        if bak.exists():
            os.chmod(bak, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return {"ok": True, "name": name, "masked": mask(key),
            "replaced_existing": replaced, "env_path": str(path)}


def probe_ark(key: str, base_url: str | None = None, timeout: float = 15) -> dict:
    """免费校验方舟 Key：GET /models（只读列表，不消耗推理额度、不产生费用）。"""
    import socket
    import urllib.error
    import urllib.request

    base = (base_url or os.getenv("ARK_BASE_URL")
            or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    req = urllib.request.Request(base + "/models", method="GET",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read() or b"{}")
        items = data.get("data") or []
        return {"ok": True, "http": 200, "model_count": len(items),
                "base_url": base,
                "sample": [i.get("id") for i in items[:3]]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")[:200]
        if e.code in (401, 403):
            return {"ok": False, "http": e.code, "category": "ARK_KEY_INVALID",
                    "message": ("Key 无效或已失效（HTTP 401）" if e.code == 401
                                else "Key 有效但权限不足（HTTP 403）：若为子账号 Key，"
                                     "需授予方舟权限，或改用主账号 Key"),
                    "raw": raw}
        return {"ok": False, "http": e.code, "category": "ARK_HTTP_ERROR",
                "message": f"方舟返回 HTTP {e.code}", "raw": raw}
    except (socket.timeout, TimeoutError):
        return {"ok": False, "http": 504, "category": "ARK_TIMEOUT",
                "message": f"连接方舟超时（>{timeout:.0f}s）"}
    except urllib.error.URLError as e:
        return {"ok": False, "http": 503, "category": "ARK_NETWORK_ERROR",
                "message": f"无法连接方舟：{type(getattr(e, 'reason', e)).__name__}"}
    except json.JSONDecodeError:
        return {"ok": False, "http": 502, "category": "ARK_BAD_RESPONSE",
                "message": "方舟返回非 JSON"}


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "缺少环境变量名"}, ensure_ascii=False))
        return 2
    name = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else ""
    raw = sys.stdin.read()
    key = raw.splitlines()[0] if raw.strip() else ""
    key = key.strip().strip('"').strip("'")

    if mode == "--probe":
        # 只校验，不写入
        try:
            validate(name, key)
        except ValueError as e:
            print(json.dumps({"ok": False, "category": "FORMAT", "message": str(e)},
                             ensure_ascii=False))
            return 3
        out = probe_ark(key)
        out["name"] = name
        out["masked"] = mask(key)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 4

    try:
        print(json.dumps(apply(name, key), ensure_ascii=False))
        return 0
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
