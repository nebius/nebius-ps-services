"""Small decoder-only transformer used by the distributed training labs."""

from __future__ import annotations

from typing import Any


def build_tiny_lm(
    torch: Any,
    *,
    vocab_size: int,
    hidden_size: int,
    layers: int,
    heads: int,
    max_sequence: int,
) -> Any:
    class CausalSelfAttention(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.heads = heads
            self.head_dim = hidden_size // heads
            self.qkv = torch.nn.Linear(hidden_size, 3 * hidden_size, bias=False)
            self.output = torch.nn.Linear(hidden_size, hidden_size, bias=False)

        def forward(self, values: Any) -> Any:
            batch, sequence, _ = values.shape
            qkv = self.qkv(values).view(batch, sequence, 3, self.heads, self.head_dim)
            query, key, value = qkv.unbind(dim=2)
            query = query.transpose(1, 2)
            key = key.transpose(1, 2)
            value = value.transpose(1, 2)
            attended = torch.nn.functional.scaled_dot_product_attention(
                query, key, value, is_causal=True
            )
            attended = (
                attended.transpose(1, 2).contiguous().view(batch, sequence, hidden_size)
            )
            return self.output(attended)

    class Block(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.attention_norm = torch.nn.LayerNorm(hidden_size)
            self.attention = CausalSelfAttention()
            self.mlp_norm = torch.nn.LayerNorm(hidden_size)
            self.mlp = torch.nn.Sequential(
                torch.nn.Linear(hidden_size, 4 * hidden_size, bias=False),
                torch.nn.GELU(),
                torch.nn.Linear(4 * hidden_size, hidden_size, bias=False),
            )

        def forward(self, values: Any) -> Any:
            values = values + self.attention(self.attention_norm(values))
            return values + self.mlp(self.mlp_norm(values))

    class TinyCausalLM(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.token_embedding = torch.nn.Embedding(vocab_size, hidden_size)
            self.position_embedding = torch.nn.Embedding(max_sequence, hidden_size)
            self.blocks = torch.nn.ModuleList([Block() for _ in range(layers)])
            self.final_norm = torch.nn.LayerNorm(hidden_size)
            self.lm_head = torch.nn.Linear(hidden_size, vocab_size, bias=False)

        def forward(self, token_ids: Any) -> Any:
            positions = torch.arange(token_ids.shape[1], device=token_ids.device)
            hidden = self.token_embedding(token_ids) + self.position_embedding(
                positions
            )
            for block in self.blocks:
                hidden = block(hidden)
            return self.lm_head(self.final_norm(hidden))

    return TinyCausalLM()


def make_language_batch(
    torch: Any,
    *,
    batch_size: int,
    sequence: int,
    vocab_size: int,
    device: str,
    offset: int = 0,
) -> tuple[Any, Any]:
    """Create deterministic next-token data without downloading a dataset."""
    starts = torch.arange(batch_size, device=device).unsqueeze(1) + offset
    steps = torch.arange(sequence + 1, device=device).unsqueeze(0)
    tokens = (starts * 7 + steps * 3 + 11) % vocab_size
    return tokens[:, :-1].long(), tokens[:, 1:].long()
