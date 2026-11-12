# 实体通道在「全部相册」下按人召回（2026-11-11）

## 0. 症状

用户在**全部相册**下问「王王的照片」。问答规划那一侧已经把人名接地成本名、经
`EvidenceRequest(person=…)` 交给检索（另一个会话的改动，不在本变更里），但检索侧的
实体通道在全部相册下召回为 0：

| 请求（真实库 `data/sentrix.db` 副本） | 实体通道召回 | `limit=6` 返回 |
|---|---|---|
| `GET /api/v1/search?q=王王&person=王王&scope_id=home-default` | 24 | 6，前 5 张全是王王 |
| `GET /api/v1/search?q=王王&person=王王`（不传 scope） | **0** | **0** |

（任务说明里记的是 32 / 1，那是同一天更早的库；数字随库变，形状不变。）

## 1. 根因：两处缺口叠在一起

1. **`person_bridge` 投影恒为空。** 词法投影只在导入时由 `ImportAssets` 调
   `refresh_from_observation` 写，那时还没有人被确认，`observations.people_json` 是空的。
   之后确认人物（`link_confirmed_mention` → `_append_person_snapshot`）只改快照、不刷投影；
   `SqliteLexicalIndex.rebuild_scope` 有实现，生产代码里没有调用方。真实库里
   `field_type='person_bridge'` 的行数是 **0**，而 `people_json` 非空的观察有 600 多条。
2. **兜底在 scope 为空时不跑。** `RetrieveEntity._assets_for_person` 查不到投影时退回扫
   观察快照，但 `ObservationRepository.list_scope` 要一个 scope，scope 为 `None`/`""` 时
   这条兜底直接返回空集（docstring 里写明了缺一条跨 scope 的扫描）。

所以给了 scope 时实体通道一直是靠兜底在跑，全部相册下两条路都是空的。

## 2. 改了什么

### 2.1 写侧：投影跟着快照同一个事务写

- `search_terms.sync_person_bridge(connection, observation_ids)`：按 `people_json` 的
  **当前值**删掉并重写这些观察的 `person_bridge` 行（`observation_search_terms` 与
  `observation_search_fts` 两份），只碰这一个字段。
- `entities._append_person_snapshot` 写完快照紧接着在**同一个连接、同一个事务**里调它。
  确认（`ReviewCluster.confirm`）、同名并入（`_merge_into_person`）、在线匹配
  （`EnrollFace`）都经 `link_confirmed_mention` 走到这里，所以三条确认路径一次覆盖。
- 名字解析只有一份：`assets.people_names`（原 `_json_people`，改成公开），观察读取与投影
  同步共用；归一化走 `_values` → `normalize_text`，与 `refresh_from_observation` 写出的
  行完全相同（有用例钉住「同步之后再整条刷新，行不变」）。

**为什么是吃 `connection` 的函数，不是 `LexicalIndex` 端口上的方法。** 快照是权威、投影
是派生，两者分两次提交，中间失败一次就是「快照里有人、按人名查不到」——正是这次的缺陷。
写快照的是基础设施层的仓储（`people_json` 本来就是身份层写进观察表的跨聚合投影），让它在
自己的事务里调同层的一个 SQL 函数，比让每个应用层用例在提交之后记得再调一次端口可靠。
`import-linter` 的 `infrastructure-purity` 不禁止同层依赖（`text_vectors.py` → `vectors.py`
是先例）。

**守卫。** `test_every_people_snapshot_write_also_syncs_the_projection` 静态扫描仓储目录：
任何含 `SET people_json` 的模块都必须调用 `sync_person_bridge(`。人物合并、取消确认等之后
新增的快照写入路径漏了同步会在这里变红。

### 2.2 存量回填：脚本，不是迁移

- 端口 `LexicalIndex` 新增 `rebuild_person_bridge(scope_id: str | None = None) -> int`，
  实现是一个事务里对所选观察逐条 `sync_person_bridge`，**不设上限**（`rebuild_scope` 的
  `REBUILD_LIMIT` 是为整条重建要读 asset 与地点设的；这里每条只是几句本地 SQL，设上限
  等于静默留下一批「快照里有、投影里没有」）。
- `scripts/backfill_person_bridge.py [scope]`：经 `Container` 拿端口调它，幂等。
  `check_method_reachability.py` 只扫 `apps/api/src`，看不到脚本这个调用者，所以
  `LexicalIndex.rebuild_person_bridge` 登记进了 `PENDING_METHODS`（写明调用者是这个脚本）。
