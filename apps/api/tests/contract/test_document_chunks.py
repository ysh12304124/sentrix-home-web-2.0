from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from sentrix.application.retrieval.index_observation_text import IndexObservationText
from sentrix.infrastructure.media.text_chunks import document_chunks
from sentrix.infrastructure.persistence.repositories.media_deletion import SqliteMediaDeletionRepository
from sentrix.infrastructure.persistence.repositories.text_vectors import SqliteTextVectorRepository


def test_long_document_covers_original_with_budget_and_cleans_all_chunks(client, tmp_path):
    text = ("Hotel receipt Cairo $64.08. 中文原文保持。\n" * 150) + "Last invoice $367.99."
    tokenizer = Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    tokenizer.train_from_iterator(
        [text], trainers.BpeTrainer(vocab_size=300, initial_alphabet=pre_tokenizers.ByteLevel.alphabet())
    )
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    chunks = document_chunks(text, "Cairo booking", tokenizer_path=str(path), endpoint="", token_budget=256)
    assert len(chunks) > 1
    assert chunks[0].start == 0 and chunks[-1].end == len(text)
    assert all(left.end >= right.start for left, right in zip(chunks, chunks[1:], strict=False))
    assert all(len(tokenizer.encode(chunk.text).ids) <= 192 for chunk in chunks)
    for chunk in chunks:
        assert chunk.text.endswith(text[chunk.start : chunk.end])

    # 同一正文分块索引可补齐部分失败，重导不重复调用；删除清理 SQLite 和外部索引账本。
    response = client.post(
        "/api/v1/documents/import",
        files={"file": ("short.txt", b"Hotel receipt")},
        data={"scope_id": "chunks"},
    )
    identifier = response.json()["items"][0]["asset_id"]
    container = client.app.state.container
    observation = container.observation_repository.list_scope("chunks", limit=100)[0]
    vectors = SqliteTextVectorRepository(container.database)

    class Embedder:
        model_id = "chunk-test"
        dimension = 2
        calls = 0

        def embed_query(self, value):
            self.calls += 1
            return [1.0, 0.5]

    embedder = Embedder()
    index = IndexObservationText(embedder=embedder, vectors=vectors)
    assert index.index_document(observation, chunks).rows == len(chunks) * 2
    assert index.index_document(observation, chunks).rows == 0
    assert embedder.calls == len(chunks)
    with container.database.connect() as db, db:
        db.execute(
            "DELETE FROM text_vectors WHERE source_id=? AND space='semantic' AND model_name='chunk-test'",
            (observation.id + ":chunk:1",),
        )
    assert index.index_document(observation, chunks).rows == 2
    assert SqliteMediaDeletionRepository(container.database).delete_asset(identifier)
    with container.database.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM text_vectors WHERE json_extract(metadata_json,'$.asset_id')=?",
                (identifier,),
            ).fetchone()[0]
            == 0
        )
        assert (
            ":chunk:"
            in db.execute(
                "SELECT sources_json FROM media_cleanup_jobs WHERE asset_id=?", (identifier,)
            ).fetchone()[0]
        )


def test_short_document_does_not_load_tokenizer():
    chunks = document_chunks(
        "Original 内容", "Title", tokenizer_path="does-not-exist", endpoint="", token_budget=512
    )
    assert len(chunks) == 1
    assert (chunks[0].start, chunks[0].end) == (0, len("Original 内容"))
