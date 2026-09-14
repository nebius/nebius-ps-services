# LLM Training and GPU Optimization on NVIDIA H100

This course teaches how an LLM training step works, how its state consumes memory and communication, and how to optimize it without changing the learning objective or hiding correctness regressions.

## 1. Model learning and training objectives

**Objective**

Distinguish pretraining, continued pretraining, supervised fine-tuning, parameter-efficient tuning, and reward-guided post-training.

**How it works**

### What model training is

A model is a parameterized computation: it takes an input and uses stored numbers, called parameters or weights, to produce an output. Training is the process of changing those numbers using examples and an objective that scores the model's behavior. We do not write a separate rule for every possible answer. Instead, we choose a model structure, a dataset and an update procedure. The resulting behavior is learned from data, so it must be evaluated rather than assumed correct.

A large language model (LLM) works with tokens: pieces of text represented by integer IDs. A token need not be a whole word. A decoder-only language model assigns scores to possible next tokens given a preceding sequence. It can be trained from known examples of which token actually followed a prefix. This course explains that learning process first, then how to make its computation, memory use and communication efficient on H100 GPUs. Basic Python and the two GPU prerequisite courses provide the programming and device foundation.

### Why train, and when not to

Training is useful when a model must learn a predictive relationship or adapt its behavior to a domain or task. LLM pretraining learns broad language patterns from a large corpus, usually starting from random weights. Continued pretraining reuses an existing checkpoint and a similar objective; fine-tuning adapts a pretrained model using selected examples. Not every model trained from scratch is undergoing LLM pretraining. These choices differ greatly in required data, compute and evaluation. They are introduced in the rest of this lesson and explored later.

Training is not the default solution to every application problem. Using an existing model is inference; supplying documents in a prompt changes its context without necessarily changing its parameters. If prompting or retrieving current facts solves the need, changing weights may add cost and risk without benefit. Training requires suitable permitted data, an evaluation set separate from the update data, and checks for overfitting: performing well on examples seen during training but poorly on new ones.

### How one learning step works

Supervised fine-tuning (SFT) trains an existing model on selected input/target examples. Parameter-efficient fine-tuning (PEFT) limits the amount of trainable state; an adapter is an added trainable component attached to a base model. Low-rank adaptation (LoRA) is one PEFT technique: smaller matrix factors represent an update to selected frozen weights. Reward-guided training instead scores generated answers and adjusts the policy, the model's distribution over possible tokens. These terms identify different choices about supervision and trainable parameters, not one universal training procedure. A checkpoint is saved model or training state; later lessons distinguish a model export from a snapshot sufficient for exact resumption.

Take a small batch of examples. The forward pass computes predictions using the current weights. For next-token training, the model produces logits, which are unnormalized scores for vocabulary entries. Softmax converts scores into probabilities. A loss turns the probability assigned to each known target into a scalar penalty; cross-entropy penalizes low probability on the correct token. The backward pass calculates gradients: how sensitive that loss is to small changes in each trainable weight. An optimizer uses those gradients to change the weights. Repeat with more batches, evaluate on held-out data, and save checkpoints so the state can be used or resumed.

The model's forward pass computes predictions and the loss function assesses their error. Backward does not itself update the weights; the optimizer performs the update. Labels are used to assess predictions during training; the answer for the next generated token is not supplied during ordinary inference. During a causal training forward, many known sequence positions can be processed in parallel while the attention mask prevents a position from seeing its future. Generation must instead obtain later tokens from earlier outputs.

The CPU prepares batches and submits operations. GPU kernels perform the large tensor operations in forward, backward and the update. Weights persist between batches; activations are intermediate values, gradients carry the current learning signal, and an optimizer may keep additional history. This is why training memory is more than a model-file size. GPU optimization must preserve the learning task, not merely make a different or incomplete update finish faster.

Mean squared error (MSE) averages squared prediction-minus-target differences. Plain stochastic gradient descent (SGD) updates a weight by subtracting learning_rate × gradient. The learning rate controls the size of each update.

### Execution and dependencies

GPU courses explain how operations execute; training adds the learning contract that decides which values change and why. Every optimization later must preserve this data-to-update lifecycle.

A training procedure connects examples to a parameter update. Text becomes a tokenized batch; the model turns that batch into logits; a scalar objective scores those predictions; backward computes gradients; and the optimizer changes the trainable parameters. A learning-rate scheduler controls the update scale over time. Evaluation and checkpoints then assess and preserve the resulting state.

The training stage changes what enters this loop and what is allowed to change. Pretraining commonly learns next-token prediction from broad corpora while updating the full model. Continued pretraining starts from existing weights and applies a similar objective to another domain or distribution. Supervised fine-tuning uses curated input-response examples. Its loss policy determines whether prompt tokens, response tokens or both contribute; padding is normally excluded.

Parameter-efficient methods change the trainable state rather than defining a separate kind of supervision. They freeze base weights and update smaller adapter components, so they can be combined with SFT. Reward-guided methods instead generate candidate responses, score them and use comparative signals to construct the policy objective. They add generation and scoring to the loop before the update.

To know which procedure a result describes, identify the trainable parameters, the examples and their provenance, the loss denominator, the held-out evaluation split and the checkpoint lineage. Two runs with the same architecture can still be performing different learning tasks if any of these differ.

“Training an LLM” can mean fundamentally different objectives, datasets, trainable state, checkpoints, and evaluation signals. Mixing them makes memory estimates, throughput comparisons, and quality claims meaningless.

### Try a small example

Before a transformer, use the scalar model prediction = weight × input. For inputs 1 and 2 with targets 2 and 4, weight 0 predicts zero. The mean squared error is (4 + 16)/2 = 10 and its gradient is -10. A learning rate of 0.1 makes the next weight 0 - 0.1 × (-10) = 1. The new loss is 2.5. Repetition approaches weight 2; input 3, never used for updates, should then produce approximately 6. This demonstrates the update mechanism, not language understanding.

**Practice labs**

- [Lab 32: Learn a weight and separate training from inference](reference/labs/32_learning_basics.md)
- [Lab 01: Trace a complete tiny-transformer training step](reference/labs/01_tiny_transformer_train.md)
- [Lab 05: Fine-tune a small model with LoRA adapters](reference/labs/05_lora_sft.md)
- [Lab 06: Work through a group-relative policy objective](reference/labs/06_grpo_objective.md)
- [Lab 07: Verify the GRPO generation-to-update loop](reference/labs/07_grpo_trainer.md)

**Mental model**

Data, tokenization, objective, trainable state, evaluation, and checkpoint content change across stages even when the decoder architecture stays fixed.

## 2. Causal training data

**Objective**

Produce correct shifted labels, ignored positions, attention masks, and packed examples.

**How it works**

A training batch groups examples for one computation. Tokens are the model's discrete input symbols, represented by integer IDs; labels specify the answers used to calculate the learning error. In next-token training, each eligible position predicts the following token. Padding fills unused positions so examples fit a rectangular tensor, while truncation discards positions beyond a length limit. A loss mask excludes selected labels from the objective; an attention mask determines which input positions can influence each prediction. They control different things.

Sequence packing places several examples in one tensor row to reduce padding. Position IDs describe positions within the intended sequence, and document boundaries determine whether one example may read another. Packing therefore requires a context policy as well as a storage plan: placing two texts next to each other does not automatically preserve the learning task. Start by tracing which tokens provide context and which labels contribute to loss.

