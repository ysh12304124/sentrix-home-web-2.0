# 文本资料导入和回读

这条通路将 ATM 邮件适配中的原文索引和回读迁入现有资产导入与问答流程。入口是有权限调用
`POST /api/v1/media/import` 的本地或评测客户端。范围包括已导出的 UTF-8 邮件和普通文本。

```bash
curl --fail-with-body http://127.0.0.1:8191/api/v1/media/import \
  -F 'file=@mail.txt;type=text/plain' \
  -F 'scope_id=example-text' \
  -F 'external_source_id=mail-001' \
  -F 'source_timestamp=2024-06-23T18:20:00+08:00'
```

ATM 客户端可把发布记录的 `detail` 保存为原文，传入对应的 `id` 和可用的 `timestamp`。
两个来源字段均可选。列表、资产详情和检索预览可用于追溯来源 ID。资产的 `captured_at`
记录来源时间，工具结果保留所提供的原始时间串。无偏移的时间按原资料保留未知时区语义。

同一资料范围内重复提交同一来源 ID、原件和时间可复用资产与观察，并补齐未完成的索引。
同一 ID 对应不同正文或时间时返回 409。同字节的不同来源应分别提供来源 ID。
省略来源 ID 时沿用同范围的内容哈希去重。历史文本可通过再次导入补建正文索引。

`search_memories` 返回 `media_kind=text` 的候选和原文节选。`read_source_text` 使用本轮
结果集中的 handle，按字符偏移读取原文，每次最多 1,600 字符。按 `next_offset` 续读直至该值为空。
原件保留原字节，可用 `GET /api/v1/assets/{asset_id}/file` 下载。
空白或无法解码的文件仍可归档，导入响应以 `indexed=false` 表明正文索引不可用。

迁移 0032 为原件身份及观察正文增加可空或带默认值的列，并为新身份建唯一索引。
前一版应用可继续读取旧字段。若需恢复迁移前的库结构，应使用迁移前备份，并保留迁移后的库与新增原件以便恢复。
