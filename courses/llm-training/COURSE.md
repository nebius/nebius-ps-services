# LLM Training and GPU Optimization on NVIDIA H100

This course teaches how an LLM training step works, how its state consumes memory and communication, and how to optimize it without changing the learning objective or hiding correctness regressions.

## 1. Understand model training and its learning objective

**Start here**

### What model training is

A model is a parameterized computation: it takes an input and uses stored numbers, called parameters or weights, to produce an output. Training is the process of changing those numbers using examples and an objective that scores the model's behavior. We do not write a separate rule for every possible answer. Instead, we choose a model structure, a dataset and an update procedure. The resulting behavior is learned from data, so it must be evaluated rather than assumed correct.

A large language model (LLM) works with tokens: pieces of text represented by integer IDs. A token need not be a whole word. A decoder-only language model assigns scores to possible next tokens given a preceding sequence. It can be trained from known examples of which token actually followed a prefix. This course explains that learning process first, then how to make its computation, memory use and communication efficient on H100 GPUs. It does not assume prior experience training neural networks; basic Python and the two GPU prerequisite courses provide the programming and device foundation.

### Why train, and when not to

Training is useful when a model must learn a predictive relationship or adapt its behavior to a domain or task. LLM pretraining learns broad language patterns from a large corpus, usually starting from random weights. Continued pretraining reuses an existing checkpoint and a similar objective; fine-tuning adapts a pretrained model using selected examples. Not every model trained from scratch is undergoing LLM pretraining. These choices differ greatly in required data, compute and evaluation. They are introduced in the rest of this lesson and explored later.

Training is not the default solution to every application problem. Using an existing model is inference; supplying documents in a prompt changes its context without necessarily changing its parameters. If prompting or retrieving current facts solves the need, changing weights may add cost and risk without benefit. Training requires suitable permitted data, an evaluation set separate from the update data, and checks for overfitting: performing well on examples seen during training but poorly on new ones.

### How one learning step works

Supervised fine-tuning (SFT) trains an existing model on selected input/target examples. Parameter-efficient fine-tuning (PEFT) limits the amount of trainable state; an adapter is an added trainable component attached to a base model. Low-rank adaptation (LoRA) is one PEFT technique: smaller matrix factors represent an update to selected frozen weights. Reward-guided training instead scores generated answers and adjusts the policy, the model's distribution over possible tokens. These terms identify different choices about supervision and trainable parameters, not one universal training procedure. A checkpoint is saved model or training state; later lessons distinguish a model export from a snapshot sufficient for exact resumption.

Take a small batch of examples. The forward pass computes predictions using the current weights. For next-token training, the model produces logits, which are unnormalized scores for vocabulary entries. Softmax converts scores into probabilities. A loss turns the probability assigned to each known target into a scalar penalty; cross-entropy penalizes low probability on the correct token. The backward pass calculates gradients: how sensitive that loss is to small changes in each trainable weight. An optimizer uses those gradients to change the weights. Repeat with more batches, evaluate on held-out data, and save checkpoints so the state can be used or resumed.

The model's forward pass computes predictions and the loss function assesses their error. Backward does not itself update the weights; the optimizer performs the update. Labels are used to assess predictions during training; the answer for the next generated token is not supplied during ordinary inference. During a causal training forward, many known sequence positions can be processed in parallel while the attention mask prevents a position from seeing its future. Generation must instead obtain later tokens from earlier outputs.

The CPU prepares batches and submits operations. GPU kernels perform the large tensor operations in forward, backward and the update. Weights persist between batches; activations are intermediate values, gradients carry the current learning signal, and an optimizer may keep additional history. This is why training memory is more than a model-file size. GPU optimization must preserve the learning task, not merely make a different or incomplete update finish faster.

Mean squared error (MSE) averages the squared prediction-minus-target differences, making a single nonnegative loss. Plain stochastic gradient descent (SGD) uses `weight -= learning_rate * gradient`; the learning rate sets the update scale. PyTorch autograd records differentiable operations on trainable values, and `torch.nn.Parameter` marks the scalar weight as a model parameter. Clear its old gradient, compute the new loss, call backward, then step the optimizer. For held-out prediction, use inference mode to avoid recording a backward graph and check that parameters stay unchanged. These operations connect the learning rule to the first lab; they do not require a transformer.

### Try a small example and choose your route

Before a transformer, use the scalar model prediction = weight × input. For inputs 1 and 2 with targets 2 and 4, weight 0 predicts zero. The mean squared error is (4 + 16)/2 = 10 and its gradient is -10. A learning rate of 0.1 makes the next weight 0 - 0.1 × (-10) = 1. The new loss is 2.5. Repetition approaches weight 2; input 3, never used for updates, should then produce approximately 6. This demonstrates the update mechanism, not language understanding.

Run Lab 32 on CPU, or on an allocated H100 with explicit CUDA selection. Inspect the first gradient, final weight, held-out prediction and unchanged weight during prediction. Then learn tokens and masks, transformer computation and a complete update through Lessons 2–4. Experienced readers can use the same checkpoint: explain which values change during training, what backward computes, and why a lower training loss alone does not prove useful generalization.

**Objective** Distinguish pretraining, continued pretraining, supervised fine-tuning, parameter-efficient tuning, and reward-guided post-training.

**Prerequisite bridge** GPU courses explain how operations execute; training adds the learning contract that decides which values change and why. Every optimization later must preserve this data-to-update lifecycle.

**Why it matters** “Training an LLM” can mean fundamentally different objectives, datasets, trainable state, checkpoints, and evaluation signals. Mixing them makes memory estimates, throughput comparisons, and quality claims meaningless.

**Mechanism** Pretraining predicts tokens over broad corpora and updates the full model. Continued pretraining applies a similar causal objective to a new domain or distribution. Supervised fine-tuning uses curated input-response examples and usually masks prompt or padding tokens from loss. Parameter-efficient methods freeze the base and update small adapter state. Reward-guided methods generate candidates, score them, and update a policy from comparative signals. All still follow dataset → tokenized batch → logits → scalar objective → backward graph → gradients → optimizer/scheduler update → evaluation/checkpoint. State the trainable parameters, loss denominator, data provenance, evaluation split, and artifact lineage for the chosen stage.

**Recall** What five contracts must be known besides model architecture?

**Mental model** Data, tokenization, objective, trainable state, evaluation, and checkpoint content change across stages even when the decoder architecture stays fixed.

**Practice labs**

- [Lab 32: Learn a weight and separate training from inference](reference/labs/32_learning_basics.md)
- [Lab 01: Trace a complete tiny-transformer training step](reference/labs/01_tiny_transformer_train.md)
- [Lab 05: Fine-tune a small model with LoRA adapters](reference/labs/05_lora_sft.md)
- [Lab 06: Work through a group-relative policy objective](reference/labs/06_grpo_objective.md)
- [Lab 07: Verify the GRPO generation-to-update loop](reference/labs/07_grpo_trainer.md)

## 2. Build causal batches with tokens, labels, masks, and packing

**What it is** A training batch groups examples for one computation. Tokens are the model's discrete input symbols, represented by integer IDs; labels specify the answers used to calculate the learning error. In next-token training, each eligible position predicts the following token. Padding fills unused positions so examples fit a rectangular tensor, while truncation discards positions beyond a length limit. A loss mask excludes selected labels from the objective; an attention mask determines which input positions can influence each prediction. They control different things.