- **不做成迁移**：`Database.migrate` 只执行 `.sql`，而投影值要过 `normalize_text`
  （Unicode 小写、折叠空白）、FTS 要过 `pre_tokenize`（中文 bigram），写成 SQL 就是第二个
  口径。也不挂在启动时自动跑：回填是一次性的维护动作，启动路径上的隐式写库不可见。

运行方式与其他回填脚本相同，**需要服务能正常装配**（`Container.build` 会做嵌入方案冒烟，
嵌入侧车得在）：

```sh
.venv/bin/python scripts/backfill_person_bridge.py            # 全部观察
.venv/bin/python scripts/backfill_person_bridge.py home-default
```

输出的行数是「快照里的人名总数」，重跑应得到同一个数。

### 2.3 读侧：全部相册只走投影，排除评测空间

- `SqliteLexicalIndex.assets_for_person(name, scope_id=None/"")` 加
  `scope_id NOT IN (SELECT id FROM memory_spaces WHERE include_in_people = 0)`——与
  `EntityRepository.list_entities` 同一条口径（写成排除集也与它一致：没登记进
  `memory_spaces` 的老 scope 仍算家庭数据）。显式给了 scope 时不过滤，评测按自己的
  scope 查照样查得到。端口 docstring 写明了这条契约。
- `RetrieveEntity` 全部相册只走投影；兜底仍只在给了 scope 时跑（判据从 `is None` 改成
  `not scope_id`，助手工具传的是 `""`）。没补跨 scope 的全表扫描：那要么在应用层重复一遍
  「哪些空间算家庭」，要么把评测副本里的同一批照片交出来；它要兜的「投影还没建」在回填
  之后已不是全部相册的常态。

## 3. 验证

**测试**（`apps/api/tests/integration/test_person_bridge_projection.py`，14 条；外加
`contract/test_search_routes.py::test_person_condition_recalls_across_all_albums`）：

- 确认之后不经任何重建，`person_bridge` 与 FTS 里就有人名；重复确认不重复；同一张图上第二个
  人确认不冲掉第一个；同步不动同一条观察的其他字段；同步与整条刷新写出同样的行。
- 回填：补上修之前的确认、幂等、按 scope 回填不碰别的空间、快照里没了的名字从投影里删掉。
- 读侧：全部相册（`None` 与 `""`）跨家庭空间召回；排除 `include_in_people = 0` 的空间；
  显式评测 scope 仍查得到；回填之后全部相册查得到存量的人。
- 去掉 `_append_person_snapshot` 里那一句 `sync_person_bridge` 时，14 条里 8 条变红（含守卫）。

**真实库副本**（`sqlite3 … .backup` 出的副本 + `qdrant/` 拷贝，`TestClient` 不跑 lifespan，
嵌入冒烟打桩；嵌入侧车不在，视觉/文本通道降级，不影响实体通道）：

| | 回填前 | 回填后 |
|---|---|---|
| `person_bridge` 行数 | 0 | 643（家庭 69，评测 574） |
| 全部相册 `?q=王王&person=王王&limit=6` 实体通道 | 0 | **24** |
| 全部相册返回 | 0 张 | **6 张，全是 `home-default` 里的王王** |
| `scope_id=home-default` 实体通道 | 24 | 24 |

第二次跑回填仍写 643 行（幂等）。评测空间里同名的人没有进全部相册（实体通道 24 = 家庭
空间里王王的照片数）。

## 4. 没做 / 后续

- **人物合并**（`SqlitePersonMergeRepository._rewrite_people_snapshots`）在另一个会话的未提交
  改动里，不在本分支。它落地时要在改完 `people_json` 的同一个连接里调
  `sync_person_bridge(connection, 改过的 observation_ids)`；不调的话上面那条静态守卫会红。
- **取消确认**在本分支没有写入路径（`delete_candidate` 拒绝删除已确认的人，`people_json` 只增不减）。
  将来加上时同上。
- **改名**（`rename_entity`）不改 `people_json`，快照里留的是旧名字——快照本身就陈旧了，
  不是投影的问题，本次没动。
- 全部相册下**其他通道**（视觉 / 词法 / 文本）仍跨所有空间召回，包括评测空间；这次只按任务
  收敛了实体通道。合同测试里评测副本那张图就是经视觉通道进来的。
- 真实库 `data/sentrix.db` **没有回填**——只在副本上验证。需要时在服务装配得起来的环境里跑
  一次上面的脚本。
