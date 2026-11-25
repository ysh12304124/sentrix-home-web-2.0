"""仅发布已提交的题库原件预览组件及测评静态包，不重启后台任务。"""

from pathlib import Path
import hashlib, json, shutil

BASE = Path(__file__).resolve().parent
release = json.loads((BASE / "gt-preview-manifest.json").read_text())
assert not (BASE / "gt-preview-acceptance.json").exists()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


for relative, value in release["files"].items():
    assert sha(BASE / "gt-preview" / relative) == value, relative
roots = [
    Path("/home/realmagic/xy/releases/psh-b2030e1-100-20261117"),
    Path("/home/realmagic/xy/releases/psh-b2030e1-100-20261117-8772"),
]
component = "apps/photobench/web/src/features/qa-browser/QaReviewCard.tsx"
for root in roots:
    assert sha(root / component) == release["component_before"], str(root)
for root in roots:
    for relative in sorted(release["files"], key=lambda name: name.endswith("/index.html")):
        target = root / relative
        saved = BASE / "gt-preview-backup" / str(target).lstrip("/")
        saved.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE / "gt-preview" / relative, target)
        assert sha(target) == release["files"][relative], str(target)
(BASE / "gt-preview-acceptance.json").write_text(
    json.dumps(
        {
            "status": "passed",
            "source_revision": release["source_revision"],
            "roots": [str(root) for root in roots],
            "files_per_root": len(release["files"]),
        }
    )
)
print((BASE / "gt-preview-acceptance.json").read_text())
