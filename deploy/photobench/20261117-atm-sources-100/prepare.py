"""从已提交 HEAD 构建 100 发布变体，保留各端口既有运行适配。"""

from pathlib import Path
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[3]
BASE_REVISION = "0608d54"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=REPO)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def extract(ref, target):
    target.mkdir()
    with tarfile.open(fileobj=io.BytesIO(git("archive", ref))) as archive:
        archive.extractall(target, filter="data")


def prepare(destination):
    assert not git("status", "--porcelain"), "Commit local changes before preparing a release"
    stage = Path(tempfile.mkdtemp(prefix="sentrix-sources100-"))
    extract(BASE_REVISION, stage / "base")
    extract("HEAD", stage / "current")
    revision = git("rev-parse", "HEAD").decode().strip()
    changed = [
        path
        for path in git(
            "diff", "--name-only", BASE_REVISION, "HEAD", "--", "apps/api/src", "apps/photobench/src"
        )
        .decode()
        .splitlines()
        if path.endswith((".py", ".sql"))
    ]
    manifest = {
        "source_revision": revision,
        "deployed_version": revision + "+100-atm-sources",
        "stage": str(stage),
        "variants": {},
        "payload": {},
        "dist": {},
    }
    destination.mkdir(parents=True)
    for name, patches in (
        ("8191", ["8191.patch"]),
        ("8291", ["8291.patch"]),
        ("8294", []),
        ("photobench", ["8772.patch"]),
    ):
        old, new = stage / ("old-" + name), stage / ("new-" + name)
        shutil.copytree(stage / "base", old)
        shutil.copytree(stage / "current", new)
        for patch in patches:
            for tree in (old, new):
                subprocess.run(
                    ["git", "apply", str(REPO / "deploy/photobench/20261117-psh-100" / patch)],
                    cwd=tree,
                    check=True,
                    capture_output=True,
                )
        paths = [
            path
            for path in changed
            if path.startswith("apps/photobench/src" if name == "photobench" else "apps/api/src")
        ]
        files = {}
        for path in paths:
            files[path] = {"before": sha(old / path), "after": sha(new / path)}
            target = destination / "payload" / name / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(new / path, target)
        manifest["variants"][name] = {"files": files, "old": str(old), "new": str(new)}
    for app, kind in (("apps/web", "product"), ("apps/photobench/web", "photobench")):
        source = stage / "current" / app
        (source / "node_modules").symlink_to(REPO / app / "node_modules", target_is_directory=True)
        build_env = dict(os.environ)
        if kind == "product":
            build_env["VITE_LEGACY_LOCAL_AUTH"] = "1"
        subprocess.run(["npm", "run", "build"], cwd=source, env=build_env, check=True)
        target = destination / "dist" / kind
        shutil.copytree(source / "dist", target)
        manifest["dist"][kind] = {
            str(path.relative_to(target)): sha(path) for path in target.rglob("*") if path.is_file()
        }
    for name in ("preflight.py", "install.py", "smoke.py"):
        shutil.copy2(Path(__file__).with_name(name), destination / name)
    manifest["payload"] = {
        str(path.relative_to(destination)): sha(path) for path in destination.rglob("*") if path.is_file()
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "directory": str(destination),
                "stage": str(stage),
                "revision": revision,
                "files": {name: len(value["files"]) for name, value in manifest["variants"].items()},
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("output", type=Path)
    prepare(parser.parse_args().output)