Sequence packing places several examples in one tensor row to reduce padding. Position IDs describe positions within the intended sequence, and document boundaries determine whether one example may read another. Packing therefore requires a context policy as well as a storage plan: placing two texts next to each other does not automatically preserve the learning task. Start by tracing which tokens provide context and which labels contribute to loss.

**Objective** Produce correct shifted labels, ignored positions, attention masks, and packed examples.

**Prerequisite bridge** The selected training stage defines which text contributes to learning. A token ID is a vocabulary index; the model will output logits, one unnormalized score per possible next token. Cross-entropy turns those scores and the correct label into a scalar penalty; ignored labels do not contribute to its sum or valid-token denominator. A causal mask prevents a position from attending to future tokens, and a packed-example boundary also prevents it from attending to unrelated examples. A block-diagonal causal mask places one causal region per example along the diagonal of the token-to-token attention matrix. Tokens may read earlier positions within their own region; entries connecting different examples are blocked. Here construct the tensors; Lessons 3 and 4 explain their model and gradient paths.

**Why it matters** Padding, prompt masking, truncation, chat templates, and packing change the loss denominator and sometimes which tokens can attend to one another. A faster pipeline with a different batch is not equivalent training.

**Mechanism** A tokenizer maps text through a versioned vocabulary and normalization contract into token IDs; embeddings then map IDs to learned vectors. For causal LM, input ID at position `t` predicts the label at `t+1`. Padding and any non-learning prompt positions receive an ignore label and are excluded from the token-weighted loss denominator. Position IDs and the causal attention mask must remain consistent. Packing concatenates examples into fewer padded sequences, but a block-diagonal causal mask or the selected backend's explicit packed-sequence boundary metadata must prevent cross-example attention when independence is required. Position IDs follow the chosen position scheme; resetting them alone does not isolate examples unless that backend explicitly converts resets into attention boundaries. Track raw example ID, token count, kept/truncated region, unmasked tokens, and packed segment boundaries. Data licensing, privacy, deduplication, and strict train/evaluation separation are correctness constraints, not loader details.
First-fit packing is a placement rule: process examples in the declared order, put each into the first bin with enough free token capacity, and open a bin when none fits. It is simple and need not use the fewest possible bins. With capacity 8 and lengths 5, 3 and 4, the first two share one bin and the third starts another. Preserve each example's span when constructing its lower-triangular causal block. Lab 25 checks this allocation and mask structure; concatenating labels and validating a packed learning objective are later, separate work.

**Recall** Which token is predicted by the hidden state at position `t`?

**Mental model** A causal model predicts the next token. Padding and prompt regions may be excluded from loss; packing combines examples while preventing attention and labels from crossing example boundaries.

**Practice labs**

- [Lab 13: Decide which tokens contribute to the training loss](reference/labs/13_loss_masking.md)
- [Lab 25: Pack variable-length examples without crossing boundaries](reference/labs/25_sequence_packing.md)

## 3. Trace a decoder-only transformer

**What it is** A decoder-only transformer is a neural-network architecture that builds a representation for each token using the context it is allowed to read, then scores possible next tokens. An embedding turns a token ID into a vector. Each transformer block combines attention, a feed-forward network and residual connections. Attention mixes information from permitted positions; a head is one attention component with its own learned projections. A projection is a learned linear transformation. The feed-forward network, often called a multilayer perceptron (MLP), transforms each position's vector through additional learned operations. An activation function introduces a nonlinear change: its output cannot be expressed as just a weighted sum plus bias. Placing it between learned linear operations prevents the MLP from collapsing into a single matrix-and-bias operation. An activation function is the transformation; activations are the values produced by the network.

Activations are intermediate values produced during a forward pass. Normalization rescales them using statistics of the vector, and residual addition adds an earlier representation to a transformed one. Attention creates query, key and value vectors through learned projections: queries seek relevant context, keys supply information for matching, and values supply the information mixed into the result. Rotary positional embeddings (RoPE) encode relative position through rotations of query/key components. The final projection produces logits—unnormalized vocabulary scores. This architecture description explains the stages before the lesson follows their tensor shapes and memory costs.

**Objective** Follow embeddings, normalization, attention, residual paths, MLP, and logits with shapes.

**Prerequisite bridge** Batches contain IDs, masks, positions, and labels. The transformer maps those tensors into logits while retaining intermediate activations needed by backward.

**Why it matters** Shape reasoning reveals compute, activation memory, communication, and kernel efficiency. It also separates parameters that persist across steps from activations whose size grows with microbatch and sequence length.

**Mechanism** In this walkthrough, `B` is batch size, `S` is sequence length, `H` is hidden width, `heads` counts attention heads, and `head_dim` is the width of one head. `V` in a shape denotes vocabulary size; the attention value tensor also named `V` is a different use of the symbol. IDs `[B,S]` index token embeddings to `[B,S,H]`; positional information such as RoPE modifies attention coordinates. Normalization feeds Q, K, and V projections, commonly reshaped to `[B,heads,S,head_dim]`. Q selects what each position seeks, K represents what positions offer, and V carries content to mix. Causal attention combines QK scores, masking, softmax, and V aggregation without needing the full score matrix to be materialized by a fused backend. GQA shares fewer K/V heads across more query heads. The output projection returns width `H`, the residual preserves a direct path, and the MLP expands then contracts the hidden dimension before another residual. Final normalization and vocabulary projection produce logits `[B,S,V]`. A logit is an unnormalized score, not a probability. Softmax exponentiates scores and divides by their sum, giving nonnegative probabilities that sum to one. Cross-entropy for a known next token is the negative natural logarithm of its probability: confident correct predictions have small loss. A gradient describes how a small parameter change would change the loss; backward computes these sensitivities. A simple gradient-descent step subtracts learning rate times gradient. AdamW adapts parameter updates using running averages of gradients and squared gradients, so each parameter's update depends on its gradient history. Its decoupled weight decay separately shrinks parameter values toward zero, rather than mixing that shrinkage into the gradient used by the adaptive update.
The supplied tiny model uses learned positional embeddings: an integer position indexes a trainable vector that is added to the token embedding. It does not implement RoPE. Its LayerNorm subtracts a feature vector's mean and divides by `sqrt(variance + epsilon)`, then applies learned scale and offset. Its MLP uses GELU, the Gaussian error linear unit `x*Phi(x)`, where Phi is the standard normal cumulative probability; this provides a smooth nonlinear transformation between the expanding and contracting linear layers. These choices define the actual architecture to trace, rather than assuming every modern decoder uses the same positional or normalization method.

For one attention head, scaled dot-product attention computes `softmax(Q @ K.T / sqrt(head_dim) + mask) @ V`. The mask gives forbidden positions zero weight by excluding their scores; in a square causal case, a position may use itself and earlier positions. PyTorch's `scaled_dot_product_attention` expresses this operation and chooses a supported implementation. Use the correct query/key/value shapes and causal policy before inspecting the chosen backend in Lesson 10.

**Recall** Why must attention output return to the model width before the residual addition?

**Mental model** Token IDs select embeddings; repeated decoder blocks transform `[batch, sequence, hidden]`; the language-model head maps hidden states to vocabulary logits.

**Practice labs**

- [Lab 01: Trace a complete tiny-transformer training step](reference/labs/01_tiny_transformer_train.md)

## 4. Execute a correct training step

