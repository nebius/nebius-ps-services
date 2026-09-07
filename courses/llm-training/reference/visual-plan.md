# Visual plan

The table defines this course's overview diagrams. The
[visual manifest](visual-manifest.json) links additional detailed diagrams to
their conceptual lessons. Each figure appears after the specified section in its declared lesson or lab home,
with accessible labels, captions, and fit-to-width sizing.

The training-lifecycle diagram follows **Start here** in Lesson 1. It connects
data preparation and tokenization to predictions/loss, gradients, parameter
updates, evaluation and checkpointing before comparing training stages.

| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Training lifecycle | Data and objective | Forward, loss, backward | Update, evaluate, checkpoint | Training is a stateful loop whose objective and recovery boundary must be explicit. | 1 | Mental model | cycle | lesson |
| Causal batch | Tokens and boundaries | Labels and masks | Valid trained tokens | Packing is useful only when labels and attention preserve example semantics. | 2 | Mechanism | flow | lesson |
| Decoder training step | Embeddings and blocks | Logits and loss | Gradients and update | Tensor shapes connect model semantics to compute and memory. | 3 | Mental model | flow | lesson |
| Exact resume | Persistent state | Checkpoint boundary | Equivalent next update | Weights alone do not reproduce optimizer, RNG, data, or partial-step state. | 5 | Mechanism | flow | lesson |
| Training memory | Weights and optimizer | Activations and temporary buffers | Phase peak | Persistent and phase-specific state require different optimizations. | 6 | Mechanism | comparison | lesson |
| Precision ledger | Storage dtypes | Compute and accumulation | Loss and update gates | A precision mode is accepted only when kernels and training signal are valid. | 7 | Mechanism | flow | lesson |
| Parallelism map | Replicate or shard state | Partition operators or layers | Route context or experts | Each parallel dimension solves a different fit or scaling constraint. | 12 | Mechanism | comparison | lesson |
| Communication overlap | Backward compute | Ready-bucket collectives | Exposed critical path | Exposed communication extends the critical path; overlap can also slow computation through resource contention. | 13 | Mechanism | overlap | lesson |
| Input pipeline | Read and tokenize | Pack and transfer | GPU step | Equivalent batches must arrive before the GPU needs them. | 9 | Mechanism | timeline | lesson |
| Training decision | Profile baseline | Change one factor | Correctness and scoped performance report | The capstone scopes every claim to equivalent work and declared evidence. | 16 | Mechanism | decision | lesson |
