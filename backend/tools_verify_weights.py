"""权重完整性核验：尺寸 + sha256 对照官方 LFS oid。不依赖"文件存在"。"""
import hashlib, json, os, sys, urllib.request

REPOS = {
    "sd15": ("stable-diffusion-v1-5/stable-diffusion-v1-5", "data/models/sd15"),
    "sdxl-base-1.0": ("stabilityai/stable-diffusion-xl-base-1.0", "data/models/sdxl-base-1.0"),
    "i2vgen-xl": ("ali-vilab/i2vgen-xl", "data/models/i2vgen-xl"),
}

def manifest(repo):
    u = f"https://huggingface.co/api/models/{repo}?blobs=true"
    d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "verify"}), timeout=40))
    out = {}
    for s in d.get("siblings", []):
        lfs = s.get("lfs") or {}
        out[s["rfilename"]] = {"size": lfs.get("size") or s.get("size"), "sha256": lfs.get("oid") or lfs.get("sha256")}
    return out

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main(model_key):
    repo, root = REPOS[model_key]
    man = manifest(repo)
    rows, ok_all = [], True
    for rel, exp in man.items():
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        real_size = os.path.getsize(p)
        size_ok = (exp["size"] is None) or (real_size == exp["size"])
        digest = sha256(p) if size_ok and exp["sha256"] else None
        sha_ok = (exp["sha256"] is None) or (digest == exp["sha256"])
        ok = size_ok and sha_ok
        ok_all &= ok
        rows.append({"file": rel, "size": real_size, "expected_size": exp["size"],
                     "size_ok": size_ok, "sha256": digest, "expected_sha256": exp["sha256"], "ok": ok})
    result = {"model": model_key, "all_ok": ok_all, "files": rows,
              "verified_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if ok_all and rows:
        with open(os.path.join(root, ".verified.json"), "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    return 0 if ok_all else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "sd15"))
