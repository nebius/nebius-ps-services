"""Audit a pinned public model's artifacts, config, tokenizer, and license."""

from __future__ import annotations

import argparse
from typing import Any

from common import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    try:
        from huggingface_hub import HfApi
        from transformers import AutoConfig, AutoTokenizer, GenerationConfig
    except ImportError as exc:
        raise SystemExit(
            "Install the pinned training environment with Transformers and "
            "huggingface_hub."
        ) from exc

    try:
        info = HfApi().model_info(
            args.model,
            revision=args.revision,
            files_metadata=False,
        )
        config = AutoConfig.from_pretrained(
            args.model,
            revision=args.revision,
            trust_remote_code=False,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            args.model,
            revision=args.revision,
            trust_remote_code=False,
        )
        generation_config = GenerationConfig.from_pretrained(
            args.model,
            revision=args.revision,
        )
    except Exception as exc:
        raise SystemExit(
            "The pinned model metadata must be reachable or cached before this lab."
        ) from exc

    files = sorted(sibling.rfilename for sibling in info.siblings)
    weight_files = [name for name in files if name.endswith(".safetensors")]
    index_files = [name for name in files if name.endswith(".index.json")]
    card_data: Any = info.card_data
    license_id = getattr(card_data, "license", None) if card_data else None
    pinned_revision_matches = info.sha == args.revision
    expected_artifacts = all(
        any(name == expected for name in files)
        for expected in ("config.json", "tokenizer.json", "README.md")
    )
    safe_loading_policy = not bool(getattr(config, "auto_map", None))
    if not pinned_revision_matches or not expected_artifacts or not weight_files:
        raise SystemExit("The pinned model artifact contract was not satisfied.")
    target = write_result(
        args,
        lab_id="16_model_artifact_audit",
        environment=environment,
        measurements={
            "model": args.model,
            "revision": args.revision,
            "resolved_commit": info.sha,
            "model_type": getattr(config, "model_type", None),
            "hidden_size": getattr(config, "hidden_size", None),
            "layers": getattr(config, "num_hidden_layers", None),
            "attention_heads": getattr(config, "num_attention_heads", None),
            "key_value_heads": getattr(config, "num_key_value_heads", None),
            "maximum_positions": getattr(config, "max_position_embeddings", None),
            "rope_theta": getattr(config, "rope_theta", None),
            "rope_scaling": getattr(config, "rope_scaling", None),
            "config_vocab_size": getattr(config, "vocab_size", None),
            "tokenizer_vocab_size": tokenizer.vocab_size,
            "tokenizer_class": tokenizer.__class__.__name__,
            "has_chat_template": bool(tokenizer.chat_template),
            "generation_max_length": generation_config.max_length,
            "generation_eos_token_id": generation_config.eos_token_id,
            "weight_files": weight_files,
            "weight_index_files": index_files,
            "license_identifier": license_id,
            "remote_model_code_declared": bool(getattr(config, "auto_map", None)),
        },
        correctness={
            "pinned_revision_matches": pinned_revision_matches,
            "expected_artifacts_present": expected_artifacts,
            "safetensors_weights_present": bool(weight_files),
            "remote_code_was_not_enabled": True,
            "model_uses_standard_transformers_code": safe_loading_policy,
            "license_metadata_was_inspected": True,
        },
    )
    print(f"Completed model-artifact audit: {target}")


if __name__ == "__main__":
    main()
