# Visual plan

The table defines this course's overview diagrams. The
[visual manifest](visual-manifest.json) links additional detailed diagrams to
their conceptual lessons. Each figure appears after the specified section in its declared lesson or lab home,
with accessible labels, captions, and fit-to-width sizing.

The end-to-end performance-system diagram follows **Start here** in Lesson 1,
connecting the introductory CPU preparation, dispatch, transfer, GPU work and
completion explanation to the limiter model developed later.

| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Causal optimization loop | Freeze baseline | Change one factor | Remeasure and decide | Every optimization starts and ends with equivalent-work evidence. | 1 | Mechanism | cycle | lesson |
| Profiler evidence funnel | Framework operators | System timeline | Selected kernel counters | Start broad and collect detail only for the next causal question. | 3 | Mechanism | decision | lesson |
| Asynchronous timing | CPU submission | Device execution | Synchronized boundary | Different timers answer different questions about queued GPU work. | 2 | Mechanism | timeline | lesson |
| Launch and fusion | Many small launches | Fused device work | End-to-end result | Fusion can reduce kernel launches and intermediate-memory traffic; measure whether those savings improve end-to-end time. | 4 | Mechanism | comparison | lesson |
| Input pipeline | Storage and CPU | Pinned transfer | GPU consumer | Queues and overlap keep equivalent batches ready for device work. | 6 | Mechanism | timeline | lesson |
| Allocator lifetime | Live tensors | Cached blocks | Peak/fragmentation evidence | Allocated and reserved memory describe different ownership states. | 8 | Mechanism | flow | lesson |
| Shape and precision | Workload shapes | Library dispatch | Time and accuracy | Padding and dtype affect both fast-path eligibility and useful work. | 9 | Mechanism | flow | lesson |
| Collective overlap | Backward compute | Ready-bucket collectives | Exposed communication | Only communication on the critical path extends the step. | 12 | Mechanism | overlap | lesson |
| Tail diagnosis | Lane divergence | Block or rank skew | Final partial wave | Distinct imbalance levels require distinct remedies. | 10 | Mechanism | comparison | lesson |
| Library-first decision | Framework or compiler | Maintained library | Custom CUDA residual gap | Escalate only when lower-maintenance paths fail with evidence. | 13 | Mechanism | decision | lesson |
