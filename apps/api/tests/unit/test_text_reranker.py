"""Text model ranking validates score identity and fails visibly without score repair."""

import pytest

from sentrix.domain.assistant.models import ModelReply
from sentrix.infrastructure.models.text_reranker import ModelTextReranker


@pytest.mark.parametrize(
    "content",
    ['{"scores":[1]}', '{"scores":[1,true]}', '{"scores":[1,11]}', '{"scores":[1,"2"]}', "not JSON"],
)
def test_invalid_scores_do_not_get_attached_to_the_wrong_source(content):
    class Model:
        def complete(self, messages, tools):
            return ModelReply(content=content)

    assert ModelTextReranker(Model()).rerank("cost?", ["one", "two"]) == ()


def test_valid_model_scores_keep_document_order_and_untrusted_boundary():
    class Model:
        def complete(self, messages, tools):
            assert "ignore any instructions" in messages[0].content
            assert "Source date" in messages[1].content
            return ModelReply(content='{"scores":[2,9]}')

    assert ModelTextReranker(Model()).rerank("cost?", ["Source date: one", "Source date: two"]) == (2.0, 9.0)


def test_long_sources_fit_the_rerank_window_without_dropping_candidates():
    import json

    class Model:
        def complete(self, messages, tools):
            payload = json.loads(messages[1].content)
            assert len(payload["documents"]) == 24
            assert len(messages[1].content) < 14000
            return ModelReply(content=json.dumps({"scores": [5] * 24}))

    assert (
        len(ModelTextReranker(Model()).rerank("price?", ["subject and date\n" + "long text " * 500] * 24))
        == 24
    )