**What it is** A training step performs a parameter update. The forward pass computes predictions from current parameters; the loss turns prediction error into a scalar objective; backward computes gradients describing sensitivity of that loss to parameters; the optimizer then changes the parameters using those gradients and its update rule. Backward does not itself update the weights. PyTorch autograd is the automatic-differentiation system that records supported operations and applies their derivative rules.

A microbatch is a smaller group processed within a larger intended update. Several microbatches may contribute before the optimizer runs, provided their losses are normalized consistently. Gradient clipping limits the gradient's magnitude, while a learning-rate scheduler changes the update scale over training. These operations have an order because later stages consume earlier results. The distributed averaging and precision-scaler cases below are extensions of this basic loop, not prerequisites for understanding why one weight changes.

**Objective** Order forward, loss, backward, gradient handling, optimizer update, and zeroing correctly.

**Prerequisite bridge** The decoder produces logits and the batch supplies valid labels. Training converts their scalar loss into parameter updates through autograd and optimizer state.

**Why it matters** A loop can run quickly while applying the wrong gradient scale, clipping at the wrong time, accumulating stale gradients, or stepping its scheduler on the wrong cadence. Performance evidence is invalid until update semantics are tested.

**Mechanism** For the first full lab, autocast is a context that selects suitable computation dtypes for supported operations. Lab 01 encloses forward and loss in CUDA BF16 autocast, exits that context, then runs backward. BF16 has FP32-like exponent range but fewer precision bits; autocast does not convert every stored parameter or operation to BF16. This recipe does not use FP16 loss scaling. Lesson 7 explains that separate path and the wider accuracy comparison.

Gradient-norm clipping bounds the size of the combined parameter-gradient vector before an update. Compute its L2 norm and, when it exceeds a limit, scale gradients down together. A gradient `[3,4]` has norm 5; a limit of 1 scales it to `[0.6,0.8]`. `clip_grad_norm_` modifies the gradients and reports their pre-clipping norm; reject non-finite values before applying the optimizer. Clipping is not proof that every weight update has that norm, particularly with an adaptive optimizer. Keep clipping after backward and, when scaling is used later, after unscaling. Clear gradients after the update or before the next backward because backward accumulates into them. The optional save flag records a partial training snapshot; study complete resume state in Lesson 5 before using it as a checkpoint exercise.

Execute forward under the selected autocast policy, compute a token-weighted loss over valid labels, scale if FP16 requires it, and call backward to accumulate gradients. Across microbatches with unequal valid-token counts, combine loss numerators and denominators or weight each contribution so the gradient matches the reference batch. Unscale before clipping, check non-finite gradients, perform the optimizer update, advance the scheduler according to its declared unit, then clear gradients with the intended memory semantics. AdamW adds first and second moments and often FP32 master state; those persistent buffers affect both memory and checkpointing. DDP accumulation may suppress intermediate synchronization with `no_sync` while preserving the final reduction. The denominator also matters across ranks. With ordinary DDP averaging over R ranks, each rank can backpropagate R × local_loss_sum / global_valid_tokens. Count global valid tokens across all ranks and the entire intended accumulation window before normalization; accumulate scaled local sums and synchronize at the final microbatch. For two ranks with 100 and 300 valid labels, DDP's division by two then produces the same gradient as the concatenated 400-token reference. Averaging two rank-local means does not. This derivation assumes the default averaging behavior, not a custom communication hook or a rank-joining policy with different semantics. For the first single-GPU pass, focus on forward, loss, backward and update. The DDP/no_sync and multi-rank normalization derivation above is a preview for Lesson 11, not a requirement to launch distributed jobs now; autocast format choices are developed in Lesson 7.

**Recall** What accumulates when gradients are not cleared between backward calls?

**Mental model** Autograd records the forward graph, backward accumulates gradients into parameter buffers, and the optimizer updates parameters from those buffers and its state.

**Practice labs**

- [Lab 01: Trace a complete tiny-transformer training step](reference/labs/01_tiny_transformer_train.md)
- [Lab 02: Trade microbatch size for peak memory](reference/labs/02_gradient_accumulation.md)
- [Lab 13: Decide which tokens contribute to the training loss](reference/labs/13_loss_masking.md)
- [Lab 25: Pack variable-length examples without crossing boundaries](reference/labs/25_sequence_packing.md)

## 5. Evaluate, checkpoint, and resume exactly

**What it is** Evaluation measures a model's behavior on examples not used for the current parameter updates. A checkpoint is a saved snapshot of training state; resuming means restoring enough state to perform the intended next update. Saving model weights alone can support inference or a new fine-tuning run, but does not necessarily reproduce a paused training run. Optimizer history, learning-rate position and random choices can affect what happens next.

A random-number generator (RNG) produces reproducible pseudorandom sequences from its state. An epoch is a pass through a dataset; a sampler chooses examples and their order; a data cursor records how far consumption has progressed. Atomic checkpoint publication makes a completed snapshot become visible as a unit rather than exposing a partly written file. This saved restart checkpoint differs from activation checkpointing, which later recomputes intermediate values during backward to save GPU memory.

**Objective** Save every state needed to reproduce the next training step after interruption.

**Prerequisite bridge** A correct step is a state transition. Exact resume means restoring the complete pre-transition state and consuming the same next data, not merely loading similar weights.

**Why it matters** Missing optimizer, scheduler, scaler, RNG, sampler, or partial-accumulation state can silently fork the run. A checkpoint that loads successfully may still fail continuation equivalence.

**Mechanism** Treat checkpointing as a state machine. Persist model and adapter parameters; optimizer moments and step counts; scheduler position; mixed-precision scaler or FP8 recipe state; CPU and accelerator RNG; distributed sampler epoch and data cursor; gradient-accumulation position and any retained gradients; and version/config/artifact identity. Write to a temporary private path, synchronize participating ranks, verify completeness, then publish atomically. Evaluation must use held-out data and aggregate loss by valid tokens rather than averaging unequal batches. Test resume by comparing uninterrupted and interrupted runs from the same checkpoint boundary; exact deterministic recipes may require bitwise equality, while nondeterministic supported kernels need an explicit tolerance-based claim.

**Recall** Which states besides model weights affect the next update?

**Mental model** Exact resume may require model, optimizer, scheduler, scaler, RNG, data position, sampler, and accumulation state. Evaluation uses held-out data and must not update training state.

**Practice labs**

- [Lab 24: Reproduce the next update after checkpoint restoration](reference/labs/24_checkpoint_resume.md)

## 6. Build a training memory ledger

**What it is** A training memory ledger accounts for the values that must exist at each stage and for how long they are needed. Persistent state survives between steps, including parameters and optimizer history. Activations are intermediate forward values, some of which backward needs. Temporary state, such as an operation's workspace, may exist only while that operation runs. Peak memory is the greatest simultaneous requirement, not the sum of every allocation made over the entire run.

Allocated memory is storage currently held by tensors; reserved memory also includes blocks retained by the allocator for reuse. Some mixed-precision recipes keep wider master weights for updates alongside lower-precision computation values. Offload moves selected state to another storage tier, usually host memory, and introduces transfer costs. Distributed buffers add another lifetime to the ledger. Their detailed sharding rules come later; first identify which values must coexist even for one ordinary training step.

**Objective** Account for weights, gradients, optimizer state, activations, temporaries, communication buffers, and allocator reserve.

