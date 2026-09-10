# Lab 32: Learn a weight and separate training from inference

This first exercise makes learning visible without a transformer, downloaded model or service. A scalar model predicts weight × input. Starting with weight zero, PyTorch computes prediction error, differentiates it and updates the weight for 30 steps, then checks how closely its predictions reproduce the examples. You then use the learned weight on an input excluded from training and confirm that inference does not change the weight. The purpose is to understand the state transition before studying performance engineering.

## Before you start

**Theory preparation:** Read Lesson 1's scalar example for prediction, mean squared error, gradient, SGD and held-out evaluation. Predict the first update before running.

Activate the course's PyTorch environment. CPU is the default and is sufficient for this conceptual exercise; CUDA is an explicit option on a full H100 allocation. No data or model is downloaded. Read the opening definitions of parameter, prediction, loss, gradient, optimizer and held-out evaluation. Both profile labels run the same tiny example; neither is a performance benchmark. The seed field is shared result metadata, but this deterministic example does not sample training data.

H100 changes the feasible batch, precision, and parallel execution, not the definition of the objective. Tiny course models demonstrate mechanics and performance measurement, never production model quality.

## Concepts and code path

`run_experiment` owns the learning loop and checks; `main` selects the device and writes the result. `torch.nn.Parameter` marks the scalar weight as trainable. Inputs [1, 2] have targets [2, 4]. Each of 30 steps clears the previous gradient, computes mean squared error, calls backward and applies SGD with learning rate 0.1. Clearing matters because backward otherwise accumulates gradients.

After learning, inference mode computes the remaining loss and the prediction for input 3. It records no new gradient graph. The program separately checks the parameter value and the absence of gradients; disabling graph recording alone is not proof that arbitrary application code cannot mutate a weight.

## Practice

Run the CPU example first and inspect the first gradient, final weight and held-out prediction. CUDA is an explicit alternative on an allocated H100. Continue to Lab 01 after Lessons 2–4; leave SFT/LoRA and GRPO for Lessons 14–15.

From the course root, run the CPU path first. The second command is an alternative on the learner-provided Slurm cluster, not a requirement for learning the concept. Outputs use the normal private, no-clobber result writer.

```bash
umask 077
python3 labs/32_learning_basics.py --device cpu
sbatch slurm/single_gpu.sbatch labs/32_learning_basics.py --device cuda
```

## Check your results

Expect `initial_weight` 0, `loss_before` 10 and `first_gradient` -10. After the first update the weight would be 1 and loss 2.5; after the supplied 30 updates `final_weight` should be approximately 2 and `held_out_prediction` approximately 6. FP32 checks use rtol 1e-5 and atol 1e-6. `weight_after_inference` must equal the final learned value. Inspect the result's environment before labeling it a CPU or H100 run. No elapsed-time or speedup claim is produced.

## Investigate the behavior

Hand-calculate the first update before reading the result. Why does a negative gradient increase the weight when SGD subtracts it? Why would omitting gradient clearing change the update rule? As an explicit source-edit extension, try a smaller learning rate and compare update count; do not invent a command-line flag. Explain why one successful held-out point on this synthetic linear rule does not prove generalization on language, noisy data or an unrelated task.

## If something goes wrong

If PyTorch cannot be imported, check that the intended interpreter is selected and the course dependencies are installed. CUDA selection intentionally fails if a full compatible H100 is unavailable; use the CPU mode for the introductory concept. Numerical failure after editing the loop requires checking the target formula, reduction, learning rate and gradient clearing. Do not weaken the tolerance to conceal an incorrect update.

## Takeaways and next step

Training changes parameters through forward, loss, backward and optimizer steps. Inference uses the resulting parameters without those updates. Carry this distinction into next-token labels, transformer logits and cross-entropy; the larger Lab 01 adds realistic tensor structure, not a different meaning of learning.