The selected training stage defines which text contributes to learning. A token ID is a vocabulary index; the model will output logits, one unnormalized score per possible next token. Cross-entropy turns those scores and the correct label into a scalar penalty; ignored labels do not contribute to its sum or valid-token denominator. A causal mask prevents a position from attending to future tokens, and a packed-example boundary also prevents it from attending to unrelated examples. A block-diagonal causal mask places one causal region per example along the diagonal of the token-to-token attention matrix. Tokens may read earlier positions within their own region; entries connecting different examples are blocked. Here construct the tensors; Lessons 3 and 4 explain their model and gradient paths.

Text first passes through a tokenizer's vocabulary and normalization rules to become token IDs. The model's embedding table maps those IDs to learned vectors. In causal language modeling, the logits at position `t` predict the next token at `t+1`; the loss must align each prediction with that next-token target rather than the token already supplied at the same position.

A loss mask decides which predictions contribute to the objective. Padding and any excluded prompt positions receive an ignore label and are left out of both the loss sum and the valid-token denominator. An attention mask answers a separate question: which input positions may influence each prediction? Ignoring a label does not stop other positions from attending to its input token.

Packing puts several examples into fewer tensor rows. If those examples must remain independent, their attention regions need boundaries: a block-diagonal causal mask, or explicit packed-sequence metadata understood by the selected backend, blocks cross-example attention. Position IDs must follow the intended positional scheme. Resetting positions alone does not isolate examples unless that backend explicitly converts the resets into attention boundaries.

First-fit packing supplies a concrete storage plan. Process example lengths in order, place each in the first bin with enough capacity, and open a new bin if none fits. With capacity 8 and lengths 5, 3 and 4, lengths 5 and 3 share the first bin; length 4 starts a second. This simple rule need not find the fewest bins. Each stored example span must still receive its own lower-triangular causal region. Lab 25 checks the allocation and mask structure; constructing labels and validating an actual packed learning objective remain separate work.

Example identity, token count, retained or truncated regions, unmasked tokens and packed boundaries connect the batch back to the source data. Licensing, privacy, deduplication and strict train/evaluation separation remain part of the learning contract even when packing makes the storage more efficient.

Padding, prompt masking, truncation, chat templates, and packing change the loss denominator and sometimes which tokens can attend to one another. A faster pipeline with a different batch is not equivalent training.

For tokens [A, B, C], a causal next-token objective pairs the representation after A with label B and the representation after B with label C. It must not train the position after C to predict the first token of an unrelated packed document. An attention boundary prevents reading the other document, while a loss mask excludes invalid prediction targets; those two controls solve different problems.

**Practice labs**

- [Lab 13: Decide which tokens contribute to the training loss](reference/labs/13_loss_masking.md)
- [Lab 25: Pack variable-length examples without crossing boundaries](reference/labs/25_sequence_packing.md)

**Mental model**

A causal model predicts the next token. Padding and prompt regions may be excluded from loss; packing combines examples while preventing attention and labels from crossing example boundaries.

## 3. Decoder architecture and information flow

**Objective**

Follow embeddings, normalization, attention, residual paths, MLP, and logits with shapes.

**How it works**

A decoder-only transformer is a neural-network architecture that builds a representation for each token using the context it is allowed to read, then scores possible next tokens. An embedding turns a token ID into a vector. Each transformer block combines attention, a feed-forward network and residual connections. Attention mixes information from permitted positions; a head is one attention component with its own learned projections. A projection is a learned linear transformation. The feed-forward network, often called a multilayer perceptron (MLP), transforms each position's vector through additional learned operations. An activation function introduces a nonlinear change: its output cannot be expressed as just a weighted sum plus bias. Placing it between learned linear operations prevents the MLP from collapsing into a single matrix-and-bias operation. An activation function is the transformation; activations are the values produced by the network.

Activations are intermediate values produced during a forward pass. Normalization rescales them using statistics of the vector, and residual addition adds an earlier representation to a transformed one. Attention creates query, key and value vectors through learned projections: queries seek relevant context, keys supply information for matching, and values supply the information mixed into the result. Rotary positional embeddings (RoPE) encode relative position through rotations of query/key components. The final projection produces logits—unnormalized vocabulary scores. This architecture description explains the stages before the lesson follows their tensor shapes and memory costs.

Batches contain IDs, masks, positions, and labels. The transformer maps those tensors into logits while retaining intermediate activations needed by backward.

### From token IDs to attention inputs

Let `B` be batch size, `S` sequence length, `H` hidden width and `V` vocabulary size. Input IDs have shape `[B,S]`. Looking them up in the embedding table produces `[B,S,H]`: one H-value vector for each token position. Positional information tells the model where that vector occurs in the sequence.

The concrete decoder example uses learned positional embeddings, added to token embeddings. Its LayerNorm subtracts a vector's mean, divides by `sqrt(variance + epsilon)`, and applies a learned scale and offset. Other architectures make different choices: RoPE rotates query/key components rather than adding a learned position vector. The walkthrough must follow the actual architecture instead of treating these methods as interchangeable.

### Mix information from allowed positions

From normalized hidden values, learned projections form queries Q, keys K and values V. Here V names the value tensor, not the vocabulary size above. Here, `heads` is the number of attention heads and `head_dim` is the number of values in each head's vector. These tensors are commonly arranged as `[B,heads,S,head_dim]`. A query describes what a position seeks, a key contributes a matching score, and a value supplies content to combine.

For one head, scaled dot-product attention is `softmax(Q @ K.T / sqrt(head_dim) + mask) @ V`. Query-key products give scores; scaling controls their magnitude; the mask excludes forbidden positions; softmax converts allowed scores into weights; and those weights mix the value vectors. In square causal attention, each position can use itself and earlier positions. A fused implementation can perform this calculation without storing the entire score matrix. PyTorch's `scaled_dot_product_attention` expresses the operation and selects a supported implementation. Grouped-query attention (GQA) uses fewer K/V heads shared across more query heads.

### Return to the hidden representation and produce logits

The attention output projection returns to width H so it can be added to the residual path. The MLP expands the hidden dimension, applies a nonlinear activation and contracts it again before another residual addition. This example uses Gaussian error linear unit (GELU), `x*Phi(x)`, where Phi is the standard normal cumulative probability. Final normalization and a vocabulary projection produce logits `[B,S,V]`.

A logit is a score, not a probability. Softmax exponentiates the scores and divides by their sum, producing nonnegative probabilities that sum to one. Cross-entropy for a known next token is the negative natural logarithm of its probability, so assigning that token more probability lowers the loss.

Backward traces how that loss depends on each trainable parameter and computes its gradient. Simple gradient descent subtracts learning rate times gradient. AdamW instead uses running averages of gradients and squared gradients to adapt the update, with decoupled weight decay separately shrinking parameters toward zero. It does not mix that decay into the gradient used for the adaptive calculation.

Shape reasoning reveals compute, activation memory, communication, and kernel efficiency. It also separates parameters that persist across steps from activations whose size grows with microbatch and sequence length.

**Practice labs**

- [Lab 01: Trace a complete tiny-transformer training step](reference/labs/01_tiny_transformer_train.md)

**Mental model**

Token IDs select embeddings; repeated decoder blocks transform `[batch, sequence, hidden]`; the language-model head maps hidden states to vocabulary logits.

## 4. The parameter-update lifecycle

**Objective**

Order forward, loss, backward, gradient handling, optimizer update, and zeroing correctly.

**How it works**

A training step performs a parameter update. The forward pass computes predictions from current parameters; the loss turns prediction error into a scalar objective; backward computes gradients describing sensitivity of that loss to parameters; the optimizer then changes the parameters using those gradients and its update rule. Backward does not itself update the weights. PyTorch autograd is the automatic-differentiation system that records supported operations and applies their derivative rules.