**Prerequisite bridge** The correct update and resume lessons established model parameters, gradients, optimizer buffers and saved activations. A phase-aware ledger now places persistent and temporary state in forward, backward, optimizer and checkpoint time; distributed replication/sharding will build on this ledger later.

**Why it matters** Checkpoint file size is not live training footprint. Optimizer moments, master weights, saved activations, temporary workspaces, gathers, reduction buckets, graph pools, and allocator headroom can exceed visible parameter bytes.

**Mechanism** Start with parameter count × storage bytes for model, gradients, master weights, and each optimizer moment. Add activations by layer, microbatch, sequence, and dtype, noting which are retained until backward or recomputed. Add attention/MLP temporaries, library workspaces, FSDP all-gather buffers, DDP buckets, FP8 metadata, graph pools, and allocator reserve. Draw live intervals and calculate separate forward, backward, and optimizer peaks. Label every value calculated, measured, or unknown; reconcile the ledger with allocator snapshots and peak APIs. Model files, optimizer shards, and temporary load/staging buffers form a separate checkpoint/load ledger.

**Recall** Which training state often exceeds parameter memory under Adam?

**Mental model** Persistent model/optimizer states scale with parameters; activations scale with batch and sequence; temporaries and collectives create phase-specific peaks. Activation or optimizer offload trades HBM for host-memory capacity and transfer latency, so it is a capacity escape hatch rather than a default speed optimization.

**Practice labs**

- [Lab 21: Validate precision changes across a full training update](reference/labs/21_mixed_precision_training.md)

## 7. Use BF16, FP16, and FP8 without losing the signal

**What it is** Mixed precision uses different numerical formats for different parts of a training calculation. FP32 is 32-bit floating point; FP16 is 16-bit floating point; BF16 means brain floating point 16; FP8 uses an 8-bit floating-point format. Range describes the largest and smallest magnitudes represented, while precision describes the spacing between representable values. A smaller format can reduce storage or enable faster supported arithmetic, but can also alter losses, gradients and updates.

Autocast selects operation-appropriate formats within a region; it does not simply convert all model state to one dtype. Loss scaling multiplies the loss before backward and later unscales gradients to help small gradients survive limited range. NVIDIA Transformer Engine is a library for supported low-precision transformer operations and scaling state. An amax value is an observed maximum absolute magnitude, and a recipe defines how such measurements select scales and formats. These mechanisms must be checked across the complete update, not only its forward output.

**Objective** Select state-specific precision and verify loss, gradients, updates, and kernels.

**Prerequisite bridge** The memory ledger shows where bytes live; mixed precision chooses formats for individual states and operations without changing the mathematical training objective.

**Why it matters** “FP8 training” does not mean every tensor is FP8. Unsupported shapes, cast/scaling overhead, overflow/underflow, cold scaling history, or a changed optimizer can erase performance or learning equivalence.

**Mechanism** TF32 is a compute mode for selected FP32 matrix operations, not a storage type. BF16 usually keeps FP32-like exponent range with fewer fraction bits. FP16 commonly uses loss scaling: scale loss, backward, unscale gradients, check non-finite values, clip, and update. Transformer Engine FP8 wraps supported forward modules in `te.autocast`; backward inherits the forward precision context. Delayed scaling uses prior amax history, while current or block recipes have different metadata and support. Keep normalization, reductions, optimizer/master state, and sensitive paths in suitable higher precision. Compare a warmed recipe state and record scaling/amax behavior, loss, gradients, update deltas, finite checks, and chosen kernels.

FP8 is a family rather than one encoding: E4M3 provides more fraction bits, while E5M2 trades fraction precision for a wider exponent range. A hybrid recipe may assign different encodings to forward tensors and gradients; use the selected Transformer Engine recipe, not a blanket dtype claim. Stochastic rounding probabilistically chooses adjacent representable values to reduce systematic rounding bias over repeated conversions. It does not guarantee per-value accuracy or recover values lost through saturation. Block scaling, outlier handling and higher-precision operations are parts of a complete numerical recipe. NVIDIA NVFP4 recipes can combine stochastic rounding and transforms, but those Blackwell-specific training paths are comparison-only here, not an H100 lab or a reason to install an unsupported recipe.
Numerical validation must follow the entire update. Start the compared paths from matching parameters, optimizer state and inputs. Compare scalar loss, parameter gradients and parameter-update deltas before using separate warmed loops for timing. Relative L2 error is the magnitude of candidate-minus-reference divided by reference magnitude, treating matching named tensors as one vector. It measures aggregate discrepancy, not each element's worst error. Lab 21 sums squared elements across matching tensor names and guards the denominator at `1e-12`; scalar loss instead uses absolute difference divided by the guarded absolute reference loss. A missing gradient tensor is a structural failure, not a small numerical error. These short comparisons detect update changes but cannot establish convergence.

A finite-value check rejects NaN (not a number) and positive or negative infinity before applying a tolerance. Check every compared reference and candidate tensor, then each error and its norm calculation before aggregating results: NaN can bypass a threshold comparison or disappear when a maximum is taken. Large finite inputs can also overflow a norm calculation, so finite inputs alone do not certify a finite error. These checks protect the acceptance decision; they do not replace the independent reference or establish long-run convergence.

**Recall** Why does FP16 commonly need gradient scaling while BF16 often does not?

**Mental model** Mixed precision assigns dtypes to compute and state while preserving sensitive operations or accumulation. Transformer Engine manages FP8 recipes and scaling for supported operations. FP8 weight caching retains a quantized copy of unchanged weights across microbatches in an accumulation window, avoiding repeated conversions. Fused weight-gradient accumulation writes matrix-product gradients directly into an accumulation buffer, combining gradient production and addition instead of requiring a separate temporary gradient and addition step. These techniques can remove conversions or launches only for supported recipes, modules and shapes. Weight caching still needs numerical checks because changing scale history can make cached and freshly converted values differ.

**Practice labs**

- [Lab 21: Validate precision changes across a full training update](reference/labs/21_mixed_precision_training.md)
- [Lab 22: Qualify a warmed Transformer Engine FP8 recipe](reference/labs/22_transformer_engine_fp8.md)

## 8. Trade accumulation and recomputation for memory

**What it is** Gradient accumulation and activation checkpointing are two different ways to change a training step's memory needs. Accumulation processes smaller microbatches, combines their correctly weighted gradients and performs one optimizer update for the intended effective batch. It saves memory by not holding every example's activations at once. The global batch counts the work contributing to that update across microbatches and, where used, ranks; variable valid-token counts require token-aware weighting.

Activation checkpointing retains selected forward values and recomputes missing intermediates when backward needs them. It trades extra computation for fewer saved activations. Selective recomputation repeats chosen operations or regions; full-block recomputation repeats a larger region. This is not saving a restart checkpoint to disk. The two techniques can be combined, but each changes a different part of the step, so measure memory, total work and update equivalence separately.

**Objective** Compare microbatching and selective/full activation recomputation at fixed effective tokens.

**Prerequisite bridge** The training step defines effective tokens and update cadence; the memory ledger identifies saved activations that dominate peak capacity. Accumulation and checkpointing change different parts of that ledger.

**Why it matters** Accumulation reduces per-microbatch activations but does not remove parameter/optimizer state. Checkpointing removes selected saved activations but executes forward work again during backward. Comparing different effective batches hides the real exchange.

