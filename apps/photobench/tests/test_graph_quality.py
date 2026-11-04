from photobench.domain.runs.graph_quality import (
    aggregate_graph_qa_by_type,
    aggregate_graph_qa_overall,
    face_clustering_quality,
    final_media_effect_from_run_summaries,
)


def test_graph_qa_marks_historical_items_without_graph_telemetry():
    rows = aggregate_graph_qa_by_type(
        [
            {
                "question_type": "fact_retrieval",
                "media_retrieval_recall": 1.0,
                "judge": {"score": 2},
            }
        ]
    )

    assert rows[0]["telemetry_status"] == "not_recorded"
    assert rows[0]["graph_routed_count"] == 0
    assert rows[0]["retrieval_recall"] == 1.0


def test_graph_qa_compares_graph_head_with_primary_head():
    rows = aggregate_graph_qa_by_type(
        [
            {
                "question_type": "multi_hop",
                "gt_media": [{"asset_id": "gt"}],
                "tool_trace": [
                    {
                        "result": {
                            "retrieval_channels": [{"channel": "graph", "status": "ok"}],
                            "graph_primary_asset_ids": ["other"],
                            "graph_fused_asset_ids": ["gt"],
                        }
                    }
                ],
            }
        ]
    )

    assert rows[0]["telemetry_status"] == "recorded"
    assert rows[0]["graph_effect_count"] == 1
    assert rows[0]["graph_recall_delta"] == 1.0
    assert rows[0]["graph_gt_improved_count"] == 1


def test_graph_qa_counts_empty_heads_as_zero_recall():
    rows = aggregate_graph_qa_by_type(
        [
            {
                "question_type": "multi_hop",
                "gt_media": [{"asset_id": "gt"}],
                "tool_trace": [
                    {
                        "result": {
                            "retrieval_channels": [{"channel": "graph", "status": "ok"}],
                            "graph_primary_asset_ids": ["gt"],
                            "graph_fused_asset_ids": [],
                        }
                    }
                ],
            }
        ]
    )

    assert rows[0]["graph_effect_count"] == 1
    assert rows[0]["graph_recall_delta"] == -1.0
    assert rows[0]["graph_gt_worsened_count"] == 1


def test_graph_qa_does_not_call_degraded_graph_enabled():
    rows = aggregate_graph_qa_by_type(
        [
            {
                "question_type": "multi_hop",
                "gt_media": [{"asset_id": "gt"}],
                "tool_trace": [
                    {
                        "result": {
                            "retrieval_channels": [{"channel": "graph", "status": "degraded"}],
                            "graph_primary_asset_ids": ["gt"],
                            "graph_fused_asset_ids": ["gt"],
                        }
                    }
                ],
            }
        ]
    )

    assert rows[0]["graph_routed_count"] == 1
    assert rows[0]["graph_enabled_count"] == 0


def test_graph_qa_overall_compares_primary_and_fused_retrieval_heads():
    overall = aggregate_graph_qa_overall(
        [
            {
                "question_type": "multi_hop",
                "gt_media": [{"asset_id": "gt"}],
                "judge": {"score": 2},
                "tool_trace": [
                    {
                        "result": {
                            "retrieval_channels": [{"channel": "graph", "status": "ok"}],
                            "graph_primary_asset_ids": ["other"],
                            "graph_fused_asset_ids": ["gt"],
                        }
                    }
                ],
            }
        ]
    )

    assert overall["available"] is True
    assert overall["without_graph"]["retrieval_recall"] == 0.0
    assert overall["with_graph"]["retrieval_recall"] == 1.0
    assert overall["delta"]["retrieval_recall"] == 1.0
    assert overall["without_graph"]["retrieval_precision"] == 0.0
    assert overall["with_graph"]["retrieval_precision"] == 1.0
    assert overall["delta"]["retrieval_precision"] == 1.0
    assert overall["with_graph"]["answer_quality_mean"] == 2.0
    assert overall["delta"]["answer_quality_mean"] is None
    assert overall["quality_comparison_status"] == "requires_paired_no_graph_run"
    assert overall["comparison_basis"] == "candidate_heads"


def test_graph_qa_overall_uses_paired_final_media_delivery_metrics():
    baseline = [
        {
            "qa_id": "q1",
            "answerability": "answerable",
            "retrieval_media_refs": [{"media_id": "m1"}],
            "media_retrieval_counts": {"gt": 2, "predicted": 2, "matched": 1},
            "judge": {"score": 1},
        }
    ]
    graph = [
        {
            "qa_id": "q1",
            "answerability": "answerable",
            "retrieval_media_refs": [{"media_id": "m1"}],
            "media_retrieval_counts": {"gt": 2, "predicted": 2, "matched": 2},
            "judge": {"score": 2},
            "gt_media": [{"asset_id": "gt"}],
            "tool_trace": [
                {
                    "result": {
                        "retrieval_channels": [{"channel": "graph", "status": "ok"}],
                        "graph_primary_asset_ids": ["other"],
                        "graph_fused_asset_ids": ["gt"],
                    }
                }
            ],
        }
    ]

    overall = aggregate_graph_qa_overall(
        graph,
        baseline,
        baseline_run_id="run-off",
        graph_run_id="run-on",
    )

    final = overall["final_media_effect"]
    assert final["available"] is True
    assert final["baseline_run_id"] == "run-off"
    assert final["with_graph_run_id"] == "run-on"
    assert final["without_graph"]["media_retrieval_recall"] == 0.5
    assert final["with_graph"]["media_retrieval_recall"] == 1.0
    assert final["delta"]["media_retrieval_recall"] == 0.5
    assert final["delta"]["answer_quality_mean"] == 1.0
    assert overall["comparison_basis"] == "final_media_delivery"
    assert overall["without_graph"]["retrieval_recall"] == 0.5
    assert overall["with_graph"]["retrieval_recall"] == 1.0
    assert overall["delta"]["retrieval_recall"] == 0.5