A microbatch is a smaller group processed within a larger intended update. Several microbatches may contribute before the optimizer runs, provided their losses are normalized consistently. Gradient clipping limits the gradient's magnitude, while a learning-rate scheduler changes the update scale over training. These operations have an order because later stages consume earlier results. The distributed averaging and precision-scaler cases below are extensions of this basic loop, not prerequisites for understanding why one weight changes.

The decoder produces logits and the batch supplies valid labels. Training converts their scalar loss into parameter updates through autograd and optimizer state.

### Build gradients, then update parameters

Forward computes logits from the current parameters, and the loss combines errors over valid labels. Backward differentiates that scalar and accumulates gradients into parameter buffers. The optimizer consumes those gradients to change the weights. Clearing gradients after the update, or before the next intended backward, prevents an earlier step from unintentionally contributing to the next one.

Lab 01 runs forward and loss inside CUDA bfloat16 (BF16) autocast, then exits the context before backward. Autocast selects computation formats for supported operations; it does not convert every stored parameter to BF16. This BF16 recipe does not use 16-bit floating point (FP16) loss scaling. When a separate FP16 recipe does use scaling, gradients must be unscaled before their true magnitudes are checked or clipped.

### Control gradient magnitude at the correct point

Gradient-norm clipping acts on the combined parameter-gradient vector before the optimizer update. A gradient `[3,4]` has Euclidean (L2) norm 5. A limit of 1 scales it to `[0.6,0.8]`, preserving direction while reducing magnitude. `clip_grad_norm_` modifies the gradients and reports their pre-clipping norm. Non-finite gradients must be rejected before updating. Clipping bounds the gradient, not necessarily the adaptive optimizer's parameter-update norm.

The optimizer may also maintain persistent state. AdamW keeps first and second moments; a separate higher-precision master-weight copy depends on the mixed-precision implementation and is not an automatic extra allocation in every recipe. The learning-rate scheduler advances according to its declared unit, such as optimizer updates. These states belong in memory and restart accounting. Lab 01's optional save is only a partial snapshot; Lesson 5 explains complete resume state.

### Keep the same objective across microbatches and ranks

Several microbatches can contribute before one update. With unequal valid-token counts, averaging their mean losses equally gives short microbatches too much weight. Instead, combine loss sums and valid-token counts, or weight each contribution so the accumulated gradient matches the intended reference batch.

The same issue occurs across ranks. Under ordinary DistributedDataParallel (DDP) averaging over R ranks, each rank can backpropagate **R × local_loss_sum / global_valid_tokens**. DDP's later division by R cancels that factor, leaving the global token-weighted gradient. Count valid tokens across all ranks and the **entire intended accumulation window**. With two ranks holding 100 and 300 valid labels, this gives the concatenated 400-token objective; averaging their two local means does not.

DDP's `no_sync` can suppress intermediate communication while microbatches accumulate, with synchronization at the final microbatch. It does not choose the correct loss denominator for the program. This derivation assumes default averaging, not a custom hook or rank-joining policy with different semantics. It is a preview for Lesson 11; the first single-GPU pass needs only the forward, loss, backward and update sequence.

A loop can run quickly while applying the wrong gradient scale, clipping at the wrong time, accumulating stale gradients, or stepping its scheduler on the wrong cadence. Performance evidence is invalid until update semantics are tested.

**Practice labs**

- [Lab 01: Trace a complete tiny-transformer training step](reference/labs/01_tiny_transformer_train.md)
- [Lab 02: Trade microbatch size for peak memory](reference/labs/02_gradient_accumulation.md)
- [Lab 13: Decide which tokens contribute to the training loss](reference/labs/13_loss_masking.md)
- [Lab 25: Pack variable-length examples without crossing boundaries](reference/labs/25_sequence_packing.md)

**Mental model**

Autograd records the forward graph, backward accumulates gradients into parameter buffers, and the optimizer updates parameters from those buffers and its state.

## 5. Training evaluation and recovery

**Objective**

Save every state needed to reproduce the next training step after interruption.

**How it works**

Evaluation measures a model's behavior on examples not used for the current parameter updates. A checkpoint is a saved snapshot of training state; resuming means restoring enough state to perform the intended next update. Saving model weights alone can support inference or a new fine-tuning run, but does not necessarily reproduce a paused training run. Optimizer history, learning-rate position and random choices can affect what happens next.

A random-number generator (RNG) produces reproducible pseudorandom sequences from its state. An epoch is a pass through a dataset; a sampler chooses examples and their order; a data cursor records how far consumption has progressed. Atomic checkpoint publication makes a completed snapshot become visible as a unit rather than exposing a partly written file. This saved restart checkpoint differs from activation checkpointing, which later recomputes intermediate values during backward to save GPU memory.

A correct step is a state transition. Exact resume means restoring the complete pre-transition state and consuming the same next data, not merely loading similar weights.

A resumable checkpoint captures the state just before a well-defined next transition. The model weights are only one part. The optimizer's moments and step counts affect the next update; the scheduler's position affects its scale; and a precision scaler or 8-bit floating point (FP8) recipe can affect the arithmetic. Adapters and their configuration must also match the base model.

The next batch is state too. CPU and accelerator RNG states affect random choices, while the distributed sampler epoch and data cursor determine which examples come next. If a checkpoint is taken inside an accumulation window, the accumulated gradients and current microbatch position must be retained. Version, configuration and artifact identity establish how these values are interpreted.

Saving should expose only a complete snapshot. Data is written to a temporary private location, participating ranks coordinate at the declared boundary, and completeness is checked before the snapshot is made visible atomically. A partially written file or a mix of states from different updates cannot define a valid restart.

Restoration is tested by continuing both an uninterrupted run and a restored run from the same boundary with the same next data. A deterministic supported recipe may require bitwise equality; a nondeterministic kernel path needs an explicit tolerance-based continuation claim. Merely loading the file proves neither. Evaluation separately uses held-out data and weights loss by valid tokens so unequal batch lengths do not distort the reported mean.

Missing optimizer, scheduler, scaler, RNG, sampler, or partial-accumulation state can silently fork the run. A checkpoint that loads successfully may still fail continuation equivalence.

**Practice labs**

- [Lab 24: Reproduce the next update after checkpoint restoration](reference/labs/24_checkpoint_resume.md)

**Mental model**

Exact resume may require model, optimizer, scheduler, scaler, RNG, data position, sampler, and accumulation state. Evaluation uses held-out data and must not update training state.

## 6. Training memory and state lifetimes

**Objective**

Account for weights, gradients, optimizer state, activations, temporaries, communication buffers, and allocator reserve.

**How it works**

A training memory ledger accounts for the values that must exist at each stage and for how long they are needed. Persistent state survives between steps, including parameters and optimizer history. Activations are intermediate forward values, some of which backward needs. Temporary state, such as an operation's workspace, may exist only while that operation runs. Peak memory is the greatest simultaneous requirement, not the sum of every allocation made over the entire run.

Allocated memory is storage currently held by tensors; reserved memory also includes blocks retained by the allocator for reuse. Some mixed-precision recipes keep wider master weights for updates alongside lower-precision computation values. Offload moves selected state to another storage tier, usually host memory, and introduces transfer costs. Distributed buffers add another lifetime to the ledger. Their detailed sharding rules come later; first identify which values must coexist even for one ordinary training step.

The correct update and resume lessons established model parameters, gradients, optimizer buffers and saved activations. A phase-aware ledger now places persistent and temporary state in forward, backward, optimizer and checkpoint time; distributed replication/sharding will build on this ledger later.

Start with state that survives between updates. Parameter count multiplied by bytes per stored value gives parameter storage. Apply the same reasoning to gradients and each optimizer moment, using their actual dtypes. Count a separate master-weight copy only when the chosen implementation maintains one; do not assume every mixed-precision optimizer does.