**Mechanism** Hold global valid tokens per optimizer update constant. Sweep microbatch size and accumulation count, using token-weighted loss and `no_sync` where appropriate. For checkpointing, mark the layer graph, the bytes saved by each candidate boundary, and the FLOPs or kernels replayed. Selective recomputation targets large, cheap-to-recreate intermediates; full-layer checkpointing is simpler but may repeat expensive attention and MLP work. PyTorch's non-reentrant checkpointing implementation records the autograd dependency graph during forward while omitting selected saved tensors. During backward it recreates the values needed by that graph and, with early stopping enabled, stops recomputation once those intermediates exist. Select this implementation explicitly with `use_reentrant=False` where qualified, and test stateful modules, random operations, autocast/FP8 state and side effects. Record peak memory, step-time distribution, valid tokens/s, and numerical equivalence.

**Recall** Which technique reduces saved activations by repeating forward computation?

**Mental model** Accumulation reduces per-microbatch activation size but adds launches and delays updates; checkpointing discards selected activations and recomputes them during backward.

**Practice labs**

- [Lab 02: Trade microbatch size for peak memory](reference/labs/02_gradient_accumulation.md)
- [Lab 14: Compare no, selective, and full recomputation](reference/labs/14_activation_checkpointing.md)

## 9. Prevent input-pipeline starvation

**What it is** The training input pipeline turns stored examples into GPU-ready batches. It reads and transforms examples, assembles them through collation, and transfers the resulting tensors from host to device (H2D). Workers are processes or threads preparing data. Prefetch means preparing future batches before the trainer requests them; queue depth counts prepared batches waiting for consumption. Starvation occurs when the GPU has reached its next step but the needed batch is not ready.

Pinned host memory supports suitable asynchronous transfers, and nonblocking submission allows the CPU to continue before a copy finishes. Neither removes the need for correct buffer lifetime and dependencies. More workers can prepare faster or compete for the same storage and CPU resources. In training, sample identity and order are part of the workload: feeding different examples or dropping slow ones is not a valid optimization of the same learning task.

**Objective** Keep training data ready without changing sample order or semantics.

**Prerequisite bridge** Accumulation and recomputation optimize the GPU step only when a valid next batch is available. The input pipeline is a distributed producer system whose correctness includes exact sample ownership.

**Why it matters** Loader starvation creates idle gaps; careless tuning can duplicate, omit, reorder, or retokenize data differently. Throughput gained by changing examples is not an optimization.

**Mechanism** Decompose reading, decompression, parsing, tokenization, packing, collation, pinning, and H2D transfer. Record ready-time distributions and queue depth beside GPU steps. Use synthetic/resident controls to isolate the pipeline. Seed workers and distributed samplers, call epoch updates correctly, and verify global sample IDs and unmasked-token counts. Persistent workers avoid process restart; prefetch hides producer variance; offline tokenization removes repeated CPU work but creates a tokenizer/config/revision-bound artifact. On shared storage, measure per-node behavior and contention before adding workers. Pinned batches must live until nonblocking copies finish.
A dataset maps an example index to sample content, and a collator combines samples into tensors and metadata. PyTorch DataLoader executes that preparation in the calling process or in configured worker processes; positive-worker prefetch stages future batches. Fix index order and sample construction before comparing settings. A digest is a compact fingerprint of a byte sequence. Lab 26 feeds ordered sample IDs and token bytes into SHA-256, a cryptographic hash function, so matching digests support unchanged order and content for that exact serialization. They do not establish data quality or replace checks on the intended samples. A sampled queue size is only an observation at that moment; measure the consumer's actual wait as well.

**Recall** Which trace pattern indicates the GPU is waiting for the next batch?

**Mental model** Storage, tokenization, collation, packing, pinning, and transfer form a staged producer. Queue depth and worker count trade latency hiding against memory and contention.

**Practice labs**

- [Lab 26: Diagnose a slow training-data producer](reference/labs/26_input_pipeline.md)

## 10. Profile fused operations and CUDA Graphs in training

**What it is** Fusion combines operations so a program may launch fewer kernels and avoid writing some intermediate tensors. Compilation transforms a program region into executable implementations. A CUDA Graph represents GPU operations and their dependencies as a reusable execution plan; capture records compatible work, and replay submits that plan again with less repeated host setup. Replay is not a saved numerical result and does not itself fuse kernels. These are related but distinct techniques.

For training, the replayed work can change parameter, gradient and optimizer-state values while using retained storage. A graph memory pool manages allocations associated with captured work. Compiler guards check assumptions such as shape; a graph break ends a compiler-captured region and is not the same thing as a failed CUDA Graph capture. A profiler records execution activity, and NVIDIA Tools Extension (NVTX) annotations name regions in a trace. The lesson applies these tools to the complete update rather than assuming an optimized forward pass guarantees faster training.

**Objective** Reduce launch and intermediate traffic while respecting autograd and optimizer semantics.

**Prerequisite bridge** A correct update, explicit memory/precision policy and a supplied next batch establish the single-GPU baseline. Fusion, compilation and graph replay now target local launch gaps and intermediates before distributed placement and communication are introduced.

**Why it matters** Training graphs include autograd, mutation, RNG, optimizer state, dynamic shapes, and collective boundaries. A transformation that works for an inference fragment can produce graph breaks or incorrect gradients in training.

**Mechanism** Use PyTorch Profiler with a scheduled active window and NVTX phase ranges for input, forward, loss, backward, optimizer, and communication. Identify repeated pointwise chains or CPU gaps. Prefer maintained fused operators, then qualified `torch.compile`, and verify graph breaks, guards, generated kernels, intermediate allocations, and backward equivalence. CUDA Graphs capture stable addresses and control flow after warm-up; graph pools retain memory and buckets handle a bounded set of microbatch/sequence shapes. Separate compile, capture, and replay cost. Preserve RNG advancement, scaler/FP8 state, gradient zeroing, and collective dependencies.

**Recall** Which training behaviors make graph capture less stable than a fixed inference loop?

**Mental model** Compilation can fuse forward/backward operators; CUDA Graphs can replay stable step regions. Dynamic shapes, optimizer branches, and allocation changes require buckets or fallbacks.

**Practice labs**

- [Lab 27: Separate compiled expressions from captured training steps](reference/labs/27_fused_graph_trace.md)
- [Lab 30: Inspect the operators in a tiny training step](reference/labs/30_training_profiler.md)

## 11. Choose DDP and FSDP2 from state placement

**What it is** Distributed Data Parallel (DDP) keeps a complete model replica on each participating process. The input pipeline supplies each process its assigned data; DDP combines gradients so updates stay consistent. A rank is a process identifier; a replica is a complete copy; a shard is one piece of a larger tensor or state. Fully Sharded Data Parallel version 2 (FSDP2) divides parameters and associated training state across ranks and gathers needed parameter pieces for computation. It targets state capacity as well as distributed execution.

A collective involves a group of ranks: all-reduce combines values and returns the result to all; all-gather assembles their pieces; reduce-scatter combines values but returns a different reduced piece to each rank. ZeRO, the Zero Redundancy Optimizer family, describes related stages of training-state sharding. These terms explain where state lives and how it moves; they do not imply that adding ranks always reduces step time.

**Objective** Compare replicated and sharded training state on two one-GPU nodes.

**Prerequisite bridge** The memory ledger divides model, gradient, and optimizer state; distributed data parallelism decides which ranks own copies or shards and which collectives reconstruct a consistent update.

