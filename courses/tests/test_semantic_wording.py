"""Guard reviewed semantic distinctions; these are not a grammar checker."""


from test_course_content_contract import ROOT


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
    assert "repeated-prefix and unique-prefix request cohorts" in source
    assert "near-match cohort" not in source


def test_speculation_bridge_retains_prefill_first_token_boundary():
    source = (ROOT / "llm-inference/COURSE.md").read_text()
    assert "After prefill supplies the first output token" in source
    assert "one target-model forward pass for each subsequent token" in source


def test_inference_glossary_separates_operation_and_observation():
    glossary = (ROOT / "llm-inference/GLOSSARY.md").read_text()
    assert "the query/key/value attention operation" in glossary
    assert "First-nonempty-content timing is a proxy" in glossary
