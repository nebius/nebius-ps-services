# Lab 32: Learn a weight and separate training from inference

This first exercise makes learning visible without a transformer, downloaded model or service. A scalar model predicts weight × input. Starting with weight zero, PyTorch computes prediction error, differentiates it and updates the weight for 30 steps, then checks how closely its predictions reproduce the examples. You then use the learned weight on an input excluded from training and confirm that inference does not change the weight. The purpose is to understand the state transition before studying performance engineering.

## Before you start

**Theory preparation:** Read Lesson 1’s learning-step example for a parameter, forward prediction, mean squared error, gradient, SGD update and held-out inference. Explain which value changes during learning and which values remain fixed during evaluation before running the CPU example.

Activate the course's PyTorch environment. CPU is the default and is sufficient for this conceptual exercise; CUDA is an explicit option on a full H100 allocation. No data or model is downloaded. Read the opening definitions of parameter, prediction, loss, gradient, optimizer and held-out evaluation. Both profile labels run the same tiny example; neither is a performance benchmark. The seed field is shared result metadata, but this deterministic example does not sample training data.

H100 changes the feasible batch, precision, and parallel execution, not the definition of the objective. Tiny course models demonstrate mechanics and performance measurement, never production model quality.

## Concepts and code path

The source has two responsibilities. `run_experiment` owns a small, inspectable learning loop and numerical checks. `main` selects a device, applies the course's private result-writing convention and reports completion. `common.py` provides environment checks and result handling without cross-course imports. Inputs [1, 2] have targets [2, 4]; only one FP32 weight is trainable. Mean squared error is the mean of the squared prediction differences. Autograd creates the gradient; SGD subtracts learning rate × gradient. The loop clears gradients before each of 30 updates so they do not unintentionally accumulate.

After learning, inference mode computes the remaining loss and the prediction for input 3. It records no new gradient graph. The program separately checks the parameter value and the absence of gradients; disabling graph recording alone is not proof that arbitrary application code cannot mutate a weight.

## Practice

Given a prompt of 20 tokens and a response of 30, SFT may compute causal logits for all 50 but divide loss over only the 30 response labels. Change from full fine-tuning to LoRA on selected projections. Expected observation: trainable and optimizer bytes fall sharply while forward activations and most matrix work remain; quality must still be judged on a held-out task set.

Begin with Lab 32 to observe forward, loss, backward, an update and held-out inference. Make a read-only preview of the objectives in Labs 01, 05, 06, and 07 and name the trainable parameters. Do not run the advanced trainers yet: execute the basic update in Lesson 4, SFT/LoRA in Lesson 14, and GRPO in Lesson 15.

From the course root, run the CPU path first. The second command is an alternative on the learner-provided Slurm cluster, not a requirement for learning the concept. Outputs use the normal private, no-clobber result writer.

```bash
umask 077
python3 labs/32_learning_basics.py --device cpu
sbatch slurm/single_gpu.sbatch labs/32_learning_basics.py --device cuda
```

## Check your results

Expect `initial_weight` 0, `loss_before` 10 and `first_gradient` -10. After the first update the weight would be 1 and loss 2.5; after the supplied 30 updates `final_weight` should be approximately 2 and `held_out_prediction` approximately 6. FP32 checks use rtol 1e-5 and atol 1e-6. `weight_after_inference` must equal the final learned value. Inspect the result's environment before labeling it a CPU or H100 run. No elapsed-time or speedup claim is produced.

Record stage, dataset contract, target tokens, loss, trainable-state count, and evaluation criterion.

Performance comparisons are valid only within an equivalent training objective and update contract.

## Investigate the behavior

Hand-calculate the first update before reading the result. Why does a negative gradient increase the weight when SGD subtracts it? Why would omitting gradient clearing change the update rule? As an explicit source-edit extension, try a smaller learning rate and compare update count; do not invent a command-line flag. Explain why one successful held-out point on this synthetic linear rule does not prove generalization on language, noisy data or an unrelated task.

Full updates provide flexibility but require more optimizer state and communication. Adapters reduce trainable state but not necessarily activations or base-model compute. Reward loops add rollout, scoring, synchronization, and instability that tokens-per-step alone cannot describe.

## If something goes wrong

If PyTorch cannot be imported, check that the intended interpreter is selected and the course dependencies are installed. CUDA selection intentionally fails if a full compatible H100 is unavailable; use the CPU mode for the introductory concept. Numerical failure after editing the loop requires checking the target formula, reduction, learning rate and gradient clearing. Do not weaken the tolerance to conceal an incorrect update.

Calling every adaptation “fine-tuning” hides different memory, quality, and checkpoint requirements.

## Takeaways and next step

Training changes parameters through forward, loss, backward and optimizer steps. Inference uses the resulting parameters without those updates. Carry this distinction into next-token labels, transformer logits and cross-entropy; the larger Lab 01 adds realistic tensor structure, not a different meaning of learning.

State the exact data, targets, trainable tensors, update rule, and acceptance gate for the chosen stage.

Explain why executing a different learning objective faster is not evidence that the original training task was optimized.
