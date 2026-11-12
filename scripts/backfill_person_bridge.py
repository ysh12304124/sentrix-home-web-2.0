#!/usr/bin/env python3
"""按观察的人物快照回填 ``person_bridge`` 词法投影。**幂等，可反复跑。**

**为什么要回填。** ``person_bridge`` 原先只在导入时写，那时还没有人被确认，所以这一列
在存量库里恒为空；确认人物、合并人物只改 ``observations.people_json``，不刷投影。
写入侧从这一版起在同一个事务里同步（``search_terms.sync_person_bridge``），但**此前**
确认过的人只在快照里、不在投影里——实体通道在「全部相册」下只读投影，于是按人找照片
召回为 0。见 ``docs/refactor/2026-11-11-entity-channel-all-albums.md``。

**为什么是脚本不是迁移。** 迁移是纯 SQL（``Database.migrate`` 只执行 ``.sql``），而投影
的值要过 ``normalize_text``（Unicode 小写、折叠空白），FTS 那一份还要过 ``pre_tokenize``
（中文 bigram）——两样都写不成 SQL。在 SQL 里近似一份，等于造第二个口径。

**幂等靠实现自己**：每条观察先删掉它的 ``person_bridge`` 行再按快照重写，一个事务。
重跑写出的行数与第一次相同（它是「快照里一共有几个名字」，不是「本次新增几行」）。

用法::

    .venv/bin/python scripts/backfill_person_bridge.py            # 全部观察
    .venv/bin/python scripts/backfill_person_bridge.py home-default
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from sentrix.bootstrap.container import Container  # noqa: E402
from sentrix.config import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 person_bridge 词法投影（幂等，可反复跑）")
    parser.add_argument("scope", nargs="?", default="", help="只回填这个空间；缺省为全部观察")
    scope = parser.parse_args().scope.strip() or None

    container = Container.build(load_settings())
    print(f"数据库 {container.settings.database_path}")
    print(f"范围 {scope or '全部观察'}")

    written = container.lexical_index.rebuild_person_bridge(scope)

    print(f"person_bridge 现有 {written} 行（= 快照里的人名总数；重跑应得到同一个数）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
