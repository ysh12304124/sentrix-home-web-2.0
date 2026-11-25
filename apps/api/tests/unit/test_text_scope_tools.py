"""Text tooling is advertised only in text scopes; visual schemas retain their baseline."""

from sentrix.domain.assistant.evidence_ledger import EvidenceLedger
from sentrix.domain.assistant.jit import select_turn_tools
from sentrix.domain.assistant.tools import FINAL_ANSWER, SEARCH_MEMORIES


def test_visual_scope_keeps_original_search_and_final_contracts():
    offered = {spec.name: spec for spec in select_turn_tools(EvidenceLedger("scope"), has_source_text=False)}
    assert set(offered) == {"search_memories", "final_answer"}
    assert offered["search_memories"].parameters == SEARCH_MEMORIES.parameters
    assert offered["final_answer"].parameters == FINAL_ANSWER.parameters
    assert "selected_text_handles" not in offered["final_answer"].parameters["properties"]


def test_text_scope_advertises_both_paths_and_explicit_source_citations():
    offered = {spec.name: spec for spec in select_turn_tools(EvidenceLedger("scope"), has_source_text=True)}
    assert set(offered) == {"search_memories", "search_text_memories", "read_source_text", "final_answer"}
    assert "selected_text_handles" in offered["final_answer"].parameters["properties"]


def test_text_review_distinguishes_source_metadata_from_private_handles():
    from sentrix.application.assistant.judge import FaithfulnessJudge
    from sentrix.domain.assistant.judge import JUDGE_SYSTEM, TEXT_SOURCE_REVIEW_RULES
    from sentrix.domain.assistant.models import ModelReply

    class Model:
        def complete(self, messages, tools):
            self.messages = messages
            return ModelReply(content='{"faithful":true,"problems":[]}')

    model = Model()
    judge = FaithfulnessJudge(model)
    judge.review(query="How much?", answer="23 pounds", transcript=[], source_text=False)
    assert model.messages[0].content == JUDGE_SYSTEM
    judge.review(query="How much?", answer="23 pounds", transcript=[], source_text=True)
    assert model.messages[0].content == JUDGE_SYSTEM + TEXT_SOURCE_REVIEW_RULES


def test_declared_text_question_uses_one_retrieval_path_and_mixed_stays_available():
    from sentrix.domain.assistant.evidence_ledger import EvidenceRequirement, EvidenceType

    ledger = EvidenceLedger(
        "scope",
        [
            EvidenceRequirement(
                id="body", evidence_type=EvidenceType.SOURCE_TEXT, description="Read correspondence"
            )
        ],
    )
    names = {t.name for t in select_turn_tools(ledger, has_source_text=True)}
    assert "search_text_memories" in names and "search_memories" not in names
    ledger.requirements.append(
        EvidenceRequirement(id="photos", evidence_type=EvidenceType.PHOTO_SET, description="Find pictures")
    )
    assert "search_memories" in {t.name for t in select_turn_tools(ledger, has_source_text=True)}