def test_graph_qa_does_not_compare_an_incomplete_paired_run():
    overall = aggregate_graph_qa_overall(
        [
            {
                "qa_id": "q1",
                "gt_media": [{"asset_id": "gt"}],
                "tool_trace": [
                    {
                        "result": {
                            "retrieval_channels": [{"channel": "graph", "status": "ok"}],
                            "graph_primary_asset_ids": ["other"],
                            "graph_fused_asset_ids": ["gt"],
                        }
                    }
                ],
            }
        ],
        [{"qa_id": "q1", "media_retrieval_counts": {"gt": 1, "predicted": 1, "matched": 1}}],
        baseline_run_id="run-off",
        graph_run_id="run-on",
        graph_run_valid=False,
    )

    assert overall["comparison_basis"] == "candidate_heads"
    assert overall["final_media_effect"]["available"] is False
    assert overall["final_media_effect"]["status"] == "requires_complete_paired_runs"


def test_overall_final_media_effect_uses_run_summary_values_directly():
    effect = final_media_effect_from_run_summaries(
        {
            "media_retrieval_recall_macro": 0.764,
            "media_retrieval_precision_macro": 0.512,
            "media_retrieval_f1_macro": 0.603,
            "answer_quality_mean": 1.4,
            "media_retrieval_metric_count": 487,
        },
        {
            "media_retrieval_recall_macro": 0.393,
            "media_retrieval_precision_macro": 0.501,
            "media_retrieval_f1_macro": 0.579,
            "answer_quality_mean": 1.2,
            "media_retrieval_metric_count": 487,
        },
        baseline_run_id="run-off",
        graph_run_id="run-on",
    )

    assert effect["status"] == "run_summary"
    assert effect["without_graph"]["media_retrieval_recall"] == 0.393
    assert effect["with_graph"]["media_retrieval_recall"] == 0.764
    assert effect["delta"]["media_retrieval_recall"] == 0.371


def test_graph_qa_overall_does_not_infer_final_media_baseline_from_candidate_heads():
    overall = aggregate_graph_qa_overall(
        [
            {
                "qa_id": "q1",
                "gt_media": [{"asset_id": "gt"}],
                "retrieval_media_refs": [{"media_id": "m1"}],
                "media_retrieval_counts": {"gt": 1, "predicted": 1, "matched": 1},
                "tool_trace": [
                    {
                        "result": {
                            "retrieval_channels": [{"channel": "graph", "status": "ok"}],
                            "graph_primary_asset_ids": [],
                            "graph_fused_asset_ids": ["gt"],
                        }
                    }
                ],
            }
        ]
    )

    assert overall["final_media_effect"]["available"] is False
    assert overall["final_media_effect"]["status"] == "requires_paired_no_graph_run"


def test_face_clustering_quality_uses_identity_gallery_and_image_links():
    quality = face_clustering_quality(
        [
            {
                "id": "cluster-1",
                "samples": [
                    {"face_instance_id": "seed", "file_name": "faceid_1.jpg", "validity": "uncertain"},
                    {"face_instance_id": "a-face", "file_name": "a.jpg", "validity": "verified"},
                    {"face_instance_id": "b-face", "file_name": "b.jpg", "validity": "verified"},
                ],
            }
        ],
        {"a.jpg": ["1"], "b.jpg": ["1"]},
    )

    assert quality["available"] is True
    assert quality["identity_gallery"]["f1"] == 1.0
    assert quality["image_identity_links"]["f1"] == 1.0


def test_face_clustering_quality_counts_imported_images_without_detections():
    quality = face_clustering_quality(
        [
            {
                "id": "cluster-1",
                "samples": [
                    {"face_instance_id": "seed", "file_name": "faceid_1.jpg", "validity": "uncertain"},
                    {"face_instance_id": "a-face", "file_name": "a.jpg", "validity": "verified"},
                ],
            }
        ],
        {"a.jpg": ["1"], "b.jpg": ["1"]},
        asset_names={"a.jpg", "b.jpg"},
    )

    assert quality["identity_gallery"]["gt_asset_count"] == 2
    assert quality["identity_gallery"]["false_negative"] == 1
    assert quality["identity_gallery"]["evaluable_image_count"] == 2
    assert quality["image_identity_links"]["false_negative"] == 1
    assert quality["image_identity_links"]["evaluable_image_count"] == 2
