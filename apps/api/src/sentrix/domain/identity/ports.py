"""身份域端口：人脸仓储与实体仓储。

domain 只声明形状，SQL 在 infrastructure。两个仓储分开不是因为表多，而是因为**写侧
是两条不同的链路**：``FaceRepository`` 的写入者是在线匹配与簇审阅（Task 72/73），
``EntityRepository`` 的写入者是命名与属性维护（Task 73/74）。合成一个
``IdentityRepository`` 会让「谁改了实体的名字」这件事在类型上消失。

**成员一律声明成方法，只读属性用 ``@property``。** 沿用
``domain/retrieval/ports.py`` 里 ``VisualEmbedder`` 的注解：适配器把只读属性实现成
property 时，mypy 的 ``arg-type`` 不允许一个只读 property 去满足「可写变量」的协议约定。
本文件里 ``PersonVision.model_id`` 就是按这条写的属性成员。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sentrix.domain.identity.family import (
    FamilyAnalysisRunRecord,
    FamilyEvidenceRow,
    FamilyMembershipRecord,
    FamilyPersonProjection,
    FamilyPortraitRecord,
    FamilyRelationshipRecord,
)
from sentrix.domain.identity.models import (
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_PENDING,
    PORTRAIT_TRIGGER_MANUAL,
    PROPERTY_SOURCE_DERIVED,
    AppearanceEvidenceRecord,
    EntityDecoration,
    EntityMentionRecord,
    EntityPropertyRecord,
    EntityRecord,
    EntityRevisionRecord,
    EntitySpec,
    FaceClusterRecord,
    FaceDetection,
    FaceInstanceRecord,
    FaceManifestRecord,
    FacePrototypeRecord,
    PersonMomentRecord,
    PersonNameMatch,
    PhotoIdentityRecord,
    PortraitRevisionRecord,
    RelationshipRecord,
    SemanticClaimRecord,
    SemanticProfileRecord,
)


class FaceDetector(Protocol):
    """一张图 → 人脸表。实现在 ``infrastructure/models/face_adapter.py``。

    **只声明两个检测入口，不声明适配器的其余状态。** 1.0 的 ``FaceAdapter`` 还带一串
    ``identity_*`` 字段（``identity_ready`` / ``identity_error`` / ``identity_fallback``），
    它们服务于那三个被判死的 AdaFace 适配器（见 ``s5-deviations`` 证据 1/3）；2.0 这条
    链路只剩「有向量 / 没向量」，而那是 ``FaceDetection`` 自己的字段，不是适配器的状态。

    两个入口的差别在**召回策略**，不在能力上：``detect`` 是相册导入用的常规检测；
    ``detect_identity_photo`` 是用户预置身份照片用的三级降级（常规 → 放大重试 → 整图
    兜底），它更慢、更宽松，也因此只该用在「用户明确说这张图里有这个人」的场合。
    """

    def detect(self, path: str) -> Sequence[FaceDetection]:
        """常规检测：返回的人脸都已经带好向量（没有向量的候选被丢掉）。"""
        ...

    def detect_identity_photo(self, path: str) -> Sequence[FaceDetection]:
        """预置照片的检测：检不出就放大重试，再检不出就把整张图当成一张脸。"""
        ...


@dataclass(frozen=True, slots=True)
class PreparedFaces:
    """人脸检测（GPU）的产物，``FaceEnroller.commit`` 的输入。

    空 ``detections`` 是**有效值**——它说的是「这张图确实没有脸」，与「这张图
    没检测过」不是一回事：后者在 ``ImportAssets`` 那侧由 ``None`` 表达。
    """

    detections: tuple[FaceDetection, ...] = ()


class FaceEnroller(Protocol):
    """人脸链的两段式（与 ``AssetVisualIndexer`` 同形）。

    ``prepare`` 只做检测——那是这一段里唯一能离开写库事务边界的东西；匹配与建簇
    必须留在 ``commit``：它读的是**同 scope 的候选簇**，而那些簇是同批前一张照片
    刚写进去的。顺序反了不报错，只是同一个人的两张照片各建一个簇。

    两个方法都只收 id 与路径，不收 Asset / Observation：这一层不需要它们的其余
    字段，多收一个就等于多一条可以改 scope 的入口（``EnrollFace`` 的 scope 只能
    从 asset 读，那条纪律在它自己那一层）。
    """

    def prepare(self, path: str) -> PreparedFaces: ...

    def commit(self, asset_id: str, observation_id: str, prepared: PreparedFaces) -> int: ...


class FaceRepository(Protocol):
    """人脸簇 / 实例 / 原型。

    ``list_clusters`` 强制 ``memory_spaces.include_in_people = 1``——这是端口的契约，
    不是某个调用方的过滤：不给这一条，任何人写一次 ``/api/people`` 都会把评测空间
    聚出来的人脸当家庭成员展示。
    """

    def list_clusters(self, *, status: str | None = None) -> Sequence[FaceClusterRecord]:
        """``status`` 为空时返回除 ``rejected`` 外的全部簇，且只含进人物视图的空间。"""
        ...

    def list_clusters_for_entity(self, entity_id: str) -> Sequence[FaceClusterRecord]:
        """这个人名下的全部簇（含 rejected），按 ``updated_at DESC``。

        与 ``list_clusters`` 有三处不同，三处都是 1.0 ``db.py:4580`` 的原样：按实体过滤
        （人物详情页要的是这个人的簇）、**不过滤** ``include_in_people``（详情页已经知道
        这个人是谁，再按空间挡一次只会让他的簇凭空少一半）、**不过滤** ``rejected``
        （被拒的簇是这个人历史的一部分，界面要能解释「这簇曾经属于他」）。
        """
        ...

    def list_candidates(self, scope_id: str) -> Sequence[FaceClusterRecord]:
        """在线匹配的候选：同 scope、``status IN ('pending','confirmed')``。

        与 ``list_clusters`` 有两处**故意**不同，两处都照抄 1.0 ``db.py:4867``：
        按 scope 过滤（匹配是空间内的事），且**不过滤** ``include_in_people``
        （评测空间也要聚类，只是它的簇不进人物视图）。
        """
        ...

    def get_cluster(self, cluster_id: str) -> FaceClusterRecord | None:
        """簇审阅的入口：四个动作都先按 id 找它，找不到就是 404。"""
        ...

    def get_instance(self, instance_id: str) -> FaceInstanceRecord | None:
        """实例带出它所属 asset 的路径 / 文件名 / 媒体类型与 scope。"""
        ...

    def list_instances(self, cluster_id: str) -> Sequence[FaceInstanceRecord]: ...

    def list_face_manifest(self, asset_id: str) -> Sequence[FaceManifestRecord]:
        """一张照片里**检测到的每一张脸**（含没绑定到人的那些），按提示词要的顺序。T123 用。

        1.0 ``agent_runtime/tools.py:2320-2358`` 的 ``_photo_face_manifest``，逐字。
        与 ``list_photo_identities`` 的关系见 ``FaceManifestRecord`` 的 docstring——
        **两条查询、两种口径，不要合并**。

        排序是 ``quality DESC, detection_confidence DESC, id``——**三键**，第三键是
        ``id``。1.0 的 ``list_photo_identities`` 只有前两键（同分时取到哪一行由 SQLite
        定），这一条有第三键，所以 ``face_id`` 的编号是**确定的**。这个差别是必须的：
        编号要进提示词、还要被模型回指，两次调用给出不同的编号会让那条回指失效。
        """
        ...

    def list_photo_identities(self, asset_id: str) -> Sequence[PhotoIdentityRecord]:
        """一张照片里**已经绑到人物实体**的那些脸（T122/T123 共用）。

        1.0 ``agent_runtime/tools.py:2277-2316`` 的 ``_confirmed_photo_identities``，
        那条 JOIN 原样：``face_instances ⋈ face_clusters ⋈ entities``，
        只留 ``entity_type = 'person'``。

        **名字与判据不一致，这是 1.0 的，照搬不修。** 那个函数叫
        ``_confirmed_photo_identities``、docstring 也写着 *confirmed*，而 SQL 里
        **一个状态过滤都没有**——两个 JOIN 都是 INNER，所以它判的是「**绑没绑**」：
        有的脸没有簇、有的簇没有实体，那些不出现；至于簇或实体是不是 ``confirmed``，
        它一次都没看过。2.0 里簇可以带 pending 实体（``enroll_face`` 就是「建簇带
        pending 实体」），所以这两个问题不是一回事。**顺手补一个
        ``status = 'confirmed'`` 是在改产品行为，不是修 bug。**

        排序也是 1.0 的：``quality DESC, detection_confidence DESC``（**没有第三键**，
        所以完全同分的两张脸取到哪一行由 SQLite 决定——照抄）。

        ``bbox`` 这一格是给 T123 的：``_inspect_photo`` 拿它去裁目标人物的定位图
        （``tools.py:2181-2192``）。T122 自己一个都不用，但两条工具共用这一条读路径，
        少带一格就会让 T123 再开一条几乎一样的查询。
        """
        ...

    def person_candidate_features(self, scope_id: str) -> Sequence[dict[str, Any]]:
        """核心人物排序的输入：这个空间里每个**未退休**的簇一行特征。

        1.0 ``db.py:6610`` 的原样。列名就是 ``domain/identity/insights.py`` 的
        ``core_person_score`` 读的那几个（``date_count`` / ``event_count`` /
        ``co_person_count`` / ``scene_count`` / ``quality`` / ``confirmed``），
        所以这个查询与那个打分函数是一对，改一边必须改另一边。

        **按簇出而不是按人出**（``person_id`` 取 ``COALESCE(c.entity_id, c.id)``）：
        还没确认的簇也要参与排序，否则一个刚聚类出来、还没被命名的人永远进不了画像
        ——而「提醒用户这里有个常在的人」正是这个功能的一半。

        返回的是**行形状的字典**，不是 Record：``rank_core_people`` /
        ``select_representatives`` 是照着 1.0 的行形状写的纯函数（见 T86d 的决策），
        在这一层再翻译一次 Record 只会多一层要同步的映射。
        """
        ...

    def representative_candidates(self, person_id: str) -> Sequence[dict[str, Any]]:
        """代表照选择的候选：这个人的簇里所有的脸。

        1.0 ``person_insights.py:262-270`` 的 SQL。``LEFT JOIN event_observations``
        是**故意的**：没有归到事件里的照片也要进来，否则一个人在「还没分段」的那批
        照片里就一张代表照都选不出来。代价是那些行的 ``event_id`` 为 ``None``，而
        ``select_representatives`` 的「每事件最多 per_event 张」会把它们全算作同一个
        （空）事件——1.0 原样，不修。
        """
        ...

    def best_instance_for_person_in_event(self, person_id: str, event_id: str) -> FaceInstanceRecord | None:
        """这个人在这个事件里质量最高的那张脸；没有就是 ``None``。

        1.0 ``app.py:2026-2028`` 的 SQL，逐字搬：质量优先、检测置信度次之。

        一个事件里同一个人可能被拍到二十次（``ObserveAppearance`` 的 docstring 写明
        为什么只取一张）。**只认经 ``entity_mentions`` 确认给了他的实例**——那与外观
        证据仓储的第三重守卫是同一条判据，这里先挡一次，好让「他不在这个事件里」与
        「模型没看清」在调用方看来是两件事。
        """
        ...

    def find_instance(self, cluster_id: str, instance_id: str) -> FaceInstanceRecord | None:
        """只在**这个簇里**找这张脸。拆分的合法性就是这一条：实例不属于该簇 → 404。"""
        ...

    def add_instance(
        self,
        *,
        asset_id: str,
        observation_id: str,
        cluster_id: str | None,
        bbox: Sequence[float],
        embedding: Sequence[float],
        detection_confidence: float,
        quality: float,
        pose: Sequence[float],
        pose_bucket_name: str,
        embedding_model: str,
        embedding_version: str,
        embedding_quality_signal: float,
        validity: str,
        area_ratio: float,
        sharpness: float,
    ) -> FaceInstanceRecord:
        """落一条人脸实例。``cluster_id`` 可空——那是「这张脸还算不上某个人」的表达。

        ``pose_bucket_name`` 收的是**算好的桶名**，不是算它的那个纯函数
        （``domain/identity/faces.pose_bucket``）——仓储不重算，重算就会有两份规则。
        """
        ...

    def create_cluster(
        self, embedding: Sequence[float], *, confidence: float, scope_id: str
    ) -> FaceClusterRecord:
        """新建一个簇，``entity_id`` 先留空。

        1.0 ``create_face_cluster`` 一次做三件事（建簇 → 建实体 → 回填 ``entity_id``），
        2.0 拆开：实体属于另一个聚合，在这里顺手写它会让「谁改了实体」在类型上消失。
        ``confidence`` 收的是**质量**——1.0 的形参就叫 confidence，同一条语句里传进去的
        却是 quality，两处语义一致，这里跟着叫 confidence 以免与第二个 0.30 混起来。
        """
        ...

    def link_entity(self, cluster_id: str, entity_id: str) -> FaceClusterRecord:
        """把簇挂到一个实体上（新簇建实体的那一步）。"""
        ...

    def confirm_cluster(self, cluster_id: str, entity_id: str) -> FaceClusterRecord:
        """簇转 ``confirmed`` 并回填归属，``revision + 1``。"""
        ...

    def retire_cluster(self, cluster_id: str, *, entity_id: str | None = None) -> FaceClusterRecord:
        """簇转 ``rejected``、``member_count`` 归零、``revision + 1``。

        ``entity_id`` 给了就一并改归属（同名并入一个已确认的人，``db.py:5230``），
        不给就保持原归属（用户把两个簇并成一个，``db.py:4790``）。
        1.0 那两条 SQL 的差别只有这一个字段，所以是一个方法而不是两个。
        """
        ...

    def move_instances(self, source_cluster_id: str, target_cluster_id: str) -> int:
        """把源簇的实例整体改挂到目标簇，返回移动条数（1.0 的合并只改这一列）。"""
        ...

    def move_instance(self, instance_id: str, target_cluster_id: str) -> None:
        """把一张脸移到另一个簇（拆分）。"""
        ...

    def active_cluster_for_entity(self, entity_id: str) -> FaceClusterRecord | None:
        """这个人当前**可以拿来确认**的簇：``pending`` 优先，其次 ``confirmed``，都要有成员。

        与 ``confirmed_cluster_for_entity`` 有两处**故意**不同，两处都照抄 1.0
        ``db.py:5258``：``status`` 收的是两种（先确认一个还没认过的簇，而不是只找已确认
        的），且 ``member_count > 0`` 是硬条件——空簇确认出来是一个没有任何脸的人。
        ``ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END`` 让待确认的簇排前面：
        用户按下确认时，想认的正是那个刚聚出来、还没归属的簇。
        """
        ...

    def confirmed_cluster_for_entity(self, entity_id: str) -> FaceClusterRecord | None:
        """这个人当前作为门面的已确认簇：``member_count DESC, updated_at DESC`` 取首个。

        同名合并要把实例挂到一个**已确认**的簇上，没有可挂的就不挂——1.0 允许
        ``target_cluster`` 为空，那时实例留在原地。
        """
        ...

    def list_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]: ...

    def list_verified_instances(
        self, *, scope_id: str | None, minimum_quality: float
    ) -> Sequence[FaceInstanceRecord]:
        """全局重聚类要过的那批脸。1.0 ``db.py:4961-4967`` 的四个条件，一个都不能少：

        - ``embedding_json != '[]'``——没有向量的脸不参与建簇；
        - ``cluster_id IS NOT NULL``——这张脸已经属于某个簇（整表重算算的是**已归簇**
          的那批，还没归属的由在线匹配那条路管）；
        - ``quality >= minimum_quality``——质量门；
        - ``validity = 'verified'``——复核过的脸才算数。少这一条，被复核判为
          ``uncertain`` 的脸会以「可信证据」的身份参与建簇，而它们正是最容易同时像
          好几个人的那一批。

        ``scope_id`` 为空表示跨空间（1.0 的 ``scope_id=None``）。
        """
        ...

    def assign_instances(self, instance_ids: Sequence[str], cluster_id: str) -> int:
        """把一批脸整体改挂到某个簇，返回改了几条。

        与 ``move_instances``（按源簇整体搬）不同：重聚类算出来的分组**不是**按源簇切的
        ——一个源簇的成员会被分到不同组里，所以只能按实例 id 逐个改。
        """
        ...

    def set_cluster_state(self, cluster_id: str, *, status: str, member_count: int) -> None:
        """直接写簇的状态与成员数，``revision + 1``，**不动** ``entity_id``。

        重聚类保留的那个簇（``keep_id``）要按算出来的分组改状态：组里有已确认的簇
        就是 ``confirmed``，否则 ``pending``。归属不在这条路上改——重聚类不改「这簇是
        谁」，只改「哪些脸在一簇」。
        """
        ...

    def list_uncertain_unclustered(self, *, scope_id: str | None) -> Sequence[FaceInstanceRecord]:
        """认领的候选脸：``validity='uncertain'``、有向量、且 ``cluster_id IS NULL``。

        1.0 ``_attach_uncertain_faces``（``db.py:5054-5060``）的三个条件。**只认领、
        绝不新建簇**：小图 / 遮挡检出的脸单独立簇会造出一堆只有一张脸的候选，
        而它们与已确认的人高度相似时其实是同一批人。
        """
        ...

    def list_confirmed_clusters(self, scope_id: str, *, minimum_members: int) -> Sequence[FaceClusterRecord]:
        """认领的目标：同空间、已确认、成员数不低于 ``minimum_members`` 的簇。

        1.0 ``_find_attach_cluster``（``db.py:5085-5088``）的字面量是 ``>= 2``——
        只有一张脸的「已确认」簇还没有稳定的代表向量，把不确定的脸挂上去等于放大噪声。
        """
        ...

    def refresh_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]:
        """按当前实例重算该簇的原型并回写 ``member_count``。

        先删后插：同一 ``pose_bucket`` 上留着旧行时，新行会撞 ``UNIQUE(cluster_id,
        pose_bucket)``。返回重算后的原型，省掉调用方再查一次。
        """
        ...


class EntityRepository(Protocol):
    """实体 / 修订 / 属性 / 提及。

    属性写入分两条路，这是 1.0 验证过的契约：``set_property`` 是**用户**写下当前值，
    ``maintain_property`` 是**派生**值入账。派生永远不许顶掉用户值。
    """

    def get(self, entity_id: str) -> EntityRecord | None: ...

    def list_entities(
        self, *, status: str | None = None, scope_id: str | None = None
    ) -> Sequence[EntityRecord]:
        """实体列表，不分类型。1.0 ``db.py:4041`` 的 ``list_entities`` 的前两段。

        空间规则与 ``FaceRepository.list_clusters`` 是同一条：``include_in_people = 0``
        的空间里的实体一律不出现——评测空间聚出来的实体不该出现在家庭实体目录里。
        ``status`` 为空时排除 ``rejected`` / ``superseded``（1.0 的字面量）。

        **按类型过滤不在这里。** 1.0 的 ``list_entities`` 不带类型参数，person 与非 person
        由两个路由各自在 Python 里分（``app.py:1514`` 的 ``/api/people``、``:1862`` 的
        ``/api/entities``）——「全部实体」与「家庭成员」是两个不同的产品问题，
        把它们合成一个参数会让「谁在过滤」变得说不清。

        方法名从 ``list_people`` 改回 ``list_entities`` 是**恢复 1.0 的形状**，不是新设计：
        2.0 早先把 ``entity_type = ?`` 绑死进 SQL，那是只服务人物列表时的收窄。
        """
        ...

    def recent_observation_ids(self, entity_id: str, *, limit: int = 100) -> Sequence[str]:
        """这个实体关联的观察 id，按 ``captured_at`` 降序，取前 ``limit`` 条。

        1.0 ``db.py:4590-4594`` 的 ``entity_observations ⋈ observations``，
        ``ORDER BY o.captured_at DESC LIMIT 100``。

        **只回 id，不回观察本体。** 1.0 那条 SQL 选的是 ``o.*``，但拿到行之后**只用了
        ``row["id"]``**——它紧接着对每一行再 ``get_observation`` 一次。2.0 不重复那个多余
        的投影，只取 id，由用例去取本体与 asset（1.0 那两层 N+1 的形状保留）。

        **一处照搬的 1.0 不确定性**：``captured_at`` 相同时 1.0 没有第二排序键，
        取到哪几条由 SQLite 的扫描顺序决定——同一个实体问两次，返回的**集合**都可能在
        边界上不同。照搬不修（改它要引入 1.0 没有的次序），但记在这里。
        """
        ...

    def decorate_entities(self, entity_ids: Sequence[str]) -> Mapping[str, EntityDecoration]:
        """批量取实体列表要用的派生字段。1.0 ``db.py:4056-4141`` 的 ``_decorate_entities``。

        **一次取一批，不是一条一查。** 1.0 的 docstring 写的就是这句
        （"with batched queries instead of N+1 lookups"）：七条聚合各自走一遍
        ``IN (...)``，簇的两种用途（计数、判两个布尔要用到的 ``status``/``member_count``）
        合并成一条。

        **只取数，不做判断。** 两个派生布尔与私密地点的投影在
        ``domain/identity/decoration.py`` 里算——那里的边界（``is True`` 不是真值、
        空簇列表下 ``all()`` 为真）值得单独穷举，混在 SQL 里就只能靠造库来验。

        返回的表**对每个传入的 id 都有一条**，没有任何一行数据的实体拿到全零——
        1.0 也是这个形状（它对每条实体 ``.get(id, 0)``）。传空列表时返回空表，不查库。

        ``relationship_count`` 来自 ``relationships`` 上的**两条**计数（subject 侧与
        object 侧各一条，相加），所以一条关系给两端各记 1。
        """
        ...

    def find_or_create_entities(
        self,
        specs: Sequence[EntitySpec],
        *,
        scope_id: str = DEFAULT_SCOPE_ID,
        confidence: float = 0.0,
    ) -> Mapping[tuple[str, str], EntityRecord]:
        """按 ``(entity_type, name)`` 批量找或建，返回 ``(类型, 名字) → 实体`` 的表。

        1.0 ``db.py:3708-3749`` 的 ``_find_or_create_entities_batch``，逐条对齐：

        1. **先按 ``(类型, 名字)`` 去重、丢掉空名字**——同一张照片里同一个物体写两遍
           不该建两个实体；
        2. **按类型分组去查**（``canonical_name IN (...)``），一次一类而不是一行一查；
        3. 命中的按 ``active → confirmed → 其余`` 的偏好排序取第一个（1.0 的
           ``ORDER BY CASE status ...``）——同名有多个时优先那个已经在用的；
        4. **命中的抬置信度**（``MAX(confidence, ?)``），**没命中的按 ``pending`` 新建**。

        ``status != 'rejected'`` 是查询条件的一部分：被否掉的实体不该被复用。
        查不到是**按类型批量查的**，不是逐个 ``get``——一张照片有几十个物体时，
        逐个查就是几十次往返。

        **这是 2.0 唯一一处给非人物实体建行的地方。** 在此之前只有 ``create_person``
        （它的 docstring 写着「2.0 只收 person 这一种，因为本切片没有别的调用者」）——
        S6 的 T127 就是那个调用者。
        """
        ...

    def link_observation(
        self,
        entity_id: str,
        observation_id: str,
        *,
        confidence: float = 0.0,
        source: str = "observation_extraction",
    ) -> None:
        """把「这条观察提到了这个实体」记进 ``entity_observations``。

        1.0 ``db.py:3831-3837``：``INSERT ... ON CONFLICT(entity_id, observation_id)
        DO UPDATE SET confidence = MAX(confidence, excluded.confidence)``——**同一条观察
        重跑时抬置信度、不重复插入**，那是「重跑导入不产生重复行」的一半。

        **在 S6 之前这张表一个写入者都没有**（2.0 只有一处读：``entities.py:734``
        取某个实体的事件 id）。T127 是它的第一个写入者。
        """
        ...

    def create_person(
        self,
        name: str,
        *,
        status: str = ENTITY_STATUS_PENDING,
        family_role: str | None = None,
        confidence: float = 0.0,
        summary: str = "",
        scope_id: str = DEFAULT_SCOPE_ID,
    ) -> EntityRecord:
        """建一个 person 实体并返回它。

        ``name`` 是**内部名**（新簇的实体叫「待确认人物簇 · cluster-x」），对外叫什么
        由出口决定——``pending`` 的实体在 ``list_clusters`` 里一律显示成「待命名成员」。
        1.0 的 ``create_entity`` 是通用的；2.0 只收 person 这一种，因为本切片没有别的
        调用者，写成通用签名等于给一个没人用的参数留位置。
        """
        ...

    def confirm_person(
        self,
        entity_id: str,
        *,
        name: str,
        family_role: str | None,
        summary: str,
    ) -> EntityRecord:
        """确认一个人：置名、转 ``confirmed``、写家庭角色，``confidence = MAX(…, 1)``。

        ``family_role`` 是**覆盖**不是补写（1.0 ``db.py:5163`` 的 ``family_role = ?``）：
        确认时不带角色，会把上一次的角色抹掉。这是 1.0 的行为，照搬。
        """
        ...

    def delete_candidate(self, entity_id: str) -> bool:
        """彻底删掉一个未确认的候选实体与它的派生行，返回是否删掉了。

        人脸实例**保留**（它们是证据），只是 ``cluster_id`` 归 NULL。已确认的实体抛
        ``ValueError``——删一个人要用改名或合并，不是删行。非 person 实体返回 ``False``。
        """
        ...

    def find_confirmed_person_by_name(self, scope_id: str, name: str) -> PersonNameMatch | None:
        """本 scope 内 ``entity_type='person' AND status='confirmed'`` 的人。

        **先精确名，再遍历 aliases**：反过来的话，某人的别名会抢走与之同名的另一个人。
        """
        ...

    def record_revision(
        self,
        entity_id: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        *,
        source: str,
        evidence_ids: Sequence[str] = (),
    ) -> EntityRevisionRecord:
        """追加一行审计。调用它一次就写一行，不做去重。"""
        ...

    def list_properties(
        self, entity_id: str, *, include_history: bool = False
    ) -> Sequence[EntityPropertyRecord]:
        """``include_history`` 为假时只返回 ``active`` / ``pending`` 的行。"""
        ...

    def maintain_property(
        self,
        entity_id: str,
        property_key: str,
        value: object,
        *,
        confidence: float = 0.0,
        evidence_ids: Sequence[str] = (),
        source: str = PROPERTY_SOURCE_DERIVED,
    ) -> EntityPropertyRecord:
        """写入一个派生值，返回**写入后当前生效**的那一行。

        当前值是用户值 → 原样返回它、不写任何行；与当前派生值相同 → 只抬置信度并合并
        证据；不同 → 新起一行 ``status='pending'``，把被取代的那行记进
        ``supersedes_property_id``。
        """
        ...

    def maintain_property_values(
        self,
        entity_id: str,
        property_key: str,
        values: Sequence[object],
        *,
        confidence: float = 0.0,
        evidence_ids: Sequence[str] = (),
        source: str = PROPERTY_SOURCE_DERIVED,
    ) -> EntityPropertyRecord:
        """多值属性的**合并**写入。1.0 ``db.py:4728-4744``。

        与 ``maintain_property`` 的差别是**合并而不是改口**，三处口径都照 1.0：

        1. **用户值优先**：当前值是 ``source='user'`` 就原样返回、一个字节都不写；
        2. **并集去重、丢掉空项**：``dict.fromkeys(existing + values)``——1.0 用字典**保序**
           去重，不是 ``set``（集合会打乱顺序，而这份列表要进提示词）；
        3. **已有值时就地 UPDATE，不新起 ``pending`` 行**。这一条与 ``maintain_property``
           相反，也是它必须单独存在的原因：多值属性（``semantic_details`` /
           ``raw_atmosphere_labels``）是**累积的证据**，每张照片多一条就起一条待确认的
           「改口」行，界面会堆满永远没人确认的修订。

        为什么 2.0 现在才加：``s5-deviations.md`` 第 46 条记过它在 1.0「本切片没有
        调用者」——**唯一那个调用者是语义实体抽取（T127）**。
        """
        ...

    def set_property(
        self,
        entity_id: str,
        property_key: str,
        value: object,
        *,
        evidence_ids: Sequence[str] = (),
    ) -> EntityPropertyRecord:
        """用户写下当前值：旧行转 ``superseded``、新行 ``source='user'``，并留一条修订。"""
        ...

    def person_aliases(self, entity_id: str) -> tuple[str, ...]: ...

    def set_person_aliases(self, entity_id: str, aliases: Sequence[str]) -> tuple[str, ...]:
        """整表替换别名，空串与重复项被清掉。实体不存在时返回空。"""
        ...

    def rename_person(self, entity_id: str, new_name: str) -> EntityRecord | None:
        """改一个 person 实体的本名：把旧名折进 aliases、``name_state`` 转 confirmed、
        写一条 ``source='user_rename'`` 的修订。非 person 实体返回 ``None``，空名字抛
        ``ValueError``——空名字不是「不改名」，是调用方出错。"""
        ...

    def link_confirmed_mention(
        self,
        entity_id: str,
        observation_id: str,
        *,
        face_instance_id: str | None = None,
        confidence: float = 0.0,
    ) -> EntityMentionRecord:
        """写一条提及，并把这个人追加进观察的人物快照。

        只接受 ``status='confirmed'`` 的实体，未确认的候选人物抛 ``ValueError``：
        这个快照是事件 ``person_score`` 与 ``person_bridge`` 词法投影的输入，混进一个
        尚未确认的候选，等于让聚类的一次猜测变成检索事实。重复调用幂等，返回既有行。
        """
        ...

    def confirmed_person_names(self, scope_id: str) -> frozenset[str]:
        """本 scope 内已确认人物的**本名**集合。1.0 ``is_confirmed_person_name``（``db.py:1494``）。

        这是事实写入门禁（``MaintainFacts`` 的第二段合取）的取值来源。三条判据照抄：
        ``entity_type='person'``、``status='confirmed'``、``canonical_name`` **精确相等**。

        **不查别名**，这不是遗漏：某人的别名常常是另一个人的本名（家里人互相叫小名），
        按别名放行会让模型自报的一条事实落到错的人头上，而事实表按
        ``(scope, subject, predicate)`` 唯一——写错之后连「这条是谁的」都看不出来。
        """
        ...

    def confirmed_people_for_observation(self, observation_id: str) -> frozenset[str]:
        """这条观察里**已确认**的人物 id。1.0 ``db.py:2241``。

        只认 ``entity_type='person' AND status='confirmed'``：未确认的候选人物簇还只是
        一次聚类猜测，让它进事件打分等于用猜测去归并事件。``entity_mentions`` 本身也只
        由已确认实体写得出（``link_confirmed_mention`` 的资格闸门），这一条是第二道。

        返回 ``frozenset`` 不是 ``Sequence``：调用方拿它做交集（``people & event_people``），
        顺序没有意义。
        """
        ...

    def confirmed_people_for_event(self, event_id: str) -> frozenset[str]:
        """这个事件里**已确认**的人物 id：事件名下所有观察的提及并集。1.0 ``db.py:2250``。

        与按观察查的那条差别只在 JOIN 的那一跳——事件的人物是它的成员观察带进来的，
        所以两边用的是同一份提及数据，不会出现「观察里有、事件里没有」的第三种状态。
        """
        ...

    def retire_unclustered_candidates(self) -> int:
        """把已经没有活着的人脸簇的候选人物转 ``rejected``，返回转了几行。

        1.0 ``_sync_face_entity_statuses``（``db.py:5118-5132``）：全局重聚类会把一些簇
        整个撤掉（成员被并走、或全被判成 noise），那些簇的候选实体就成了孤儿——
        它们还在人物列表里显示成「待命名成员」，点进去一张脸都没有。
        **已确认的人永远不动**：用户认过的人不会因为一次聚类重算而消失。转状态而不是
        删行，理由写在 ``RETIRED_CANDIDATE_SUMMARY`` 那里。

        跨空间是**有意**的：1.0 的循环没有 scope 条件，而实体与簇都在同一次重聚类里
        被改过，只看一个空间会留下一半孤儿。没有参数也就没有「传错了 scope」这种错。
        """
        ...

    def event_ids(self, entity_id: str) -> Sequence[str]:
        """这个人牵涉到的事件：提及桥 / 事件参与者投影 / 实体-观察关联三条来源的并集。"""
        ...


class PortraitRepository(Protocol):
    """画像修订的读写。SQL 在 ``infrastructure/persistence/repositories/portraits.py``。

    **写入口有一条否决权**：``create`` 在当前生效版本是 ``user_locked`` 时
    **直接返回当前版本、不写新行**（1.0 ``db.py:6380``）。这是用户在画像上唯一的
    否决权，所以它必须是写入口的判据，而不是界面上的一个禁用按钮——界面上禁用了，
    别的调用方（导入后的自动触发）照样会写进去。
    """

    def get(self, revision_id: str) -> PortraitRevisionRecord | None:
        """按 id 取一版。找不到返回 ``None``（由调用方决定这是 404 还是别的）。"""
        ...

    def get_active(self, person_id: str) -> PortraitRevisionRecord | None:
        """当前生效的那一版：``status IN ('active', 'user_locked')`` 里 ``revision`` 最大的一条。

        **``user_locked`` 也算「生效」**：锁住的是「这一版别被顶掉」，不是「这一版作废」。
        """
        ...

    def list_for_person(self, person_id: str) -> Sequence[PortraitRevisionRecord]:
        """这个人的全部修订，按 ``revision DESC``。"""
        ...

    def create(
        self,
        person_id: str,
        *,
        portrait_text: str,
        prompt_version: str,
        themes: Sequence[dict[str, object]] = (),
        evidence_refs: Sequence[dict[str, object]] = (),
        trigger_type: str = PORTRAIT_TRIGGER_MANUAL,
        model_name: str = "unknown",
    ) -> PortraitRevisionRecord:
        """落一版新画像，并把上一版转 ``superseded``。

        ``revision`` 是上一版 +1；没有上一版时从 1 开始。
        **当前版本 ``user_locked`` 时不写新行，原样返回它。**
        """
        ...

    def set_feedback(self, revision_id: str, feedback: str | None) -> PortraitRevisionRecord | None:
        """记用户对某一版的反馈（不新建版本）。"""
        ...

    def set_lock(self, revision_id: str, locked: bool) -> PortraitRevisionRecord | None:
        """锁 / 解锁一版。锁上转 ``user_locked``、解开转回 ``active``（1.0 ``db.py:6431``）。"""
        ...


class MomentRepository(Protocol):
    """人物瞬间的读写。SQL 在 ``infrastructure/persistence/repositories/moments.py``。

    **跨空间是一条硬不变量**：一条瞬间引用的六个对象（人 / 簇 / 事件 / 观察 /
    资产 / 人脸实例）必须同属一个记忆空间，否则 ``upsert`` 抛 ``ValueError``
    （1.0 ``db.py:6176``）。一处瞬间牵扯两个空间意味着上游的归属算错了——
    这种错不会自己暴露，只会让画像把两个空间的人混成一个。
    """

    def upsert(
        self,
        *,
        person_id: str,
        observation_id: str,
        prompt_version: str,
        cluster_id: str,
        event_id: str,
        asset_id: str,
        face_instance_id: str,
        action_text: str = "",
        interaction_target_ids: Sequence[str] = (),
        interaction_text: str = "",
        participation_style: str = "",
        visible_affect: str = "",
        social_role_cues: Sequence[str] = (),
        narrative_note: str = "",
        selection: Mapping[str, object] | None = None,
        confidence: float = 0.0,
        model_name: str = "unknown",
    ) -> PersonMomentRecord:
        """落一条瞬间。同一 ``(person_id, observation_id, prompt_version)`` 再写就是更新。

        更新**不含** ``scope_id`` / ``person_id`` / ``observation_id`` /
        ``prompt_version``——那四个是 upsert 的键，改了就不是同一条了。
        """
        ...

    def list_for(
        self,
        *,
        person_id: str | None = None,
        scope_id: str | None = None,
        status: str = "active",
    ) -> Sequence[PersonMomentRecord]:
        """按条件列瞬间，``created_at DESC``。``status=None`` 表示不按状态筛。"""
        ...


class RelationshipRepository(Protocol):
    """关系（家庭图谱的边）的读写。SQL 在 ``repositories/relationships.py``。

    **只写 ``relationships`` 行。** 1.0 的 ``create_relationship`` 在
    ``status == 'active'`` 时还会调 ``maintain_relationship_claim`` 往事实/声明层写；
    2.0 把那一半留给 application 编排（``infrastructure-purity`` 契约不允许这一层
    直接调用例）。**且不回滚**：claim 维护失败时这一行留着——「关系已确认但事实层
    没有对应声明」是可能出现且被接受的中间状态。
    """

    def list_person_relationships(self, scope_id: str | None = None) -> Sequence[RelationshipRecord]:
        """家庭图谱的边：**两端都是 person 实体**、且 ``status != 'retracted'``。

        两端都是 person 这条过滤在 SQL 里（1.0 ``db.py:5603``）：实体表里还有物品、
        氛围等类型，只按 id 取会把它们一起捞进来。
        """
        ...

    def list_relationships(self, scope_id: str | None = None) -> Sequence[RelationshipRecord]:
        """一个空间里的**全部**关系，**不过滤 status**。

        1.0 ``db.py:5590`` 的 ``list_relationships`` 有两条分支：给了 ``entity_id`` 走
        下面那条 ``list_for_entity``，**不给就是这一条**——``GET /api/relationships``
        不带 ``kind=person`` 时走的正是它。它与 ``list_person_relationships`` 的口径不同：
        那个排 ``retracted``，这个连 ``retracted`` 都给（1.0 同样是两条 SQL）。

        ``scope_id`` 为空时**跨空间全给**——1.0 的守卫是 ``if scope_id:``。
        """
        ...

    def list_for_entity(self, entity_id: str, scope_id: str | None = None) -> Sequence[RelationshipRecord]:
        """这个人牵涉到的关系，**两个方向都算**（它可能是 subject 也可能是 object）。"""
        ...

    def create(
        self,
        *,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        evidence_ids: Sequence[str] = (),
        confidence: float = 0.5,
        status: str = "pending",
    ) -> RelationshipRecord:
        """建一条关系，或把已有的那条并掉。

        同一 ``(scope, subject, predicate, object)`` 已有 ``active`` / ``pending``
        的，**不新建**：证据并集、置信度取 ``MAX``、``revision + 1``。
        两端不在同一个记忆空间时抛 ``ValueError``。
        """
        ...

    def confirm(self, relationship_id: str) -> RelationshipRecord | None:
        """确认：把**同 (subject, predicate, object) 的其它 active 关系转 superseded**，
        再把这一条转 active。同一对人在同一谓词上只能有一条生效的。
        """
        ...

    def retract(self, relationship_id: str) -> RelationshipRecord | None:
        """撤回：转 ``retracted`` 并 ``revision + 1``。**不删行**——撤回也要留痕。"""
        ...


class PersonCropRenderer(Protocol):
    """按人脸框裁出「头 + 上半身」并落成临时 JPEG。实现在
    ``infrastructure/media/person_vision.py``。

    **存在的理由就是分层契约。** ``application-purity`` 禁止 application import
    infrastructure，而裁剪要 PIL——几何本身是纯的（``domain/identity/appearance.py``
    的 ``expanded_person_crop`` 收的是一个只有 width/height/crop 的 Protocol），
    但「打开哪个文件、写哪个临时文件」是 IO，只能在 infrastructure。同
    ``PillowMetadataReader`` / ``PillowPreviewEncoder``：那两个类的名字里带 Pillow
    不是啰嗦，是这条契约的凭证。

    返回 ``(临时图路径, 源坐标)``。**临时文件由调用方删**——它就活在这一次模型调用里
    （``person_vision`` 的 docstring 写明：裁剪件从不作为 Asset 或长期文件保存）。

    ``ValueError`` 照常抛（框退化 / 图太小），由调用方决定这算不算一次失败：
    在 ``ObserveAppearance`` 里算——那张照片跳过，整批继续。
    """

    def render(self, asset_path: str | Path, bbox: Sequence[float] | None) -> tuple[str, list[int]]: ...


class PersonPreviewRenderer(Protocol):
    """在一张原图上画出人脸框与 ``P1`` / ``P2`` 标签，返回
    ``(临时图路径, 标签 → 身份)``。实现在 ``infrastructure/media/person_vision.py``。

    与 ``PersonCropRenderer`` 同一条理由（``application-purity`` 不许 application
    import infrastructure，而画框要 PIL），但两件事不一样：那个裁**一个人**给外观证据
    用，这个画**一屋子人**给瞬间抽取用——模型要靠标签才能把 ``interaction_labels`` 里
    的 ``P2`` 指回具体某个人。所以后者返回的那张表是**归属的唯一依据**，不是附带信息。

    没有框的人脸按 1.0 的做法把标签画在左上角依次排下来，**而不是丢掉这一项**——
    丢了他，模型会看到一个跳号的 ``P1``、``P3``，然后开始猜 ``P2`` 去哪了。

    **临时文件由调用方删。** ``ValueError`` / ``OSError`` 照常抛，由调用方决定这算不算
    一次失败（``ExtractMoments`` 里算：那张照片跳过，整批继续）。
    """

    def render(
        self, asset_path: str | Path, faces: Sequence[Mapping[str, Any]]
    ) -> tuple[str, dict[str, dict[str, Any]]]: ...


class PersonVision(Protocol):
    """人物视觉的三个模型入口：画像 / 瞬间 / 外观。实现在 ``infrastructure/media/person_vision_model.py``。

    三条方法各喂一条链路，**都不在导入的前置路径上**：模型不可用、读不了图、返回的不是
    JSON——一律返回 ``None``。1.0 把这三条写在同一个客户端上
    （``backend/model_clients.py:1291`` / ``:1316`` / ``:1332``），2.0 收成端口，是为了让
    用例拿到的是一句「谁会调模型」，而不是一个能问端点要模型名的具体类。

    三者的输入输出形状各按 1.0：

    - ``write_person_portrait`` 收 ``portrait.compile_portrait_evidence`` 的证据包，
      返回 ``normalize_writer_output`` 的形状（``portrait_text`` + ``themes``）——校验与
      兜底不在这一步，仍由 ``portrait.py`` 那两个纯函数决定；
    - ``analyze_person_moments`` 收带编号预览图（``person_vision.render_numbered_preview``
      的产物）与本次真实画出的标签表，返回 ``{"moments": [...]}``，其中每条已经过了一遍
      ``normalize_person_moments`` 的词表；
    - ``analyze_person_appearance`` 收 ``appearance.expanded_person_crop`` 的产物，返回
      ``{"clothing": [...], "confidence": float}``。

    ``model_id`` 是**这次会请求的模型名**，供落库时记 ``model_name``
    （``MomentRepository.upsert``）。1.0 把 ``self.model`` 塞在
    ``analyze_person_appearance`` 的返回值里（``:1350``），2.0 提到端口上——与
    ``VlmObserver.model_id`` 同一条口径。
    """

    @property
    def model_id(self) -> str: ...

    def write_person_portrait(self, pack: Mapping[str, object]) -> dict[str, object] | None: ...

    def analyze_person_moments(
        self,
        path: str,
        labels: Sequence[str],
        context: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None: ...

    def analyze_person_appearance(
        self,
        path: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None: ...


class AppearanceRepository(Protocol):
    """外观证据的读写。SQL 在 ``infrastructure/persistence/repositories/appearance.py``。

    **写入口有三重守卫**（1.0 ``db.py:4485-4497``）：人存在且已确认、人脸实例存在、
    以及**这个实例真的经 ``entity_mentions`` 确认给了这个人**。第三重挡的是
    「把 A 的衣服记到 B 头上」——那正是这个功能最要紧的一种错。

    唯一键是 ``(person_id, face_instance_id, model_name)``：换模型是**另一条**证据，
    不覆盖旧模型的结论。
    """

    def record(
        self,
        *,
        person_id: str,
        face_instance_id: str,
        crop_bbox: Sequence[float],
        clothing: Sequence[str],
        confidence: float,
        model_name: str,
    ) -> AppearanceEvidenceRecord:
        """记一条，或覆盖同一 ``(人, 实例, 模型)`` 的那条。

        ``observation_id`` / ``asset_id`` **从人脸实例带出来**，不由调用方传——
        传进来的可能与实例对不上，而那种不一致事后从证据行上看不出来。
        """
        ...

    def get(self, evidence_id: str) -> AppearanceEvidenceRecord | None: ...

    def list_for(
        self, person_id: str, *, include_empty: bool = False, limit: int = 500
    ) -> Sequence[AppearanceEvidenceRecord]:
        """这个人的外观证据，置信度降序。

        ``include_empty=False`` 会滤掉 ``clothing`` 为空表的那些——**空表不是
        「没穿衣服」，是「模型没能可靠归属给目标人物」**。
        """
        ...


class SemanticRepository(Protocol):
    """语义画像与声明。读侧是 S6 · T130，写侧是 S7a · T206。

    两张表在 2.0 从 T127 建起来之后一直**没有任何读写路径**，这个端口是它们的第一个
    读者。**写侧只有一条路**：``maintain_claim``——关系被确认时那句「写入长期记忆」。
    画像那一套的写入者 ``rebuild_person_memory`` 不在 2.0（2.0 的画像写的是
    ``person_portrait_revisions``，与这两张表不是一套），**那是 S6 之前就存在的分叉**。
    """

    def get_profile(self, person_id: str) -> SemanticProfileRecord | None:
        """1.0 ``db.py:2614-2616``：``SELECT * FROM semantic_profiles WHERE person_id = ?``。

        参数名与列名都叫 ``person_id``，但**传进来的可以是任意实体的 id**——1.0 的
        ``get_entity_detail`` 与 ``/api/knowledge`` 都这么用（前者不判类型）。
        """
        ...

    def list_claims(self, person_id: str | None = None, *, limit: int = 500) -> Sequence[SemanticClaimRecord]:
        """1.0 ``db.py:2710-2716`` 的 ``list_semantic_claims``。

        **恒带 ``status IN ('active', 'pending')`` 过滤**——那是 1.0 的默认参数
        （``include_history=False``），也是唯一被用到的取值。1.0 还有 ``include_history``
        分支，2.0 没有调用者需要它，所以不搬那个参数（不是漏）。
        ``person_id`` 为空表示全表，仍受 ``limit`` 约束。
        """
        ...

    def maintain_claim(
        self,
        *,
        person_id: str,
        scope_id: str,
        dimension: str,
        predicate: str,
        value_text: str,
        evidence_ids: Sequence[str] = (),
        event_ids: Sequence[str] = (),
        confidence: float = 0.5,
        confidence_source: str = "derived",
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> SemanticClaimRecord:
        """写一条语义声明，或把同键已有的那条并掉。1.0 ``db.py:2654-2710``。

        **合并的键是四元组** ``(person_id, dimension, predicate, COALESCE(normalized_value_text,
        value_text))``，且只在 ``status IN ('active','pending')`` 里找——命中了就
        **不新建**：证据并集、``confidence`` 取 ``MAX``、``confidence_source`` 覆盖、
        ``support_count`` 取 ``max(旧的, 支持事件数)``、``updated_at`` 刷新。

        没命中才插新行。新行的 ``status`` 由维度决定：**多值维度**
        （activity / place / clothing / capture / attendance）恒 ``active``；其余维度
        （含 ``relationship``）在**同 (person, dimension, predicate) 已有 active 行**时
        落 ``pending`` 并记下 ``supersedes_claim_id`` 与 ``revision = 旧 + 1``。

        ``predicate`` 会先过一遍 1.0 的九条别名表（``db.py:2655-2659``）。

        **``event_ids`` / ``valid_from`` / ``valid_to`` 三个参数 2.0 目前没有调用者传**
        （关系那条路三个都空），留着是因为它们参与合并的 SQL 与 ``support_count`` 的算法；
        等画像那一支落地时直接接上，不用回头改仓储。
        """
        ...


class FamilyGraphRepository(Protocol):
    """家庭图谱六张表的读写。SQL 在 ``repositories/family.py``。

    **它与 ``RelationshipRepository`` 是两套，不要合并。** 那张通用表（S7a）的
    ``predicate`` 是自由文本、没有白名单、没有 ``locked``、没有 ``state`` 版本链；
    这一套是**受约束**的：标签必须过 ``family_graph.py`` 的白名单，每行带
    ``state`` + ``locked``，而且**一条边在库里是两行**（正 + 反）。

    **``locked`` 是这套表的中心。** 用户写下的行 ``locked = 1``，模型的推断遇到它
    就跳过（``set_membership`` / ``write_portrait`` 直接返回已有行、
    ``set_relationship`` 返回空列表），撤回过的边另外在
    ``family_relationship_blocks`` 里留一行挡着。**这三条「跳过」都不报错**——
    静默保留用户的选择就是它们要的效果，所以每一条都有用例钉。
    """

    # ---- 读投影 ----------------------------------------------------------------

    def family_graph_people(self, scope_ids: Sequence[str]) -> Sequence[FamilyPersonProjection]:
        """这张图里**全部人物**的展示投影。1.0 ``db.py:5649-5688`` 的 ``family_graph_people``。

        **三个派生字段各有自己的一套查询，与 ``EntityRepository.decorate_entities``
        不是一回事**（三处都不同，别想着复用）：

        - 头像的排序多一位 ``fi.quality DESC``（那边的头像不看 quality）；
        - ``representative_media`` 走的是「这个人的脸出现在哪些照片」
          （``face_instances⋈face_clusters⋈assets``）+ ``ORDER BY a.id DESC LIMIT 3``，
          而那边的 ``preview_*`` 走的是「这个实体最新的观察」——**连接路径、条数、排序三处都不同**；
        - ``appearance_count`` 数的是**人脸实例行数**，那边是 ``COUNT(DISTINCT a.id)``。

        ``display_name`` 的占位是 **「待命名人物」**——1.0 这个函数里硬编码的就是这个词，
        与 ``app.py`` 那处的「待命名成员」不是同一个串。
        """
        ...

    # ---- 成员归属 --------------------------------------------------------------

    def get_effective_membership(self, scope_id: str, entity_id: str) -> FamilyMembershipRecord | None:
        """这个人在这个 scope 里当前生效的归属。

        ``state = 'effective'`` 且按 ``locked DESC, revision DESC, updated_at DESC``
        取第一行——**三个排序键缺一不可**：``locked`` 让用户写的压过模型的，
        ``revision``/``updated_at`` 在没有 locked 的行之间定先手。
        """
        ...

    def set_membership(
        self,
        scope_id: str,
        entity_id: str,
        membership: str,
        *,
        source: str,
        confidence: float = 0.0,
        evidence_refs: Sequence[str] = (),
        inference_run_id: str | None = None,
    ) -> FamilyMembershipRecord | None:
        """写一条归属：旧行置 ``superseded``，新行 ``effective`` 且回填 ``supersedes_id``。

        **三条边界，两条是静默的**：

        - ``membership`` 不在 ``FAMILY_MEMBERSHIPS`` 或 ``source`` 不在
          ``FAMILY_SOURCES`` → ``ValueError``（这两条会报错）；
        - 实体不是 person、或与 scope 不符 → ``ValueError``；
        - **``source='model'`` 且当前行 ``locked`` → 不写、不 commit，直接返回当前行**
          ——用户改过的归属，模型不许覆盖。返回值与「刚写成功」长得一样，
          调用方分不出来，这是 1.0 原样。

        新行的 ``locked`` 是 ``source == 'user_override'``。
        """
        ...

    # ---- 关系边 ----------------------------------------------------------------

    def list_effective_relationships(
        self, scope_id: str, entity_id: str | None = None
    ) -> Sequence[FamilyRelationshipRecord]:
        """这个 scope 的全部有效边；给了 ``entity_id`` 就只要他牵涉的（两个方向都算）。

        排序 ``locked DESC, updated_at DESC``。**只有 ``effective``**——``retracted``
        与 ``superseded`` 都不返回（这与通用 ``relationships`` 那边「连 retracted 都给」
        相反，别照那边改）。
        """
        ...

    def set_relationship(
        self,
        scope_id: str,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        inverse_predicate: str,
        *,
        source: str,
        confidence: float = 0.0,
        evidence_refs: Sequence[str] = (),
        inference_run_id: str | None = None,
    ) -> Sequence[FamilyRelationshipRecord]:
        """写一对亲属边——**一次落两行**（正向 + 反向），返回那两行。

        校验顺序是行为的一部分（1.0 ``db.py:5808-5863``），前六道会抛、后两道静默：

        1. ``source`` 合法；2. 两端不是同一个人；3. 两端都是同 scope 的 person 实体；
        4. 两个标签 strip 后非空；5. **``validate_relationship_pair`` 过白名单**；
        6. ``source='model'`` 且这对人被 block 过 → **返回空**（用户撤回过，模型不许恢复）；
        7. ``source='user_override'`` 且存在 block → **先删掉那一行**（用户重新建边即解锁）；
        8. ``source='model'`` 且当前有效边里有 ``locked`` 的 → **返回那些行**，不写。

        通过之后：**先把这一对的双向旧边全部置 ``superseded``，再插两行新边**
        ——顺序反了会留下两代并存的 ``effective``。新行的 ``family_graph_id`` 与
        ``supersedes_id`` **恒为 NULL**（1.0 原样，见迁移 0009 的注释）。
        """
        ...

    def retract_relationship(
        self, scope_id: str, subject_entity_id: str, object_entity_id: str
    ) -> Sequence[FamilyRelationshipRecord]:
        """撤回一对人的边，**并落一行 block 挡住模型恢复**。

        两步的顺序不能反：先双向置 ``retracted``，再 UPSERT 那一行 block
        （``source='user_override'``、``locked=1``）。返回的是**撤回之后**的行。
        """
        ...

    # ---- 家庭画像 --------------------------------------------------------------

    def get_active_portrait(self, scope_id: str, entity_id: str) -> FamilyPortraitRecord | None:
        """当前生效的那一段家庭画像。

        排序是 ``created_at DESC LIMIT 1``——**与 membership 的三个排序键不一样**
        （1.0 就是两个写法，见 §4b.3）。照搬。
        """
        ...

    def write_portrait(
        self,
        scope_id: str,
        entity_id: str,
        portrait_text: str,
        *,
        source: str,
        evidence_refs: Sequence[str] = (),
        inference_run_id: str | None = None,
    ) -> FamilyPortraitRecord | None:
        """写一段家庭画像：旧行 ``superseded``，新行 ``effective``。

        **不校验 ``source`` 白名单**——1.0 这个函数就是只按 ``source == 'model'``
        分支，非法值会原样落库（对比 ``set_membership`` / ``set_relationship`` 都会校验）。
        照搬这个缺口，别顺手补。

        ``source='model'`` 且当前行 ``locked`` → 不写，返回当前行。
        """
        ...

    # ---- 模型边的清场 + 运行 ----------------------------------------------------

    def supersede_model_relationships(self, scope_id: str) -> None:
        """把本 scope 里**模型写过且未锁定**的有效边全部置 ``superseded``。

        **三重过滤** ``state='effective' AND source='model' AND locked=0``——
        每一轮推断开始前清场，**用户写的边一条都不动**。少任何一个条件都会把用户的边
        也清掉，而且不报错。1.0 ``db.py:5782``。
        """
        ...

    def create_run(self, scope_id: str, config: Mapping[str, object]) -> FamilyAnalysisRunRecord:
        """开一次推断运行：``status`` / ``current_stage`` 都是 ``queued``，``stats`` 空。

        ``config`` 是**这次为什么跑**（1.0 里有 ``trigger_type`` = ``ingest`` /
        ``user_rerun``，以及 ``input_mode``）。它是留痕，不是判据。
        """
        ...

    def get_run(self, run_id: str) -> FamilyAnalysisRunRecord | None: ...

    def latest_run(self, scope_id: str) -> FamilyAnalysisRunRecord | None:
        """最近一次运行，按 ``created_at DESC``——**不是 ``updated_at``**。

        触发去抖读它的 ``stats['event_watermark']``：完成时写入当时的事件数，
        下次触发时事件数没涨就不跑。**失败时 stats 保持空**，于是 watermark 缺失、
        下次一定重跑——那是有意的。
        """
        ...

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        stats: Mapping[str, object] | None = None,
        error: str | None = None,
    ) -> FamilyAnalysisRunRecord | None:
        """推进一次运行。**``None`` 表示「不改这一项」**，不是「写成 NULL」——
        所以 ``error`` 一旦写上就清不掉（1.0 同样做不到）。``status='completed'`` 时
        额外写 ``completed_at``。
        """
        ...

    def family_text_evidence(self, scope_id: str) -> Sequence[FamilyEvidenceRow]:
        """这条 scope 里**未被拒的人物**的全部「脸 × 观察」行。1.0 那条大查询。

        四个 JOIN 与两个过滤逐条照搬：``face_instances ⋈ face_clusters ⋈ entities ⋈
        observations``（``entity_mentions`` 是 LEFT JOIN，只为了拿一个更好的置信度），
        过滤是 ``o.scope_id = e.scope_id = ?``、``entity_type = 'person'``、
        ``status NOT IN ('rejected', 'superseded')``，排序 ``o.created_at ASC``。

        **返回原始行而不是拼好的文本**：拼文本是判断（用哪几列、怎么连接、截断多少），
        归用例；这一层只负责把行取回来。
        """
        ...

    # ---- 跨 scope 的图 ---------------------------------------------------------

    def merge_scopes(self, scope_ids: Sequence[str]) -> tuple[str, Sequence[str]]:
        """把若干 scope 并成一张图，返回 ``(graph_id, 这张图下的全部 scope_id)``。

        少于两个 scope、或有不存在的 scope → ``ValueError``。**已属于某张图的 scope
        会被并进那张图**（取这些 scope 里已有的 ``graph_id``，没有才新建），
        所以这个操作是幂等的、也是可累积的。
        """
        ...

    def graph_scope_ids(self, scope_id: str) -> Sequence[str]:
        """这个 scope 所属那张图里的全部 scope（``ORDER BY scope_id``）。

        **没并过图的 scope 返回它自己一个**——不是空列表。调用方据此遍历。
        """
        ...