Forward adds activations and temporary workspaces. Some activations remain until backward uses them; others can be discarded or recomputed. Their sizes depend on the layer, microbatch, sequence length and format. Attention and MLP temporaries can create a peak even when persistent state is modest.

Distributed or specialized execution adds more intervals: Fully Sharded Data Parallel (FSDP) all-gather buffers, DistributedDataParallel (DDP) buckets, 8-bit floating point (FP8) metadata and graph pools. Place each allocation on a timeline from creation to last use. The forward, backward and optimizer peaks are the totals that coexist at those moments, not the sum of everything allocated during the step.

Allocator accounting overlaps this tensor ledger. Reserved bytes already include active allocations plus capacity retained for reuse. Add only additional unused reservation or other uncounted overhead when reconciling against reserved memory; adding the entire reserved total to live tensor bytes double-counts them. Snapshots and peak APIs help compare calculated, measured and still-unknown quantities.

Checkpoint loading has a separate peak: model files, optimizer shards and temporary load or staging buffers can coexist differently from training. A model-file size therefore cannot stand in for either the training peak or the load-time peak.

Checkpoint file size is not live training footprint. Optimizer moments, master weights, saved activations, temporary workspaces, gathers, reduction buckets, graph pools, and allocator headroom can exceed visible parameter bytes.

As a deliberately simplified ledger, one million parameters stored in 32-bit floating point (FP32) need 4 million bytes. An equally sized FP32 gradient needs another 4 million bytes, and two FP32 optimizer moment arrays add 8 million, giving 16 million bytes before activations, temporary buffers or allocator overhead. Changing the parameter dtype alone does not halve all four terms. Master weights, sharding and the actual optimizer recipe can change this accounting, so enumerate the state that the implementation really retains.

High-bandwidth memory (HBM) is the GPU’s device memory. This ledger concerns live storage there; moving or sharding state changes ownership and communication costs rather than making the state disappear.

**Practice labs**

- [Lab 21: Validate precision changes across a full training update](reference/labs/21_mixed_precision_training.md)

**Mental model**

Persistent model/optimizer states scale with parameters; activations scale with batch and sequence; temporaries and collectives create phase-specific peaks. Activation or optimizer offload trades HBM for host-memory capacity and transfer latency, so it is a capacity escape hatch rather than a default speed optimization.

## 7. Numerical precision in training

**Objective**

Select state-specific precision and verify loss, gradients, updates, and kernels.

**How it works**

Mixed precision uses different numerical formats for different parts of a training calculation. FP32 means 32-bit floating point, FP16 means 16-bit floating point, BF16 means bfloat16, and FP8 denotes an 8-bit floating-point format. Range describes the largest and smallest magnitudes represented, while precision describes the spacing between representable values. A smaller format can reduce storage or enable faster supported arithmetic, but can also alter losses, gradients and updates.

Autocast selects operation-appropriate formats within a region; it does not simply convert all model state to one dtype. Loss scaling multiplies the loss before backward and later unscales gradients to help small gradients survive limited range. NVIDIA Transformer Engine is a library for supported low-precision transformer operations and scaling state. An amax value is an observed maximum absolute magnitude, and a recipe defines how such measurements select scales and formats. These mechanisms must be checked across the complete update, not only its forward output.

The memory ledger shows where bytes live; mixed precision chooses formats for individual states and operations without changing the mathematical training objective.

### Choose formats for operations and state separately

Mixed precision changes selected representations along the update path. TensorFloat-32 (TF32) permits particular reduced-precision matrix arithmetic on FP32 inputs without changing their storage type. BF16 retains an FP32-like exponent range with fewer fraction bits. FP16 has a narrower range, so small gradients can disappear or large values can overflow more readily. Normalization, reductions, accumulation and optimizer state may need suitable higher precision even when matrix inputs use a smaller format.

Loss scaling helps FP16 gradients survive that range. Multiply the loss before backward, compute scaled gradients, then unscale them before finite checks, clipping and the optimizer update. Scaling does not create more precision; it changes magnitudes during the vulnerable calculation. BF16 often needs no such scaling, but its coarser precision still requires checking the update.

### Follow the FP8 recipe

Transformer Engine's `te.autocast` applies a selected low-precision recipe to supported forward modules. Backward uses the corresponding precision context recorded by forward. A scale maps a tensor's values into a representable range. Delayed scaling uses prior maximum-absolute-value (amax) history; current or block recipes choose scales using different data and support requirements. Warmed recipe state and its history can therefore affect results as well as speed.

FP8 is a family of encodings. E4M3 uses four exponent bits and three fraction bits; E5M2 uses five and two. The exponent controls range and the fraction controls precision within that range. Both also have a sign bit, and their special-value rules are format-specific. E4M3 retains more fraction precision, while E5M2 offers greater exponent range. A hybrid recipe can use different encodings for forward tensors and gradients. Block scaling, outlier handling and higher-precision operations complete the recipe; saying “FP8” alone does not describe the calculation.

Stochastic rounding probabilistically selects adjacent representable values to reduce systematic rounding bias over repeated conversions. It cannot guarantee an individual value's accuracy or recover a saturated value. NVIDIA NVFP4 recipes can combine rounding and transforms, but those Blackwell-specific training paths are comparison-only here, not H100 lab capabilities.

### Check the resulting update

Matching initial parameters and inputs allow loss, gradients and parameter changes to be compared across recipes. Relative Euclidean (L2) error divides the candidate-minus-reference vector magnitude by the reference magnitude; it describes aggregate discrepancy rather than the worst element. Finite checks, scaling/amax behavior and selected kernels explain what happened along the path. A short correct update test can detect a broken recipe, but cannot establish training convergence.

“FP8 training” does not mean every tensor is FP8. Unsupported shapes, cast/scaling overhead, overflow/underflow, cold scaling history, or a changed optimizer can erase performance or learning equivalence.

FP8 weight caching retains a quantized copy of unchanged weights across microbatches in an accumulation window, avoiding repeated conversions. Fused weight-gradient accumulation writes matrix-product gradients directly into an accumulation buffer, combining gradient production and addition instead of requiring a separate temporary gradient and addition step. These techniques can remove conversions or launches only for supported recipes, modules and shapes. Weight caching still needs numerical checks because changing scale history can make cached and freshly converted values differ.

**Practice labs**

- [Lab 21: Validate precision changes across a full training update](reference/labs/21_mixed_precision_training.md)
- [Lab 22: Qualify a warmed Transformer Engine FP8 recipe](reference/labs/22_transformer_engine_fp8.md)

**Mental model**

Choose a representation for each kind of computation and state. Scaling protects useful numerical range, while accumulation and sensitive operations may need greater precision; the full training result decides whether the recipe is acceptable.

## 8. Memory savings through accumulation and recomputation

**Objective**

Compare microbatching and selective/full activation recomputation at fixed effective tokens.

**How it works**

Gradient accumulation and activation checkpointing are two different ways to change a training step's memory needs. Accumulation processes smaller microbatches, combines their correctly weighted gradients and performs one optimizer update for the intended effective batch. It saves memory by not holding every example's activations at once. The global batch counts the work contributing to that update across microbatches and, where used, ranks; variable valid-token counts require token-aware weighting.

Activation checkpointing retains selected forward values and recomputes missing intermediates when backward needs them. It trades extra computation for fewer saved activations. Selective recomputation repeats chosen operations or regions; full-block recomputation repeats a larger region. This is not saving a restart checkpoint to disk. The two techniques can be combined, but each changes a different part of the step, so measure memory, total work and update equivalence separately.

