#!/usr/bin/env bash
# 把某个 git 提交安装成只读发行目录，并切换 /opt/sentrix/current。
# 用法：sudo ./install-release.sh <40-hex-sha> [仓库路径]
set -euo pipefail

SHA="${1:?git sha is required}"
SRC="${2:-.}"
ROOT=/opt/sentrix
RELEASE="$ROOT/releases/$SHA"

install -d -m 0755 "$ROOT/releases"
if [[ -d "$RELEASE" ]]; then
  echo "release already exists: $RELEASE" >&2
  exit 1
fi

install -d -m 0755 "$RELEASE"
git -C "$SRC" archive "$SHA" | tar -x -C "$RELEASE"

python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/pip" install --upgrade pip
"$RELEASE/.venv/bin/pip" install -e "$RELEASE/apps/api"

chown -R root:root "$RELEASE"
chmod -R a-w "$RELEASE"
ln -sfn "$RELEASE" "$ROOT/current"

echo "installed $SHA -> $ROOT/current"
