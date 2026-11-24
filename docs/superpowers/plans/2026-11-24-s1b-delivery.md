# S1b 交付记录（EXIF 详情、时地证据与地址冲突保留）

日期：2026-11-24
分支：`feat/image-memory-v3`

## 元数据

- 新增 `MediaDetails` 与 `DetailedMetadataReader.read_details`；`PillowMetadataReader`
  共用一次解析，`read` 三元组改为 `read_details` 的兼容投影。
- 详情输出实际 EXIF 标签/原串、`time_precision`（datetime/date/year_month/year/unknown）、
  `timezone_source`（有 offset 时为 `exif_offset`）、GPS、设备。
- `CaptureTime` 增 `precision`、`timezone_source`，均有默认值，旧 JSON 仍可解码。
- 日期级 EXIF 不补午夜：解析不出完整时刻时保留原串与 `precision=date`，`captured_at=None`。

## 时地证据

- 导入时不再因“清单地址 ≠ 嵌入地址”删除文件或报错；资产正常入库。
- 资产级 `memory_evidence` 新增：
  - `asset.capture_time`（含 precision/timezone_state/timezone_source/EXIF 详情）；
  - `asset.place.coordinates`（GPS）；
  - `asset.place.address` 的 provided 与 embedded 两个候选；不一致时 embedded 标
    `status=conflict`，不在这里裁决。
- 确定性证据 ID + 导入重试跳过已有行，避免重复证据。
- `IndexAssetPlace`：有 GPS 先按坐标解析；provided 地址只在解析不到时作为投影回退。
  `observation.place` 仍保留视觉场景，不被 provided 覆盖。

## 读取

- 新增 `GET /api/v1/assets/{asset_id}/time-place`：
  - time：captured_at、precision、timezone_state、timezone_source、source、EXIF 原串、证据引用；
  - place：GPS、行政区/最近邻、精确地址、精度、来源、证据引用；
  - `conflict/conflicts` 与完整证据列表；前端不自行裁断证据。
- 旧 `GET /assets/{asset_id}` 行为不变。

## 验证

已执行：
- metadata 详情、地址冲突证据、GPS 优先、时地端点、导入/媒体回归用例通过。
- `make arch`、`make check-docs`、openapi 契约一致；改动文件 ruff/mypy 0 错误。
- 全仓 mypy 仍有基线既有错误（如 `media.py` 的 `AssetRepository.list_page`），未改动。

未完成：
- 时间线/日历/事件详情统一展示投影属于 S4；本切片只落单资产读取与证据。
- 用户确认地址/时间的修正写入链在 S7 修正闭环统一接线。