The training step defines effective tokens and update cadence; the memory ledger identifies saved activations that dominate peak capacity. Accumulation and checkpointing change different parts of that ledger.

Accumulation divides one intended update into smaller microbatches. Each forward/backward pair needs activations for only its microbatch, while gradients accumulate until the optimizer runs once. Global valid tokens per update must remain fixed, and unequal microbatches need token-weighted contributions. DistributedDataParallel (DDP) may use `no_sync` for intermediate microbatches, but that controls communication rather than objective weighting.

Activation checkpointing changes what is saved within each forward. It retains selected values and omits other intermediates that backward will need. During backward, those missing values are produced again by repeating part of forward. Selective recomputation targets intermediates that are large to store and relatively cheap to recreate; full-layer checkpointing is simpler but can repeat expensive attention and MLP work.

Here reentrant refers to invoking a nested differentiation execution during backward. PyTorch's non-reentrant implementation records the autograd dependency graph during forward while omitting selected saved tensors. Backward recreates the tensors needed by that graph; with early stopping enabled, recomputation stops once those intermediates exist. The qualified path selects it explicitly with `use_reentrant=False`.

Re-execution must preserve semantics. Random operations, stateful modules, autocast/8-bit floating point (FP8) state and side effects can behave differently when run again unless the chosen implementation handles them correctly. The resulting trade includes bytes no longer retained, added floating-point operations (FLOPs) or kernels, peak memory, step-time distribution and valid tokens/s. Numerical update equivalence establishes whether the smaller-memory execution still performs the same learning task.

Accumulation reduces per-microbatch activations but does not remove parameter/optimizer state. Checkpointing removes selected saved activations but executes forward work again during backward. Comparing different effective batches hides the real exchange.

**Practice labs**

- [Lab 02: Trade microbatch size for peak memory](reference/labs/02_gradient_accumulation.md)
- [Lab 14: Compare checkpointing on alternate blocks and all blocks](reference/labs/14_activation_checkpointing.md)

**Mental model**

Accumulation reduces per-microbatch activation size but adds launches and delays updates; checkpointing discards selected activations and recomputes them during backward.

## 9. Training input readiness

**Objective**

Keep training data ready without changing sample order or semantics.

**How it works**

The training input pipeline turns stored examples into GPU-ready batches. It reads and transforms examples, assembles them through collation, and transfers the resulting tensors from host to device (H2D). Workers are processes or threads preparing data. Prefetch means preparing future batches before the trainer requests them; queue depth counts prepared batches waiting for consumption. Starvation occurs when the GPU has reached its next step but the needed batch is not ready.

Pinned host memory supports suitable asynchronous transfers, and nonblocking submission allows the CPU to continue before a copy finishes. Neither removes the need for correct buffer lifetime and dependencies. More workers can prepare faster or compete for the same storage and CPU resources. In training, sample identity and order are part of the workload: feeding different examples or dropping slow ones is not a valid optimization of the same learning task.

Accumulation and recomputation optimize the GPU step only when a valid next batch is available. The input pipeline is a distributed producer system whose correctness includes exact sample ownership.

A dataset maps an index to sample content. Workers read, decompress, parse and tokenize that content; packing and collation assemble the selected examples into tensors; pinning and H2D copies make those tensors available to the GPU. A delay at any producer stage can leave the trainer waiting for its next batch.

Prefetch lets workers prepare future batches while the current batch runs. It can absorb variation, but its queue consumes memory and eventually empties if production is consistently slower than consumption. Persistent workers avoid restarting their processes. Offline tokenization removes repeated CPU work but creates a prepared artifact tied to the tokenizer, configuration and data revision.

Training correctness follows the examples through these changes. Worker seeds, distributed sampler ownership and epoch updates must preserve the intended global sample IDs, order, content and unmasked-token counts. Dropping a slow example or giving two ranks the same sample changes the task even if the device becomes busier.

Ready-time distributions and queue depth beside GPU steps reveal where waiting occurs. Synthetic inputs remove the real source path; resident inputs remove preparation and transfer as well. These controls distinguish loader starvation from later delays. On shared storage, adding workers may increase contention instead of readiness, so per-node behavior matters. Pinned source batches must remain valid and unchanged until their nonblocking copies complete.

Loader starvation creates idle gaps; careless tuning can duplicate, omit, reorder, or retokenize data differently. Throughput gained by changing examples is not an optimization.

**Practice labs**

- [Lab 26: Diagnose a slow training-data producer](reference/labs/26_input_pipeline.md)

**Mental model**

Storage, tokenization, collation, packing, pinning, and transfer form a staged producer. Queue depth and worker count trade latency hiding against memory and contention.

## 10. Training execution optimization

**Objective**

Reduce launch and intermediate traffic while respecting autograd and optimizer semantics.

**How it works**

Fusion combines operations so a program may launch fewer kernels and avoid writing some intermediate tensors. Compilation transforms a program region into executable implementations. A CUDA Graph represents GPU operations and their dependencies as a reusable execution plan; capture records compatible work, and replay submits that plan again with less repeated host setup. Replay is not a saved numerical result and does not itself fuse kernels. These are related but distinct techniques.

For training, the replayed work can change parameter, gradient and optimizer-state values while using retained storage. A graph memory pool manages allocations associated with captured work. Compiler guards check assumptions such as shape; a graph break ends a compiler-captured region and is not the same thing as a failed CUDA Graph capture. A profiler records execution activity, and NVIDIA Tools Extension (NVTX) annotations name regions in a trace. The lesson applies these tools to the complete update rather than assuming an optimized forward pass guarantees faster training.

A correct update, explicit memory/precision policy and a supplied next batch establish the single-GPU baseline. Fusion, compilation and graph replay now target local launch gaps and intermediates before distributed placement and communication are introduced.

A training update contains more than forward kernels. Input preparation, forward, loss, backward, optimizer work and communication each create dependencies. A short profiler window with named NVTX ranges connects those phases to repeated pointwise chains, intermediate allocations and CPU submission gaps.

Fusion can combine compatible operations and avoid writing some intermediates. Maintained fused operators or a qualified `torch.compile` region may provide that change, but guards and graph breaks limit which operations the compiler can transform. The generated forward and backward kernels, remaining allocations and gradient results reveal whether the intended fusion happened without changing the update.

CUDA Graph replay addresses repeated submission of stable work. After warm-up, capture records operations whose addresses, control flow and dependencies remain compatible across replays. Parameters and optimizer state can change values in their retained storage. Graph pools retain memory, and a bounded set of captures can handle selected microbatch or sequence-shape buckets. Unsupported cases need an explicitly supported fallback.

These transformations must preserve random number generator (RNG) advancement, gradient zeroing, scaler or 8-bit floating point (FP8) state and collective ordering. A faster forward fragment does not establish a faster complete update. Compilation, capture and steady replay have separate time and memory costs, and backward/update equivalence must be checked alongside the final step time.

Training graphs include autograd, mutation, RNG, optimizer state, dynamic shapes, and collective boundaries. A transformation that works for an inference fragment can produce graph breaks or incorrect gradients in training.

**Practice labs**

- [Lab 27: Separate compiled expressions from captured training steps](reference/labs/27_fused_graph_trace.md)
- [Lab 30: Inspect the operators in a tiny training step](reference/labs/30_training_profiler.md)

**Mental model**

Compilation can fuse forward/backward operators; CUDA Graphs can replay stable step regions. Dynamic shapes, optimizer branches, and allocation changes require buckets or fallbacks.

## 11. Distributed training-state ownership

**Objective**

Compare replicated and sharded training state on two one-GPU nodes.

**How it works**