**Why it matters** DDP and FSDP2 solve different constraints. A small two-rank job may run faster with replication, while a larger model may require sharding merely to fit.

**Mechanism** DDP replicates model and optimizer state. Each rank computes gradients from its own data; ordinary DDP all-reduces and averages them so every rank applies the same parameter update. Reduce-scatter alone returns gradient shards, not the complete gradients required by this replicated optimizer; an all-reduce may internally combine reduce-scatter and all-gather without changing that result. FSDP2 shards selected parameter, gradient, and optimizer states, all-gathers parameters for computation, and reduce-scatters gradients back to owners. ZeRO-style stages describe progressively sharded optimizer, gradient, and parameter state; the exact framework implementation and transient buffers still matter. Freeze global non-padding tokens, sampler ownership, optimizer semantics, and loss reduction. Record persistent bytes, transient all-gathers, communication buckets, overlap, slowest-rank step, and checkpoint format/restart behavior.
For DDP, initialize the process group, bind each process to its local GPU, create the model and wrap it before constructing the optimizer used for training. Assign each rank its intended data; DDP does not split a supplied global batch for you. For FSDP2, apply `fully_shard` to the selected inner modules and then the root, and construct the optimizer from the resulting parameters afterward. Its mixed-precision policy declares parameter computation and gradient-reduction dtypes separately. This combines Lesson 7's numerical policy with state ownership; it is not simply converting the whole model to a smaller dtype. Trace which parameters gather for a module and when they can be resharded before comparing memory and update behavior.

**Recall** Which state does DDP replicate and which collective synchronizes gradients?

**Mental model** DDP replicates model and optimizer state and reduces gradients; FSDP2 shards selected state and gathers what each computation needs. Megatron Core's distributed optimizer separately shards optimizer state and coordinates reduce-scatter/update/all-gather, but this two-rank PyTorch course does not qualify its production implementation.

**Practice labs**

- [Lab 00: Verify the distributed training allocation](reference/labs/00_cluster_preflight.md)
- [Lab 03: Train replicated models with DDP](reference/labs/03_ddp_train.md)
- [Lab 04: Observe FSDP2 sharded training state](reference/labs/04_fsdp2_train.md)

## 12. Understand TP, PP, CP, and EP mechanics

**What it is** Model parallelism divides one model's work when replication alone is not the desired layout. Tensor parallelism (TP) splits tensors and parts of an operation across ranks. Pipeline parallelism (PP) puts groups of layers on different stages and passes intermediate activations between them. Context parallelism (CP) partitions sequence context and coordinates the attention information needed across those partitions. Expert parallelism (EP) distributes the experts in a mixture-of-experts (MoE) layer: a router selects which expert networks process each token.

A pipeline bubble is idle time while a stage waits for work or results. All-to-all communication exchanges different pieces between ranks, often for expert routing. Grouped matrix multiplication, also called grouped GEMM, executes several matrix problems together. A capacity factor sets an expert's token-capacity allowance relative to a stated average. Sequence parallelism can instead partition particular non-attention activations or operations within another parallel layout; it is not a universal synonym for CP. Each scheme changes a different ownership boundary.

**Objective** Map tensor, pipeline, context, and expert parallelism to partitioned state and communication.

**Prerequisite bridge** DDP and FSDP partition data and training state. Model parallel strategies partition computation or model structure when one rank cannot efficiently own the full layer, sequence, pipeline, or expert set.

**Why it matters** The same parallel degree can produce very different communication, bubbles, memory, and load balance. A topology choice must begin from what is sharded and when consumers need it.

**Mechanism** Tensor parallel splits matrix dimensions and exchanges partial activations through all-reduce, reduce-scatter, or all-gather; sequence parallel can shard compatible activation work alongside TP. Pipeline parallel assigns layer stages and sends activations/gradients, with microbatch schedules creating fill/drain bubbles. Context parallel partitions a long sequence and communicates K/V or attention state needed across partitions. Expert parallel places MoE experts on ranks, routes tokens with all-to-all, and uses grouped GEMM to batch several local expert operations. Router balance, capacity factors, dropped/padded tokens, and slow experts determine useful work. Map each collective direction, tensor shape, owner, and dependency to the physical topology.
Use matrix algebra to derive a partition before launching it. For `Y = X @ W`, splitting W into output-column shards gives each rank different columns of Y; all-gather concatenates them in the declared order. Splitting X's features and matching rows of W instead gives partial products whose sum is Y, so all-reduce reconstructs it. If G is the output gradient, the full derivatives are `dW = X.T @ G` and `dX = G @ W.T`. A column shard produces a weight-gradient shard and one contribution to dX; sum those input-gradient contributions. Normalize each partial loss by the global output-element count so summing contributions preserves the original mean objective. Validate reconstructed outputs, gradients and one update; retained full references must stay in the memory accounting.

Point-to-point communication sends a tensor from one rank to another instead of involving the whole group. In a pipeline, the receiving rank starts its local differentiation from that activation and sends its activation gradient back. A plain send/receive is not a distributed autograd graph: the sender must apply the returned gradient to its retained local activation. In the context mechanics example, sum local squared values and divide by the full context's element count to preserve the global mean; gathering context shards checks reconstruction, not distributed attention.

Expert routing groups tokens by owner, exchanges those groups with all-to-all, applies each owner's expert, then reverses the routing to restore input order. The training path must also return token gradients and update expert weights. Lab 12 explicitly carries these values across communication boundaries rather than assuming communication builds an autograd connection. Batched matrix multiplication (`bmm`) computes corresponding matrix products in a batch; padding unequal expert groups to a common size permits that comparison but performs extra work. Keep padding costs and the actual grouped implementation visible.

A distributed acceptance decision must reach the same verdict on every rank. Lab 19 converts any non-finite tensor, norm or relative error into a failing infinite error, then reduces the integer pass flags with MIN across all ranks. One failed rank therefore prevents publication everywhere. This infinite error is a rejection marker, not a valid measurement. Keep ranks participating through the verdict collective rather than raising on just one rank before its peers reach that operation.

**Recall** Which strategy partitions layers, which partitions tensor dimensions, and which routes tokens to experts?

**Mental model** TP splits operator dimensions, PP splits layer stages, CP splits sequence context, and EP splits experts. Sequence parallelism partitions selected activations alongside TP. A virtual pipeline stage is a logical chunk of layers; one physical rank can own several chunks and interleave their microbatch work. An asymmetric layout assigns different numbers or types of layers to physical stages to balance their costs. These choices change assignment and scheduling, not the model's required layer order. Each strategy targets a different capacity, bubble, or scaling constraint.

**Practice labs**

- [Lab 12: Trace expert routing, gradients, and grouped work](reference/labs/12_moe_expert_parallel.md)
- [Lab 19: Partition a linear layer and verify its backward pass](reference/labs/19_tensor_parallel_linear.md)
- [Lab 29: Follow pipeline and context partitions through backward](reference/labs/29_parallelism_mechanics.md)

## 13. Overlap communication with useful backward work

**What it is** Communication overlap means a transfer or collective progresses while independent useful computation also runs. In training, backward produces gradients at different times, so an already-ready group can communicate while later gradients are still being computed. A gradient bucket is a group of gradient values communicated together. Readiness means all inputs needed by that communication are available. A dependency says one operation must wait for another; the critical path is the chain of dependencies that determines completion time.

