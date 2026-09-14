"""Guard reviewed semantic distinctions; these are not a grammar checker."""

import math

from test_course_content_contract import lab_section, ROOT


def test_crossover_evidence_allows_no_winner_for_either_gpu_measurement():
    evidence = lab_section("gpu-fundamentals", 1, "Check your results")
    assert "gpu_resident" in evidence
    assert "gpu_with_transfers_median_ms" in evidence
    assert "none did" in evidence


def test_dispatch_example_counts_ten_costs_without_assuming_graph_replay():
    example = lab_section("gpu-optimizations", 3, "Practice")
    assert "each preceded by" in example
    assert "warmed execution" in example
    assert 10 * (4 + 8) == 120


def test_tail_example_repartitions_preserved_work():
    source = (
        ROOT / "gpu-optimizations/reference/labs/15_tail_load_balance.md"
    ).read_text()
    assert "480 smaller blocks while preserving the result" in source
    assert "480 useful blocks by splitting each task" not in source
    assert 241 * 2 != 480
    assert math.ceil(480 / 240) == 2


def test_event_and_graph_definitions_do_not_overgeneralize_the_lab():
    glossary = (ROOT / "gpu-optimizations/GLOSSARY.md").read_text()
    assert "timing-enabled events" in glossary
    assert "created explicitly or through stream capture" in glossary


def test_bank_conflicts_distinguish_competing_words_from_broadcast():
    glossary = (ROOT / "custom-cuda-kernels/GLOSSARY.md").read_text()
    assert "different words in the same bank" in glossary
    assert "same-word broadcasts" in glossary


def test_training_syllabus_transfers_skills_not_different_workload_evidence():
    syllabus = (ROOT / "llm-training/SYLLABUS.md").read_text()
    assert "Reuse Lab 30 profiling skills" in syllabus
    assert "Lab 31's matched workload separately" in syllabus
    assert "Reuse Lab 30 evidence" not in syllabus


def test_prefix_near_match_is_an_extension_not_a_supplied_cohort():
    source = (
        ROOT / "llm-inference/reference/labs/20_prefix_cache_client.md"
    ).read_text()
    assert "repeated-prefix and unique-prefix cohorts" in source
    assert "As an extension, add a token-controlled near-match cohort" in source


def test_speculation_bridge_retains_prefill_first_token_boundary():
    source = (ROOT / "llm-inference/COURSE.md").read_text()
    assert "After prefill supplies the first output token" in source
    assert "one target-model forward pass for each subsequent token" in source


def test_inference_glossary_separates_operation_and_observation():
    glossary = (ROOT / "llm-inference/GLOSSARY.md").read_text()
    assert "the query/key/value attention operation" in glossary
    assert "First-nonempty-content timing is a proxy" in glossary