Distributed Data Parallel (DDP) keeps a complete model replica on each participating process. The input pipeline supplies each process its assigned data; DDP combines gradients so updates stay consistent. A rank is a process identifier; a replica is a complete copy; a shard is one piece of a larger tensor or state. Fully Sharded Data Parallel version 2 (FSDP2) divides parameters and associated training state across ranks and gathers needed parameter pieces for computation. It targets state capacity as well as distributed execution.

A collective involves a group of ranks: all-reduce combines values and returns the result to all; all-gather assembles their pieces; reduce-scatter combines values but returns a different reduced piece to each rank. ZeRO, the Zero Redundancy Optimizer family, describes related stages of training-state sharding. These terms explain where state lives and how it moves; they do not imply that adding ranks always reduces step time.

The memory ledger divides model, gradient, and optimizer state; distributed data parallelism decides which ranks own copies or shards and which collectives reconstruct a consistent update.

Begin with one training rank and its state. In DDP, adding ranks creates complete model replicas with their own optimizer state. The application assigns each rank its input; DDP does not split a batch automatically. Each replica computes local gradients, then ordinary DDP all-reduces and averages them so every replica can apply the same update.

All-reduce returns the complete reduced gradient to every rank. Reduce-scatter alone instead returns one reduced shard per rank, which is insufficient for an ordinary optimizer that owns the full replicated model. An all-reduce may internally use reduce-scatter followed by all-gather, but its externally visible result remains a full gradient on every rank.

FSDP2 changes ownership. It shards selected parameters, gradients and optimizer state. Before computation needs a full parameter group, all-gather assembles its pieces; after gradients are computed, reduce-scatter returns the reduced pieces to their owners. This reduces persistent per-rank state while introducing temporary gathered parameters and communication. Its mixed-precision policy can also distinguish parameter-computation and gradient-reduction formats.

ZeRO-style stages describe progressively sharding optimizer state, gradients and parameters. Actual implementations differ in when they gather or retain tensors, so the persistent ledger alone cannot predict peak memory or step time. Global non-padding tokens, sample ownership, loss reduction and optimizer semantics must remain the same in a comparison. Transient buffers, bucket overlap, the slowest-rank step and checkpoint/restart behavior describe the resulting execution.

DDP and FSDP2 solve different constraints. A small two-rank job may run faster with replication, while a larger model may require sharding merely to fit.

Megatron Core's distributed optimizer separately shards optimizer state and coordinates reduce-scatter/update/all-gather, but this two-rank PyTorch course does not qualify its production implementation.

**Practice labs**

- [Lab 00: Verify the distributed training allocation](reference/labs/00_cluster_preflight.md)
- [Lab 03: Train replicated models with DDP](reference/labs/03_ddp_train.md)
- [Lab 04: Observe FSDP2 sharded training state](reference/labs/04_fsdp2_train.md)

**Mental model**

Replication gives each rank full persistent state; sharding assigns persistent pieces to different owners. Communication supplies the temporary full values or combined gradients needed at each computation boundary.

## 12. Model partitioning and communication

**Objective**

Map tensor, pipeline, context, and expert parallelism to partitioned state and communication.

**How it works**

Model parallelism divides one model's work when replication alone is not the desired layout. Tensor parallelism (TP) splits tensors and parts of an operation across ranks. Pipeline parallelism (PP) puts groups of layers on different stages and passes intermediate activations between them. Context parallelism (CP) partitions sequence context and coordinates the attention information needed across those partitions. Expert parallelism (EP) distributes the experts in a mixture-of-experts (MoE) layer: a router selects which expert networks process each token.

A pipeline bubble is idle time while a stage waits for work or results. All-to-all communication exchanges different pieces between ranks, often for expert routing. Grouped matrix multiplication, also called grouped general matrix multiplication (GEMM), executes several matrix problems together. A capacity factor sets an expert's token-capacity allowance relative to a stated average. Sequence parallelism can instead partition particular non-attention activations or operations within another parallel layout; it is not a universal synonym for CP. Each scheme changes a different ownership boundary.

DistributedDataParallel (DDP) and Fully Sharded Data Parallel (FSDP) partition data and training state. Model parallel strategies partition computation or model structure when one rank cannot efficiently own the full layer, sequence, pipeline, or expert set.

A parallel layout first assigns ownership: which rank holds a tensor, computes a layer or processes an expert's tokens? Communication then supplies the values that computation needs from other owners. The useful distinction is what crosses that boundary and when its consumer can proceed.

### Partition one matrix operation

Tensor parallelism divides an operation's dimensions. Each rank computes its assigned output or a partial contribution, then an all-gather, all-reduce or reduce-scatter forms the representation needed next. Sequence parallelism can shard compatible activation work alongside TP.

Use matrix algebra to derive a partition before launching it. For `Y = X @ W`, splitting W into output-column shards gives each rank different columns of Y; all-gather concatenates them in the declared order. Splitting X's features and matching rows of W instead gives partial products whose sum is Y, so all-reduce reconstructs it. If G is the output gradient, the full derivatives are `dW = X.T @ G` and `dX = G @ W.T`. A column shard produces a weight-gradient shard and one contribution to dX; sum those input-gradient contributions. Normalize each partial loss by the global output-element count so summing contributions preserves the original mean objective. Validate reconstructed outputs, gradients and one update; retained full references must stay in the memory accounting.

### Pass activations and gradients between stages

Pipeline parallelism assigns groups of layers to stages. Microbatches move forward through those stages and their gradients travel back, creating fill and drain bubbles when some stages have no ready work. Context parallelism instead partitions sequence context and communicates the K/V or attention state needed across partitions.

Point-to-point communication sends a tensor from one rank to another instead of involving the whole group. In a pipeline, the receiving rank starts its local differentiation from that activation and sends its activation gradient back. A plain send/receive is not a distributed autograd graph: the sender must apply the returned gradient to its retained local activation. In the context mechanics example, sum local squared values and divide by the full context's element count to preserve the global mean; gathering context shards checks reconstruction, not distributed attention.

### Route tokens to their expert owners

Expert parallelism places MoE experts on ranks. Grouped GEMM can batch several local expert operations. Router balance, capacity factors, dropped or padded tokens and slow experts affect how much useful work each rank completes.

Expert routing groups tokens by owner, exchanges those groups with all-to-all, applies each owner's expert, then reverses the routing to restore input order. The training path must also return token gradients and update expert weights. Lab 12 explicitly carries these values across communication boundaries rather than assuming communication builds an autograd connection. Batched matrix multiplication (`bmm`) computes corresponding matrix products in a batch; padding unequal expert groups to a common size permits that comparison but performs extra work. Keep padding costs and the actual grouped implementation visible.

### Make completion and correctness a group decision

A distributed acceptance decision must reach the same verdict on every rank. Lab 19 converts any non-finite tensor, norm or relative error into a failing infinite error, then reduces the integer pass flags with MIN across all ranks. One failed rank therefore prevents publication everywhere. This infinite error is a rejection marker, not a valid measurement. Keep ranks participating through the verdict collective rather than raising on just one rank before its peers reach that operation.

The same parallel degree can produce very different communication, bubbles, memory, and load balance. A topology choice must begin from what is sharded and when consumers need it.

A virtual pipeline stage is a logical chunk of layers; one physical rank can own several chunks and interleave their microbatch work. An asymmetric layout assigns different numbers or types of layers to physical stages to balance their costs. These choices change assignment and scheduling, not the model's required layer order. Each strategy targets a different capacity, bubble, or scaling constraint.