Exposed communication is the portion that still delays completion after available overlap. Returning from an asynchronous API call does not prove that the network and useful arithmetic overlapped. NVIDIA Scalable Hierarchical Aggregation and Reduction Protocol (SHARP) is network-assisted collective processing on supported infrastructure, not a feature supplied merely by an H100 GPU. This course first teaches software scheduling and observed overlap on the actual two-node path.

**Objective** Measure exposed communication rather than total collective duration.

**Prerequisite bridge** Each parallel strategy creates a collective dependency. Overlap is possible only after a chunk becomes ready and before a consumer needs its result.

**Why it matters** Total collective duration can remain unchanged while step time improves, or an “asynchronous” collective can remain fully exposed. The optimization target is critical-path wait, not merely a shorter communication bar.

**Mechanism** Mark backward layer readiness, bucket fill, collective launch, transfer, completion, and optimizer dependency on a rank-aligned timeline. DDP gradient overlap starts reductions while earlier layers still backpropagate; FSDP can overlap gradient reduce-scatter and next-forward parameter all-gather with independent work. TP, EP all-to-all, and PP point-to-point have their own runways. Smaller buckets start sooner but increase call count; larger buckets amortize latency but start later. Reduced-precision communication can cut nominal payload size, but an optimization is acceptable only when its declared numerical and collective-compatibility checks pass. Record per-rank arrival skew and contention because communication and compute can share memory/fabric resources.

Lab 33 now exposes real DistributedDataParallel (DDP) bucket construction and communication hooks. `bucket_cap_mb` is a capacity hint, not a promise that every bucket has that size or that changing it changes the bucket count. Parameter sizes and readiness constrain packing; startup buckets may be rebuilt. Record each public `GradBucket`'s actual uncompressed size after warm-up. A large cap can leave communication until late in backward, while a small cap can launch more, earlier collectives and spend more time on startup. All ranks must preserve collective order. The selected topology and message sizes determine the useful trade-off; there is no universal 100-MB optimum.

The allreduce hook averages FP32 buckets. FP16 and BF16 hooks cast communication buffers down, reduce them and cast back; they reduce nominal payload precision, not the model's stored parameter dtype. FP16 can overflow sooner, while BF16 has coarser precision. PowerSGD approximates sufficiently large matrix-shaped gradients with low-rank factors. Forming and reducing those factors costs compute and extra collectives; small tensors may remain uncompressed. Error feedback retains approximation residuals, and warm start reuses factor information. These are lossy algorithms, not lossless transport compression.

PowerSGD begins with uncompressed iterations. Its start threshold must allow DDP's bucket rebuilding to finish, and the measured window must actually reach compressed steps. The lab keeps error feedback and warm start enabled and reports their rank/start settings. It fixes the model, global batch, loss mean and plain SGD update, and independently evolves a full-batch FP32 reference. Exact uncompressed runs must agree within FP32 tolerances; lossy runs report gradient and parameter trajectory error rather than claiming equality. Both paths must perform finite, nonzero updates. A short synthetic trajectory is a diagnostic: production adoption still requires a predeclared task-quality or convergence threshold over sufficient training.

A gradient hook is a callback triggered at a defined point in differentiation. A post-accumulate hook runs after a parameter's gradient has been accumulated, allowing an already-complete gradient to be reduced while independent backward work continues. It must not communicate an unfinished accumulation window. Lab 28 retains asynchronous Work handles, joins them before using reduced gradients and normalizes the result. A DDP communication hook instead replaces the bucket's communication path and returns a future for its result. Keep the framework's averaging contract and identical collective order on every rank; observing readiness is different from silently changing the reduction.

Lab 33's Tanh stages use the hyperbolic tangent, a smooth elementwise function with outputs between -1 and 1. It makes the repeated linear stages nonlinear while keeping a small deterministic workload. Plain stochastic gradient descent (SGD) applies `parameter -= learning_rate * gradient` without AdamW's adaptive history in this configuration, making the independent update check direct. Apply the relative-L2 policy from Lesson 7 to the whole matched gradient or parameter state; after several lossy steps, the error describes diverging trajectories, not just the latest compressed bucket.

**Recall** What dependency allows a gradient bucket to reduce before backward completes?

**Mental model** Gradients become ready from later to earlier layers. Buckets can launch collectives as soon as their gradients are complete while backward continues elsewhere. Lower-precision collective buffers change bandwidth and error, while SHARP can offload supported reductions to the fabric; both require topology- and recipe-specific qualification.

**Practice labs**

- [Lab 28: Reduce gradients when they become ready](reference/labs/28_communication_overlap.md)
- [Lab 33: Tune real DDP buckets and communication hooks](reference/labs/33_ddp_buckets.md)

## 14. Perform SFT and LoRA with explicit savings

**What it is** Supervised fine-tuning (SFT) continues training an existing model on selected input/target examples, such as instructions paired with desired responses. Low-rank adaptation (LoRA) changes how an update is represented: selected base weights stay frozen while smaller trainable matrix factors contribute an additional transformation. A frozen parameter participates in computation but is not changed by the optimizer. The base model is the starting model, and an adapter is the added trainable component or its saved state.

LoRA's rank is the limited dimension through which the update factors are composed; it is unrelated to a distributed process's rank number. SFT specifies the supervision, while LoRA specifies which parameters express the change, so they can be used together. LoRA is not automatically quantization, and freezing weights does not eliminate every activation or backward cost. End-of-sequence (EOS) labels and response masks determine which behavior the fine-tuning objective actually teaches.

For an input column vector x, a bias-free projection produces `Wx`. LoRA adds a second path: `y = Wx + s * B(Ax)`. A maps input width to the smaller rank r, B maps that r-dimensional result to output width, and s scales the update. Train A and B while W stays fixed. For a hand calculation, let W be the 2-by-2 identity, A=[1, 0], B=[0, 2] as a column, s=0.5 and x=[3, 4]. Then Ax=3, B(Ax)=[0, 6], the scaled update is [0, 3], and y=[3, 7]. This illustrates composition, not the library's initialization. Standard scaling is alpha/r unless a different recipe is explicitly selected; the lab's configuration defines it. The shapes of A and B explain the parameter count below.

**Objective** Measure trainable-state reduction and evaluate adapter behavior independently from throughput.

**Prerequisite bridge** The core training path now explains updates, optimizer memory, precision, data flow and distributed placement. Apply that ledger to SFT, which changes the supervised-data contract, and LoRA, which changes the trainable parameterization. The supplied single-GPU adapter lab does not require a distributed run; the preceding distributed lessons provide context for scaling it later.

**Why it matters** Small trainable state is often confused with proportionally smaller total memory or faster steps. The frozen base still occupies memory and participates in forward/backward activation computation.

**Mechanism** Freeze a pinned base-model and tokenizer revision. Apply the exact chat template, EOS, truncation, prompt-loss mask, and held-out split. For a projection `W` of shape `d_out × d_in`, LoRA adds trainable factors of rank `r` with `r(d_in+d_out)` parameters instead of `d_in*d_out`, usually scaled before adding to the frozen projection. Count adapter weights, gradients, and optimizer states separately from base weights, activations, workspaces, and temporary tensors. Save adapter configuration plus base revision so deployment can reconstruct the effective model. Evaluate task behavior and regression sets; throughput is a separate system outcome.
The Hugging Face Transformers library loads the pinned model and tokenizer implementation. PEFT, the Parameter-Efficient Fine-Tuning library, attaches the configured LoRA factors to the selected projection modules. Use an immutable revision, inspect which module names are targeted and verify that only the intended parameters require gradients before constructing the optimizer. A configuration object specifies the adapter rank and scale; it is not evidence that every requested layer was adapted. Check the actual trainable parameter count and frozen-base behavior before interpreting memory savings.