For a context-parallel attention example, split a four-token sequence so one rank owns positions 1–2 and another owns 3–4. The query at position 4 still needs allowed keys and values from all four positions; the query at position 2 must not see later positions. Exchanging key/value blocks lets each owner accumulate its local queries' attention contributions while maintaining the causal mask. The normalization must include all allowed keys, not a separate softmax average per rank. Backward must also return gradient contributions to the owners of those keys and values. This explains the communication dependency; the scalar context-partition lab only checks ownership, reconstruction and global normalization, not a distributed attention implementation.

**Practice labs**

- [Lab 12: Trace expert routing, gradients, and grouped work](reference/labs/12_moe_expert_parallel.md)
- [Lab 19: Partition a linear layer and verify its backward pass](reference/labs/19_tensor_parallel_linear.md)
- [Lab 29: Follow pipeline and context partitions through backward](reference/labs/29_parallelism_mechanics.md)

**Mental model**

Partitioning changes who owns tensors, layers, context or experts. The model's dependencies remain, so each consumer must receive the missing values and backward must return the corresponding gradients.

## 13. Gradient readiness and communication overlap

**Objective**

Measure exposed communication rather than total collective duration.

**How it works**

Communication overlap means a transfer or collective progresses while independent useful computation also runs. In training, backward produces gradients at different times, so an already-ready group can communicate while later gradients are still being computed. A gradient bucket is a group of gradient values communicated together. Readiness means all inputs needed by that communication are available. A dependency says one operation must wait for another; the critical path is the chain of dependencies that determines completion time.

Exposed communication is the portion that still delays completion after available overlap. Returning from an asynchronous application programming interface (API) call does not prove that the network and useful arithmetic overlapped. NVIDIA Scalable Hierarchical Aggregation and Reduction Protocol (SHARP) is network-assisted collective processing on supported infrastructure, not a feature supplied merely by an H100 GPU. This course first teaches software scheduling and observed overlap on the actual two-node path.

Each parallel strategy creates a collective dependency. Overlap is possible only after a chunk becomes ready and before a consumer needs its result.

### Start communication when its inputs are ready

Backward produces different gradients at different times. DistributedDataParallel (DDP) assigns gradients to buckets; a bucket can communicate when every gradient it needs is ready. Its collective can then progress while independent backward work computes other gradients. The optimizer must wait for all reduced gradients it consumes, so an unfinished collective at that point still extends the step.

Smaller buckets may become ready earlier but create more calls and fixed latency. Larger buckets amortize that overhead but start later, leaving less independent computation available to overlap their transfer. Parameter sizes, readiness order and topology determine the trade-off. A rank-aligned timeline connects readiness, launch, transfer, completion and the optimizer wait; a fast-returning API call cannot establish overlap.

Other layouts expose different opportunities. Fully Sharded Data Parallel (FSDP) can schedule gradient reduce-scatter or parameter all-gather alongside suitable independent work. TP collectives, EP all-to-all and PP point-to-point communication each have their own producer and consumer dependencies. Rank arrival skew and contention matter because communication and computation can compete for memory or fabric resources.

### Change the communication representation deliberately

The allreduce hook averages 32-bit floating point (FP32) buckets. 16-bit floating point (FP16) and bfloat16 (BF16) hooks cast communication buffers down, reduce them and cast back; they reduce nominal payload precision, not the model's stored parameter dtype. FP16 can overflow sooner, while BF16 has coarser precision. PowerSGD approximates sufficiently large matrix-shaped gradients with low-rank factors. Forming and reducing those factors costs compute and extra collectives; small tensors may remain uncompressed. Error feedback retains approximation residuals, and warm start reuses factor information. These are lossy algorithms, not lossless transport compression. Finite values, declared update-error and task-quality checks, and compatible collective behavior on every rank remain acceptance requirements.

PowerSGD approximates a large gradient matrix using two smaller matrices whose product reconstructs an approximation. Communicating these low-rank factors can require fewer values than communicating the full matrix. Error feedback stores the omitted residual and adds it to a later gradient before compression so the same information is not silently discarded every step. SGD means stochastic gradient descent. Lower communication volume still has to repay factorization work and preserve acceptable training behavior.

### Distinguish readiness observation from a replacement reduction

A gradient hook is a callback triggered at a defined point in differentiation. A post-accumulate hook runs after a parameter's gradient has been accumulated, allowing an already-complete gradient to be reduced while independent backward work continues. It must not communicate an unfinished accumulation window. A DDP communication hook instead replaces the bucket's communication path and returns a future for its result. Keep the framework's averaging contract and identical collective order on every rank; observing readiness is different from silently changing the reduction.

Total collective duration can remain unchanged while step time improves, or an “asynchronous” collective can remain fully exposed. The optimization target is critical-path wait, not merely a shorter communication bar.

**Practice labs**

- [Lab 28: Reduce gradients when they become ready](reference/labs/28_communication_overlap.md)
- [Lab 33: Tune real DDP buckets and communication hooks](reference/labs/33_ddp_buckets.md)

**Mental model**

Gradients become ready from later to earlier layers. Buckets can launch collectives as soon as their gradients are complete while backward continues elsewhere. Lower-precision collective buffers change bandwidth and error, while SHARP can offload supported reductions to the fabric; both require topology- and recipe-specific qualification.

## 14. Parameter-efficient adaptation

**Objective**

Measure trainable-state reduction and evaluate adapter behavior independently from throughput.

**How it works**

Supervised fine-tuning (SFT) continues training an existing model on selected input/target examples, such as instructions paired with desired responses. Low-rank adaptation (LoRA) changes how an update is represented: selected base weights stay frozen while smaller trainable matrix factors contribute an additional transformation. A frozen parameter participates in computation but is not changed by the optimizer. The base model is the starting model, and an adapter is the added trainable component or its saved state.

LoRA's rank is the limited dimension through which the update factors are composed; it is unrelated to a distributed process's rank number. SFT specifies the supervision, while LoRA specifies which parameters express the change, so they can be used together. LoRA is not automatically quantization, and freezing weights does not eliminate every activation or backward cost. End-of-sequence (EOS) labels and response masks determine which behavior the fine-tuning objective actually teaches.

For an input column vector x, a bias-free projection produces `Wx`. LoRA adds a second path: `y = Wx + s * B(Ax)`. A maps input width to the smaller rank r, B maps that r-dimensional result to output width, and s scales the update. Train A and B while W stays fixed. For a hand calculation, let W be the 2-by-2 identity, A=[1, 0], B=[0, 2] as a column, s=0.5 and x=[3, 4]. Then Ax=3, B(Ax)=[0, 6], the scaled update is [0, 3], and y=[3, 7]. This illustrates composition, not the library's initialization. Standard scaling is alpha/r unless a different recipe is explicitly selected; the lab's configuration defines it. The shapes of A and B explain the parameter count below.

The core training path now explains updates, optimizer memory, precision, data flow and distributed placement. Apply that ledger to SFT, which changes the supervised-data contract, and LoRA, which changes the trainable parameterization. The supplied single-GPU adapter lab does not require a distributed run; the preceding distributed lessons provide context for scaling it later.

SFT first determines the supervised examples and the tokens that contribute to loss. The model and tokenizer revision, chat template, EOS handling, truncation, prompt-loss policy and held-out split define that objective. A prompt mask is a recipe choice; it must not be assumed merely because a run uses LoRA.

LoRA then changes how selected weight updates are represented. For a projection W with shape `d_out × d_in`, the frozen base path remains in the computation. A trainable input factor maps into rank r, and a second factor maps back to the output width. Their scaled result is added to the base output. Together they contain `r(d_in+d_out)` trainable parameters instead of `d_in*d_out` for a full matrix update.

Backward still passes through the computation needed to train those factors, but the optimizer does not update the frozen base weights. Adapter weights, gradients and optimizer state can therefore be much smaller. Base weights, activations, workspaces and temporary tensors still consume memory, so total savings need not match the reduction in trainable parameter count.