**Recall** Which base parameters change during standard LoRA training?

**Mental model** LoRA learns low-rank updates while base weights remain frozen. It reduces trainable parameters and optimizer state but does not automatically remove base-model activations or all compute.

**Practice labs**

- [Lab 05: Fine-tune a small model with LoRA adapters](reference/labs/05_lora_sft.md)
- [Lab 13: Decide which tokens contribute to the training loss](reference/labs/13_loss_masking.md)

## 15. Understand GRPO objective and system loop

**What it is** Group Relative Policy Optimization (GRPO) is a reinforcement-learning method that adjusts a model using several scored answers to the same prompt. The policy is the model's distribution over possible actions—in this case generated tokens. A rollout is a sampled answer, a reward is its score, and an advantage expresses how favorable it was relative to a baseline, here derived from the group. This provides a learning signal without requiring a desired token at every position as supervised training does.

The old policy generated the sampled answers; a reference policy can separately anchor behavior. A probability ratio compares a sampled token's new and old probabilities. The surrogate objective is a tractable training expression using these sampled quantities. Clipping moderates that expression; it is not a hard bound on every policy change. Kullback–Leibler (KL) divergence measures a difference between probability distributions. Reward hacking means improving the score without improving the intended behavior, so successful optimization of the toy objective is not proof of answer quality.

**Objective** Compute group-relative advantages and locate rollout, reward, policy, and synchronization costs.

**Prerequisite bridge** SFT learns from fixed target tokens. GRPO-style post-training samples several outputs from the current or near-current policy and learns from relative reward within each group.

**Why it matters** The objective is only one part of the system. Rollout generation, verifier/reward execution, variable output lengths, policy weight transfer, and stale samples can dominate time or corrupt learning.

**Mechanism** For each prompt, sample a group of candidates under a recorded policy version. Score them, subtract the group mean, and commonly divide by a stabilized standard deviation to form relative advantages. Zero-variance groups need an explicit rule rather than division by zero. A clipped policy-ratio surrogate reduces the incentive for some large probability changes; it is not a hard bound on the full policy update. A recipe may additionally penalize divergence from a reference policy, with an explicit coefficient. The system may colocate rollout and training to avoid weight transfer or disaggregate them for independent scaling; either choice creates queueing and synchronization boundaries. Record reward components, candidate lengths, acceptance/filtering, policy version, rollout latency, verifier latency, and trainer step time. Test for reward hacking with held-out and adversarial cases. The old policy is the frozen behavior policy that generated the rollout; the reference policy is a separately chosen anchor, often the initial model. They need not be the same. For a sampled token, ratio = exp(new_log_probability - old_log_probability) compares its probability before and after the proposed update. For advantage A and clip width epsilon, the maximized surrogate is min(ratio × A, clamp(ratio, 1-epsilon, 1+epsilon) × A). Lab 06 minimizes the negative surrogate plus a toy reference penalty with coefficient 0.02; it uses exp(d)-d-1 with d = reference_log_probability-new_log_probability as a sampled KL estimator. This finite synthetic objective checks differentiation, not full-policy KL or model quality.
TRL, the Transformer Reinforcement Learning library, supplies a GRPO trainer that coordinates generation, reward evaluation and updates. A reward callback receives generated candidates and returns their scores; a gradient hook can observe whether those scores produce gradients on trainable adapters. Use the pinned model/tokenizer and LoRA configuration, create a small declared prompt dataset, and compare trainable parameters before and after the run. Alternating controlled rewards within a group provides nonzero variation for this plumbing check. Require finite nonzero gradients and a real parameter change; those observations do not establish that the reward measures useful answer quality.

**Recall** Why compare responses within a group generated for the same prompt?

**Mental model** A rollout system generates candidates, scores them, normalizes relative rewards, and updates a policy under clipping or divergence constraints.

**Practice labs**

- [Lab 06: Work through a group-relative policy objective](reference/labs/06_grpo_objective.md)
- [Lab 07: Verify the GRPO generation-to-update loop](reference/labs/07_grpo_trainer.md)

## 16. Deliver a causal training optimization report

**What it is** A causal training-optimization report argues that one controlled change affected performance while preserving the learning task. The baseline is the reference implementation; the candidate contains the change; a control helps test whether the proposed cause really explains the observation. An independent trial starts a separate run, rather than taking another sample from the same warmed process. Counterbalanced order alternates which variant runs first so startup or time-dependent effects do not consistently favor one side.

Uncertainty describes how much the evidence leaves unresolved, including run-to-run variation and measurement limits. Model FLOP utilization (MFU) compares an estimate of useful model arithmetic per second with an appropriate hardware ceiling; hardware FLOP utilization (HFU) also accounts for extra executed arithmetic under its stated convention. Neither ratio diagnoses a bottleneck by itself. This capstone connects these definitions to a scoped keep-or-reject decision, not a requirement to produce a speedup.

**Objective** Select one verified training change from profiler evidence and defend keep or reject.

**Prerequisite bridge** Every earlier lesson supplied one causal model and evidence type. The capstone joins them into a complete training decision rather than a collection of unrelated speed tips.

**Why it matters** Tokens per second and MFU summarize outcomes but do not diagnose causes. A credible report preserves failed hypotheses, numerical gates, workload identity, and limitations alongside a successful or rejected change.

**Mechanism** Freeze model/tokenizer/data revision, global valid tokens, sequence distribution, optimizer, precision, and completion boundary. Collect at least three independent baseline runs with step-time, valid tokens/s, phase peaks, memory, and per-rank timing. Use a short profiler window to classify the limiting stage, propose one mechanism-matched change, and run a disconfirming control. Compute MFU only with a disclosed model-FLOP convention and measured useful token rate; distinguish it from hardware FLOP utilization and state that neither identifies the bottleneck. Compare matched DDP/FSDP2 or eager/checkpoint variants only when semantics and work are equal. Publish the keep/reject decision, rejected hypothesis, uncertainty, and next experiment.

Distinguish model FLOP utilization (MFU), based on useful model work, from hardware FLOP utilization (HFU), which can include extra executed work such as recomputation. As a purely illustrative arithmetic example, suppose a step performs 100 trillion useful floating-point operations in 1 second against a matching 200-trillion-operations/second peak: both ratios are 50 percent under this counting convention. Recomputing another 50 trillion operations while taking 1.2 seconds raises HFU to 150/(1.2 × 200) = 62.5 percent, but MFU falls to 100/(1.2 × 200) ≈ 41.7 percent. More hardware work did not produce more useful updates. State the exact FLOP convention, precision, dense/sparse peak, time boundary and measured inputs. These hypothetical numbers are not H100 results.

**Recall** Which metrics describe rate, capacity, correctness, and hardware utilization?

**Mental model** A report connects equivalent-work correctness to step time, tokens/s, memory, communication, and an explicitly bounded MFU estimate.

**Practice labs**

- [Lab 30: Inspect the operators in a tiny training step](reference/labs/30_training_profiler.md)
- [Lab 31: Validate an optimization across complete training updates](reference/labs/31_training_capstone.md)