Reconstructing the effective model requires the adapter values and configuration plus the matching base revision. Evaluation checks whether that model performs the intended task and avoids regressions on held-out cases. Throughput describes a separate system outcome; a fast adapter update is not evidence of useful fine-tuning by itself.

Small trainable state is often confused with proportionally smaller total memory or faster steps. The frozen base still occupies memory and participates in forward/backward activation computation.

**Practice labs**

- [Lab 05: Fine-tune a small model with LoRA adapters](reference/labs/05_lora_sft.md)
- [Lab 13: Decide which tokens contribute to the training loss](reference/labs/13_loss_masking.md)

**Mental model**

LoRA learns low-rank updates while base weights remain frozen. It reduces trainable parameters and optimizer state but does not automatically remove base-model activations or all compute.

## 15. Reward-guided policy optimization

**Objective**

Compute group-relative advantages and locate rollout, reward, policy, and synchronization costs.

**How it works**

Group Relative Policy Optimization (GRPO) is a reinforcement-learning method that adjusts a model using several scored answers to the same prompt. The policy is the model's distribution over possible actions—in this case generated tokens. A rollout is a sampled answer, a reward is its score, and an advantage expresses how favorable it was relative to a baseline, here derived from the group. This provides a learning signal without requiring a desired token at every position as supervised training does.

The old policy generated the sampled answers; a reference policy can separately anchor behavior. A probability ratio compares a sampled token's new and old probabilities. The surrogate objective is a tractable training expression using these sampled quantities. Clipping moderates that expression; it is not a hard bound on every policy change. Kullback–Leibler (KL) divergence measures a difference between probability distributions. Reward hacking means improving the score without improving the intended behavior, so successful optimization of the toy objective is not proof of answer quality.

Supervised fine-tuning (SFT) learns from fixed target tokens. GRPO-style post-training samples several outputs from the current or near-current policy and learns from relative reward within each group.

### Generate candidates and turn scores into advantages

For one prompt, the recorded behavior policy generates a group of candidate responses. A reward function scores them. Subtracting the group's mean reward expresses whether each response performed better or worse than its peers. A common recipe divides that difference by a stabilized standard deviation to form an advantage. A zero-variance group needs an explicit rule so normalization does not divide by zero.

### Compare the new policy with the policy that sampled the data

The old policy is the frozen behavior policy that generated the rollouts. The new policy is the one being optimized. For a sampled token, `ratio = exp(new_log_probability - old_log_probability)` expresses how its probability changed. A ratio above one means the new policy assigns it more probability.

For advantage A and clip width epsilon, the maximized surrogate is `min(ratio × A, clamp(ratio, 1-epsilon, 1+epsilon) × A)`. It limits the incentive from some large probability changes in the sampled objective; it is not a hard bound on every change to the complete policy. A separate reference policy, often the initial model, can anchor behavior through a divergence penalty with a declared coefficient. That reference need not be the old sampling policy.

### Keep the system synchronized with the objective

The full loop includes generation, reward or verifier execution, objective computation and a trainer update. Colocating generation and training avoids some weight transfers; separating them allows independent scaling but adds queues and synchronization. Policy versions identify which weights produced each sample and prevent stale rollouts from being interpreted as fresh ones.

Candidate lengths, filtering, reward components, rollout latency, verifier latency and trainer time explain where work is spent. Held-out and adversarial cases test whether the score rewards the intended behavior. Improving the surrogate or reward alone cannot rule out reward hacking.

The objective is only one part of the system. Rollout generation, verifier/reward execution, variable output lengths, policy weight transfer, and stale samples can dominate time or corrupt learning.

Consider three completions with rewards 1, 2 and 3. Their group mean is 2. If this illustration uses a population standard deviation, it is sqrt(2/3), about 0.816, so the normalized advantages are approximately −1.225, 0 and +1.225 before any stabilizing epsilon. The chosen estimator and reward grouping are part of the objective. With clipping width 0.2, probability ratio 1.4 and advantage +1, the smaller of 1.4 and clipped 1.2 contributes 1.2. With advantage −1 and ratio 0.6, the smaller of −0.6 and clipped −0.8 is −0.8. The sign matters: clipping limits the incentive to move too far in the favorable direction.

**Practice labs**

- [Lab 06: Work through a group-relative policy objective](reference/labs/06_grpo_objective.md)
- [Lab 07: Verify the GRPO generation-to-update loop](reference/labs/07_grpo_trainer.md)

**Mental model**

A rollout system generates candidates, scores them, normalizes relative rewards, and updates a policy under clipping or divergence constraints.

## 16. Evidence-based training optimization

**Objective**

Select one verified training change from profiler evidence and defend keep or reject.

**How it works**

A causal training-optimization report argues that one controlled change affected performance while preserving the learning task. The baseline is the reference implementation; the candidate contains the change; a control helps test whether the proposed cause really explains the observation. An independent trial starts a separate run, rather than taking another sample from the same warmed process. Counterbalanced order alternates which variant runs first so startup or time-dependent effects do not consistently favor one side.

Uncertainty describes how much the evidence leaves unresolved, including run-to-run variation and measurement limits. Model FLOPs utilization (MFU), where FLOPs means floating-point operations, compares an estimate of useful model arithmetic per second with an appropriate hardware ceiling; hardware FLOPs utilization (HFU) also accounts for extra executed arithmetic under its stated convention. Neither ratio diagnoses a bottleneck by itself. This capstone connects these definitions to a scoped keep-or-reject decision, not a requirement to produce a speedup.

Every earlier lesson supplied one causal model and evidence type. The capstone joins them into a complete training decision rather than a collection of unrelated speed tips.

A causal report connects an observed delay to one change that should remove it. The baseline defines model, tokenizer and data revisions, global valid tokens, sequence distribution, optimizer, precision and which operations must finish before timing stops. At least three independent runs expose variation in step time, valid tokens/s, phase memory peaks and per-rank timing.

A short profiler window identifies a plausible limiting stage. The candidate changes that stage, while a disconfirming control tests whether the proposed explanation survives an alternative. DistributedDataParallel (DDP) versus Fully Sharded Data Parallel, second-generation interface (FSDP2) or eager versus checkpointed comparisons are meaningful only when they preserve the same work and update semantics. A keep/reject decision combines numerical gates, the complete-step result, uncertainty and the next unresolved question.

Utilization ratios summarize work under a stated counting convention. MFU uses useful model arithmetic and measured useful token rate; HFU may also count extra executed arithmetic. Neither ratio identifies the bottleneck. The following calculation explains why they can move in opposite directions.

For a purely illustrative calculation, suppose a step performs 100 trillion useful floating-point operations in 1 second against a matching 200-trillion-operations/second peak: both ratios are 50 percent under this counting convention. Recomputing another 50 trillion operations while taking 1.2 seconds raises HFU to 150/(1.2 × 200) = 62.5 percent, but MFU falls to 100/(1.2 × 200) ≈ 41.7 percent. More hardware work did not produce more useful updates. State the exact FLOP convention, precision, dense/sparse peak, time boundary and measured inputs. These hypothetical numbers are not H100 results.

Tokens per second and MFU summarize outcomes but do not diagnose causes. A credible report preserves failed hypotheses, numerical gates, workload identity, and limitations alongside a successful or rejected change.

**Practice labs**

- [Lab 30: Inspect the operators in a tiny training step](reference/labs/30_training_profiler.md)
- [Lab 31: Validate an optimization across complete training updates](reference/labs/31_training_capstone.md)

**Mental model**

A report connects equivalent-work correctness to step time, tokens/s, memory, communication, and an explicitly bounded MFU estimate.
