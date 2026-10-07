# PyTorch for GPU Performance Engineering

Read PyTorch code and see the work it asks the GPU to do: calculate, move data,
allocate memory or wait. This foundation prepares you for
[GPU Fundamentals](../gpu-fundamentals/index.html) and the performance labs that follow.

**Before you begin:** basic Python variables, lists, functions and loops are enough.
No machine-learning background, GPU or running cluster is needed for reading.
Examples use small invented inputs; numeric outputs are worked results, not
performance measurements. Blocks marked **CUDA example** require an NVIDIA GPU
and a compatible PyTorch environment if you choose to run them later.
Already comfortable with PyTorch? If you can explain why a transpose can share
storage, why **`eval`** does not disable gradients and why **`item`** can wait for
CUDA, use the final worked example as your entry point and revisit unfamiliar
lessons.

**Reading route:** lessons 1–7 explain tensors; 8–11 explain execution and models;
12–18 connect them to performance and optimization. Read each objective, opening
definition, diagram and code comments, then the explanation and performance
connection. The full route is about three guided hours; there are no labs,
exercises or setup tasks.

**Reading the notation:** bold monospace marks an actual PyTorch API name, such
as **`shape`**, **`dtype`** or **`torch.randn`**. Ordinary monospace marks example
variables and values, such as `x` and `[2, 3, 4]`. Plain words such as batch,
position and feature describe concepts or meanings we assign to an example.
Their meanings come from the application. Headings and callout labels may
also be bold. Code blocks use normal Python formatting, with explanatory comments.

## 1. A tensor is data with a description

### Objective

Read a tensor's **`shape`**, element count, **`dtype`** and **`device`**, and explain what each tells you.

### How it works

PyTorch is a library for computing with tensors on CPUs and accelerators. Its
**`torch.Tensor`** type represents values arranged along zero or more dimensions.
A dimension, also called an axis, organizes entries in a tensor: a matrix has
a row axis and a column axis. Each axis has a size, such as two rows or three
columns. Lesson 2 explains how to choose entries along each axis.

A tensor combines stored values with metadata: information describing those
values. Start with three attributes. **`shape`** gives the size of each axis;
**`dtype`** gives the element representation; **`device`** identifies where the
values live. The diagram describes one tensor from these three perspectives.
Element count is a separate fact derived from its sizes. Each card describes
one property of the same tensor.

![One tensor, three attributes and an element count](reference/diagrams/tensor-description.svg)

```python
import torch

x = torch.tensor([[1., 2., 3.],
                  [4., 5., 6.]], dtype=torch.float32, device="cpu")
print(x.shape)    # torch.Size([2, 3]): two rows, three columns
print(x.ndim)     # 2: the number of dimensions
print(x.numel())  # 6: the number of elements, 2 * 3
print(x.dtype)    # torch.float32: each element uses four bytes
print(x.device)   # cpu: values are in CPU memory
```

Here **`ndim`** counts axes, while **`numel`** counts values. The two axes have
sizes 2 and 3, so six values are stored. FP32 means 32-bit floating point,
represented by **`torch.float32`**; each element uses four bytes. The CPU is the
central processing unit; a GPU, or graphics processing unit, is an accelerator.
The example above creates values on the CPU. Estimating an operation's cost
combines these attributes with the computation it performs.

Unless an example states otherwise, it uses PyTorch's ordinary defaults: newly
created floating tensors are FP32 on the CPU. Integer sequences below use
**`torch.int64`**. The examples retain these global **`dtype`** and **`device`** defaults.

Other common constructors make intent easy to spot:

```python
zeros = torch.zeros(2, 3, dtype=torch.float32, device="cpu")
ones = torch.ones_like(zeros)  # Same shape, dtype and device; fill with 1
ids = torch.arange(4)         # Integer values: [0, 1, 2, 3]
```

**`torch.zeros`** fills the requested **`shape`** with zero. **`torch.ones_like`**
uses an existing tensor as the attribute template. **`torch.arange`** generates
a sequence from zero up to the value just before its stop: `0, 1, 2, 3` here.
Each constructor defines the meaning of its arguments; these comments identify
sizes, fill values and sequence boundaries.

#### Shape and random values answer different questions

In **`torch.rand`** and **`torch.randn`**, the positional numbers specify axis
sizes. Both calls below create two rows and three columns. Their difference is
the distribution: the rule describing how likely different values are.

- **`torch.rand`** uses a uniform distribution on `[0, 1)`: equal-width intervals
  within that range have equal probability. Zero is included; one is excluded.
- **`torch.randn`** uses a standard normal distribution: values cluster near zero
  with a standard deviation of one, a measure of spread. Negative values and
  values above one are possible; the distribution extends across the real-number line.

The small sketches illustrate the sampling rules. Curve height shows relative
probability density: a higher curve indicates more likely values nearby.

![Same shape, different sampling rules](reference/diagrams/random-values.svg)

```python
uniform = torch.rand(2, 3, dtype=torch.float32, device="cpu")
normal = torch.randn(2, 3, dtype=torch.float32, device="cpu")
# Both: shape [2, 3], dtype torch.float32, device cpu.
# rand: each value is at least 0 and less than 1.
# randn: values may be negative or greater than 1.
# The sizes 2 and 3 create two rows with three values each.
```

The distribution has mean zero. The average of a small random sample varies
from draw to draw. Later examples use fixed values so we can calculate their
results directly.

**Performance connection:** element count estimates the amount of data. **`dtype`**
sets bytes per element, while **`device`** determines which execution path is available.
Start with those facts before guessing why a workload is slow.

### Mental model

A tensor is values plus a description. Inspect the description before reading the computation.

## 2. Dimensions are indexed axes with sizes

### Objective

Use grid, row and column indices to select values, and explain the shapes produced by slicing and reshaping.

### How it works

An axis is a dimension used to organize values. Its size counts the entries
along it. A **`shape`** lists these sizes in order. Here, `[2, 3, 4]` describes
two grids, each with three rows and four columns: `2 * 3 * 4 = 24` values.

An index is an integer that chooses an entry along an axis. The plural of
index is indices. Counting starts at zero, so index `0` chooses the first entry
and index `1` chooses the second. A grid is a table of values; a grid index is
the number used to choose one of those tables.

| Axis number | What its index chooses | Size | Available indices |
| --- | --- | --- | --- |
| 0 | A grid | 2 | `0`, `1` |
| 1 | A row in that grid | 3 | `0`, `1`, `2` |
| 2 | A column in that row | 4 | `0`, `1`, `2`, `3` |

Read `x[1, 2, 3]` from left to right: grid index `1`, row index `2`, column
index `3`. These three indices select the second grid, its third row and its
fourth column. The highlighted cell below contains the value `23`.

![Choose a grid, a row and a column](reference/diagrams/shape-axes.svg)

```python
import torch

x = torch.arange(24).reshape(2, 3, 4)  # Arrange 0 through 23 into two grids
print(x.ndim)                        # 3 axes: grid, row, column
print(x[1, 2, 3])                    # tensor(23)
```

#### Selecting a grid, a row or one value

Each index narrows the selection. These examples continue with the same `x`:

| Expression | Selected result | Result shape |
| --- | --- | --- |
| `x[0]` | First grid: three rows of four values | `[3, 4]` |
| `x[0, 1]` | Second row of the first grid: `[4, 5, 6, 7]` | `[4]` |
| `x[0, 1, 2]` | Third value in that row: `6` | `[]` |

A matrix has two axes, a vector has one, and a scalar tensor holds one value
with zero axes. Their shapes above are `[3, 4]`, `[4]` and `[]`. A one-element
vector has **`shape`** `[1]`: one axis containing one value.

#### Keeping a group with a slice

A slice selects a range of entries and keeps their axis. `0:1` means start at
index `0` and stop just before index `1`, so it selects the first grid.

```python
print(x[0].shape)    # torch.Size([3, 4]): one grid
print(x[0:1].shape)  # torch.Size([1, 3, 4]): a group containing one grid
```

Both results contain the first grid's twelve values. `[3, 4]` describes its
rows and columns; `[1, 3, 4]` also keeps the grid axis, now with size one.
The arrows show two separate selections from the original `x`.

![One grid and a group containing one grid](reference/diagrams/index-versus-slice.svg)

A colon `:` on its own selects every entry on that axis. Read `x[:, :, 0]`
as “every grid, every row, first column”:

```python
print(x[:, :, 0])  # One value from each row in each grid
# tensor([[ 0,  4,  8],
#         [12, 16, 20]])
```

#### Giving the same values a new shape

**`reshape`** arranges the same values into new axis sizes while preserving
their reading order. Here it places the first grid's three rows followed by
the second grid's three rows into one six-row matrix:

```python
flat = x.reshape(6, 4)  # Six rows, each with four values
print(flat.shape)      # torch.Size([6, 4])
```

Both shapes contain 24 values: `2 * 3 * 4 = 6 * 4`. Our application supplies
the axis meanings. For sensor data, those original sizes could describe two
sensors, three readings per sensor and four measurements per reading.

**Performance connection:** multiplying the sizes gives the element count.
Both shapes above contain 24 values; the new shape groups them into six rows
for later operations.

### Mental model

Shape lists sizes. An index chooses an entry; a slice keeps a range; reshaping gives the same values a new arrangement.

## 3. Broadcasting reuses values across axes

### Objective

Predict a broadcast result and spot an accidental expansion.

### How it works

Broadcasting lets an operation reuse values from a smaller tensor across a
larger one. Compare sizes from the right: each pair must match, or one size must
be 1. A missing leading dimension behaves like size 1. For the nonempty tensors
here, the result takes the larger size on each axis.

Adding one bias per feature to a `[2, 3]` matrix reuses the same three bias
values for both rows. A bias here is simply an offset added to a value; feature
is our chosen meaning for a column. The repeated bias row in the diagram shows
how the operation reuses the original three bias values for each input row.

![One bias row serves two input rows](reference/diagrams/broadcast.svg)

```python
import torch

x = torch.tensor([[1., 2., 3.], [4., 5., 6.]])
bias = torch.tensor([10., 20., 30.])  # [3] aligns with the last axis
y = x + bias                        # [2, 3] + [3] -> [2, 3]
print(y)  # [[11, 22, 33], [14, 25, 36]]

row_offset = torch.tensor([[100.], [200.]])  # [2, 1]
z = x + row_offset  # [2, 3] + [2, 1] -> [2, 3]
print(z)            # [[101, 102, 103], [204, 205, 206]]
```

**Watch for:** `[3, 1] + [3]` produces `[3, 3]`, not `[3, 1]`. This can silently
create much more output than intended. `[2, 3] + [2]` fails because the trailing
sizes 3 and 2 disagree.

**Performance connection:** broadcasting avoids explicitly repeating the input,
but an ordinary addition still computes and stores its output. A tiny bias does
not make a huge output cheap. **`expand`** creates a view that reuses storage;
**`repeat`** copies repeated values. Avoid writing into expanded views because
multiple logical entries can refer to the same stored value.

### Mental model

Align from the right. Broadcasting can save input storage while still creating a large result.

## 4. Element-wise work and matrix multiplication differ

### Objective

Distinguish `*` from `@` and predict the **`shape`** of a batched matrix multiplication.

### How it works

An element-wise operation applies a rule independently to corresponding
entries, after any broadcasting. Matrix multiplication combines a row with a
column: multiply matching entries, then add the products. Let `M` be the first
matrix's row count, `K` its column count and the second matrix's row count, and
`N` the second matrix's column count. For `[M, K] @ [K, N]`,
the shared size `K` is combined away; the output is `[M, N]`.

In the diagram, the top calculation is one element-wise output; the bottom is
one matrix-product output. The plus sign belongs only to the row-column sum.

![The same inputs can mean different arithmetic](reference/diagrams/multiply.svg)

```python
import torch

a = torch.tensor([[1., 2.], [3., 4.]])
b = torch.tensor([[5., 6.], [7., 8.]])
print(a * b)  # [[5, 12], [21, 32]]: multiply corresponding entries
print(a @ b)  # [[19, 22], [43, 50]]: row-by-column products
# First matrix-product entry: 1 * 5 + 2 * 7 = 19

left = torch.ones(2, 3, 4)   # Two matrices, each [3, 4]
right = torch.ones(2, 4, 5)  # Two matching matrices, each [4, 5]
out = left @ right         # [2, 3, 5]; every entry is 4
print(out.shape)
```

For tensors with at least two dimensions, `@` uses the last two as matrix axes.
Leading axes are batch axes and may broadcast. `[2, 3, 4] @ [4, 5]` therefore
also yields `[2, 3, 5]`, reusing one right-hand matrix for both batches.
Here batch means a group processed together. The matrix operator uses the
leading axes to group independent matrix products. Our application gives those
axes meanings such as sample or time step. A single
vector also works: `[4] @ [4, 5]` produces `[5]`, treating the vector as one row
for the multiplication and omitting that temporary row axis in the result.

**Performance connection:** matrix multiplication performs many arithmetic
operations per output and can reuse input data. Element-wise work often performs
little arithmetic per value read and written. These are investigation clues,
not proof of a compute or memory bottleneck; small matrix products can also spend
much of their time on submission overhead.

### Mental model

`*` pairs entries. `@` combines rows and columns. **`shape`** reveals the arithmetic requested.

## 5. Reductions remove information and axes

### Objective

Predict a reduction's values and **`shape`**, including **`keepdim`** set to `True`.

### How it works

A reduction combines multiple values into fewer values: sum, mean and maximum
are examples. The **`dim`** argument selects the axis to combine. Axis numbers
start at zero; `-1` selects the last axis. **`keepdim`** set to `True`
retains that axis with size 1, which often makes the next broadcast unambiguous.

Each row in the diagram becomes its mean. Arrows mean “combine these values.”
Keeping the last axis gives one column, so subtraction can reuse each mean
across its original row.

![Keep one mean per row](reference/diagrams/reduction.svg)

```python
import torch

x = torch.tensor([[1., 2., 3.], [4., 5., 6.]])
print(x.sum(dim=0))    # [5, 7, 9]: combine rows, keep each column
print(x.mean(dim=1))   # [2, 5]: combine columns, one mean per row

means = x.mean(dim=-1, keepdim=True)  # [[2], [5]], shape [2, 1]
centered = x - means                 # Broadcast each row's mean
print(centered)                      # [[-1, 0, 1], [-1, 0, 1]]
print(x.mean().shape)                # torch.Size([]): scalar tensor
```

A scalar tensor holds one value and has a **`dtype`** and **`device`**.
**`item`** returns its value as a Python number; lesson 8 explains the possible CUDA wait.

Softmax turns scores into positive weights whose sum is 1 along a chosen
axis. It uses information across that axis, unlike a purely element-wise rule.
For example, **`torch.softmax`** with **`dim`** set to `-1` preserves `[2, 3]` and normalizes each row
separately. Libraries use numerically stable implementations; do not replace
them with a casual `exp(x) / exp(x).sum()` for large scores.

**Performance connection:** a scalar output can require reading a very large
input. Output size alone does not describe the work of a reduction.

### Mental model

A reduction combines an axis. **`keepdim`** preserves its place so later broadcasting stays clear.

## 6. Views change interpretation; copies move values

### Objective

Explain a transpose's **`shape`** and strides, and identify operations that may copy data.

### How it works

A view shares stored values with another tensor. A stride says how many
stored elements to step over when an index increases by one on an axis.
A row-major `[2, 3]` tensor has strides `(3, 1)`: step three elements to the next
row, one to the next column. Strides count stored elements.

The two index maps below point to the same storage. Transposing swaps the axes
and their strides while preserving the stored value order. The arrows connect
both views to their shared storage.

![Two index maps share the same storage](reference/diagrams/views-strides.svg)

```python
import torch

x = torch.arange(6).reshape(2, 3)  # [[0, 1, 2], [3, 4, 5]]
y = x.transpose(0, 1)             # [[0, 3], [1, 4], [2, 5]]
print(x.stride())                 # (3, 1)
print(y.shape, y.stride())        # [3, 2], (1, 3)
print(y.is_contiguous())          # False for this transposed view
z = y.contiguous()                # Copy into row-major order
print(z.stride())                 # (2, 1)

y[0, 1] = 99                     # Modify shared storage
print(x[1, 0])                    # tensor(99)
print(z[0, 1])                    # tensor(3): independent copy
```

Contiguous, in the default layout used here, means elements follow the
tensor's logical row-major order without gaps: finish one row before starting
the next. For the original `x`, index `[1, 2]` reaches stored element
`1 * 3 + 2 * 1 = 5`. Strides explain why the same storage can support a different
index map after a transpose. Other memory formats exist;
“contiguous” is not a universal promise of fast execution.

| Expression | Storage behavior to recognize |
| --- | --- |
| **`view`** | Requires compatible **`shape`** and strides; fails if a view is impossible. |
| **`reshape`** | Returns a view when possible; otherwise copies. |
| **`transpose`**, **`permute`** | Reorder axes as views. **`permute`** specifies all axes. |
| **`contiguous`** | Returns the input if already contiguous in the requested format; otherwise copies. |
| **`clone`** | Makes a copy; keeps gradient connectivity when tracking is enabled. |
| Basic slice / integer-array indexing | Basic slicing returns a view; advanced indexing returns a copy. |

To let **`reshape`** calculate one size, pass `-1` for that size. The six-value
`x` above gives **`shape`** `[3, 2]` with `x.reshape(3, -1)` because
`6 / 3 = 2`. Here `-1` means “calculate this size”; in an axis argument such
as **`dim`**, `-1` means “select the last axis.”

Choose **`reshape`** to regroup values in their logical reading order. Choose
**`transpose`** or **`permute`** to change axis order, such as moving an image's
channel axis before its height and width axes. Label the meaning of each size
to check that the new arrangement suits the next operation.

**Watch for:** calling **`view`** with size `-1` on this transposed `y` fails,
while **`reshape`** with the same size can copy. Adding **`contiguous`** everywhere may introduce unnecessary traffic.
Inspect the next operation and measure before changing layout.

**Performance connection:** a view can avoid moving values, but the next operation
still has to read them through its strides. A copy costs reads, writes and new
storage; a different layout may help later work. Judge the whole sequence, rather
than assuming every view is faster or every noncontiguous tensor needs repair.

### Mental model

**`shape`** gives axis sizes, and strides map indices to storage locations. Multiple tensor objects can share the same stored values.

## 7. Dtype changes storage and numerical behavior

### Objective

Estimate tensor bytes and explain why a smaller **`dtype`** needs a correctness check.

### How it works

A floating-point format represents approximate real numbers using a sign,
an exponent for scale and significant bits for precision. FP32 is 32-bit
floating point; FP16 is 16-bit floating point; BF16 is the 16-bit
bfloat16 format. BF16 has a wider exponent range than FP16 but fewer significant
bits. The same byte size does not imply the same numerical behavior.

Range describes how large or small nonzero magnitudes can be represented.
Precision describes how finely nearby values can be distinguished. A format can
cover a large range while rounding away small differences near a given value.

For an ordinary dense tensor, logical bytes equal element count times bytes per
element. These bars compare the same six values in FP32 and BF16; their lengths
represent bytes, not measured speed.

![Same shape, different byte counts](reference/diagrams/dtype-bytes.svg)

```python
import torch

x32 = torch.ones(2, 3, dtype=torch.float32)
x16 = x32.to(dtype=torch.bfloat16)  # Convert values; create new storage
print(x32.numel() * x32.element_size())  # 6 * 4 = 24 bytes
print(x16.numel() * x16.element_size())  # 6 * 2 = 12 bytes

value = torch.tensor([1.0001], dtype=torch.float32)
rounded = value.to(torch.bfloat16).float()  # Convert back to FP32
print(rounded)  # tensor([1.]): conversion cannot restore lost detail
```

This calculation is not total process memory: views may share a larger backing
allocation, and models need outputs, temporary storage and other state. Integer
indices often use **`torch.int64`**; masks use **`torch.bool`**. Do not convert indices
to floating point merely to reduce bytes.

Automatic mixed precision (AMP) chooses operation-specific dtypes in an
**`torch.autocast`** region. It does not convert every operation or model parameter to one
format. On supported GPUs, some matrix work can use Tensor Cores, hardware
units for matrix arithmetic. **`dtype`** alone does not establish their use or a speedup.

The course later shows autocast in context. For FP16 training, gradient scaling
helps prevent small gradients from vanishing during computation; BF16 usually
does not need that scaling. Validate numerical behavior and the actual workload
before accepting any precision change.

**Performance connection:** smaller elements can reduce storage and data traffic,
and suitable arithmetic may use faster hardware paths. These are different
mechanisms. Compare equivalent work and acceptable numerical error; halving
logical bytes does not promise twice the speed.

### Mental model

Smaller elements reduce logical bytes. They also change representable values, so correctness comes first.

## 8. Device placement selects where work happens

### Objective

Follow a tensor from CPU to GPU and identify when the CPU needs a completed result.

### How it works

The CPU runs the Python program. The GPU runs parallel device work. CUDA is NVIDIA's
GPU computing platform. A CUDA tensor's **`device`** selects GPU execution for supported
operations; Python itself continues on the CPU.

The diagram separates host submission from device execution. Arrows show requests
and data dependencies, not equal durations. A kernel is a function executed
on the GPU; a PyTorch operation can use one or several kernels or library calls.

![Submission is separate from completion](reference/diagrams/device-execution.svg)

**CUDA example:** input creation and scalar extraction are intentionally visible.

```python
import torch

cpu_x = torch.tensor([1., 2., 3.])  # Host values
x = cpu_x.to("cuda")               # Copy to the current CUDA device
y = x * 2                         # Submit device arithmetic
total = y.sum()                    # CUDA scalar tensor; value is 12
print(total.device)                # cuda:0 on the first visible GPU
answer = total.item()              # Wait for needed work; get host value
print(answer)                     # 12.0, a Python float
```

CUDA work is usually asynchronous relative to the CPU: the Python call can
return before device work finishes. A stream is an ordered queue of device
operations. Work in the same stream follows submission order, so **`sum`** can use
`y` without a manual wait between the two calls. This course uses the current
default stream; multiple streams require explicit dependency management.

Most arithmetic expects compatible tensor devices. **`Tensor.to`** returns the
requested tensor; assign that result to the variable you want to use.
**`torch.ones`** with **`device`** set to `"cuda"` creates values on the device directly.
**`cpu`**, printing CUDA values and **`item`** can make host code wait.
**`shape`** and **`dtype`** are metadata that host code can read directly.

**Performance connection:** thousands of tiny GPU operations can incur substantial
CPU submission overhead. Repeated host-visible scalar reads can also prevent the
CPU from submitting useful work ahead of the GPU.

### Mental model

Device selects execution. Submission can finish before computation; requesting a host value must obtain the result.

## 9. A model is a sequence of tensor operations

### Objective

Read a module's forward computation and track a linear layer's last dimension.

### How it works

An **`nn.Module`** groups computation and model state. A parameter is a
registered tensor, usually a learnable weight or bias. Registered buffers
hold other state, such as running statistics. The **`forward`** method defines the
computation; calling `model(x)` uses it through PyTorch's module machinery.

A sample is one input example; a batch is a group of samples processed together.
A feature is one numeric input or representation component. For the model below,
we choose **`shape`** `[2, 3, 4]` to mean two samples, three positions in each
sample and four features at each position.

**`nn.Linear`** with input size 4 and output size 2 converts each group of four
features into two output features. It reads features along the last axis, so
the input's last size must be 4. For our `[2, 3, 4]` input, the output has
**`shape`** `[2, 3, 2]`: two samples and three positions remain, and each position
now holds two features. The same learned transformation serves all six positions.

Each output feature is a weighted sum of the four inputs plus a bias. The
calculation is `x @ weight.T + bias`, with weight **`shape`** `[2, 4]` and bias
**`shape`** `[2]`. In the diagram, arrows carry tensors and boxes name operations.

![A linear layer transforms the last dimension](reference/diagrams/module-shapes.svg)

```python
import torch
from torch import nn

class TinyModel(nn.Module):
    def __init__(self):
        super().__init__()       # Initialize module registration
        self.proj = nn.Linear(4, 2)

    def forward(self, x):
        y = self.proj(x)         # [..., 4] -> [..., 2]
        return torch.relu(y)     # Replace negatives with zero; same shape

model = TinyModel()
x = torch.ones(2, 3, 4)          # Our axes: sample, position, feature
y = model(x)
print(y.shape)                  # [2, 3, 2]
print(model.proj.weight.shape)   # [2, 4]
print(model.proj.bias.shape)     # [2]
```

ReLU, the rectified linear unit, computes `max(0, value)` element by element;
**`torch.relu`** applies that rule to a tensor.
**`nn.Sequential`** is another common module: it passes each child module's
output to the next. A module's operations can execute through several GPU
kernels; profiling reveals the actual execution.

**`nn.Module.to`** with **`device`** `"cuda"` moves registered parameters and buffers,
modifies the module in place and returns it. Move inputs and any unregistered
tensor attributes explicitly with **`Tensor.to`** and retain the returned tensors.
**`named_parameters`** reveals parameter names, shapes, dtypes and devices.

**Performance connection:** increasing either the number of input vectors or
their feature dimensions changes matrix work and output storage. Read these
sizes before treating the model name as an explanation of its cost.

### Mental model

A module organizes tensor operations and state. Follow its forward path and the shapes crossing each operation.

## 10. Training saves information for a backward pass

### Objective

Explain the forward, loss, backward and update steps, including why gradients are cleared.

### How it works

Training adjusts parameters to reduce a loss, a scalar that measures
prediction error. A gradient describes how a small parameter change affects
that loss. Autograd, automatic differentiation, records relevant operations
and computes gradients during **`backward`**. Forward activations are intermediate
values; some must be retained until backward uses them.

The diagram shows one update: compute a loss, derive a gradient, then change the
weight. The backward arrow means differentiation, not another prediction.

![Forward values lead to gradients and an update](reference/diagrams/autograd.svg)

```python
import torch

w = torch.tensor(2.0, requires_grad=True)  # A scalar we want to learn
optimizer = torch.optim.SGD([w], lr=0.1)   # Stochastic gradient descent

optimizer.zero_grad(set_to_none=True)     # Start without an old gradient
prediction = w * 3.0                     # Forward: 2 * 3 = 6
loss = (prediction - 4.0) ** 2            # Squared error: (6 - 4)^2 = 4
loss.backward()                          # dw = 2 * (6 - 4) * 3 = 12
print(w.grad)                            # tensor(12.)
optimizer.step()                         # w becomes 2 - 0.1 * 12 = 0.8
```

**`lr`** is the learning rate, the update's scale. Real loops repeat these steps for
successive batches. **`backward`** accumulates into existing gradients, so clearing
them defines a new update. Deliberate accumulation across several batches is a
different training choice. **`backward`** computes gradients; **`step`** changes
parameters.

**`requires_grad`** requests gradient tracking for the tensor; its **`grad`**
attribute holds an accumulated gradient after backward. **`zero_grad`** clears
the optimizer's previous gradients. With **`set_to_none`** set to `True`, missing
gradients are represented by `None` instead of filled zero tensors. A missing
gradient and a zero gradient can lead to different optimizer behavior; use the
choice intentionally. Here the single weight receives a gradient each update.

Inputs need not require gradients just because parameters do. A layer can compute
parameter gradients from ordinary input data. An in-place operation, commonly
marked by a trailing underscore such as **`add_`**, changes an existing tensor.
It may invalidate values autograd saved, so in-place edits are not a general
memory optimization.

**Performance connection:** training adds backward computation, saved activations,
gradients and sometimes optimizer state. A forward-only memory estimate misses
these costs. This one-parameter SGD example has no momentum state; optimizers
such as Adam keep additional per-parameter tensors.

### Mental model

Forward produces the loss; backward computes gradients; the optimizer applies an update. Each stage has its own work and state.

## 11. Evaluation mode and gradient mode are separate

### Objective

Use **`eval`** and a gradient context for their distinct purposes when reading inference code.

### How it works

Inference uses a model to produce outputs without training it. **`eval`**
changes the behavior of modules that distinguish training and evaluation.
For example, **`nn.Dropout`** sets randomly selected activations to zero during training,
rescaling the survivors to preserve the expected scale. During evaluation it
passes values through. Autograd remains controlled separately.

**`torch.no_grad`** disables gradient recording in its region. **`torch.inference_mode`**
also removes some tracking overhead and suits computations whose results stay
outside later gradient-tracked work. Set evaluation mode and choose a gradient
context separately, as the diagram's two switches show.

![Two independent controls for inference](reference/diagrams/inference-controls.svg)

```python
import torch
from torch import nn

model = nn.Sequential(nn.Linear(4, 2), nn.Dropout(p=0.5))
x = torch.ones(3, 4)
model.eval()                       # Dropout now passes values through
tracked = model(x)                 # Parameters still require gradients
print(tracked.requires_grad)       # True: gradient tracking remains enabled

with torch.inference_mode():
    output = model(x)              # No backward graph for inference
print(output.shape)                # [3, 2]
print(output.requires_grad)        # False
```

Use **`torch.no_grad`** when you need the less restrictive mode, for example when fixed
features will later feed a trainable layer. **`detach`** creates a tensor that
shares the original storage and has its connection to gradient history removed.

**CUDA example:** AMP can be combined with inference on a GPU that supports BF16.
The parameters stay FP32 here; eligible operations choose a lower precision.

```python
# Continue with the model and x above; requires CUDA with BF16 support.
model = model.to("cuda")
x_gpu = x.to("cuda")
with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
    output_gpu = model(x_gpu)  # Shape [3, 2]; check numerical quality later
```

**Performance connection:** evaluation behavior protects the meaning of the
prediction, while disabling gradient recording can avoid backward bookkeeping
and saved tensors. Mixed precision is a separate numerical/performance choice.
Compare the same inference behavior and validate outputs before judging a speed
or memory change.

### Mental model

**`eval`** selects module behavior. Gradient contexts decide whether computation is recorded for differentiation.

## 12. Transfers are part of the input pipeline

### Objective

Distinguish data preparation, host-to-device copying and GPU computation without assuming they overlap.

### How it works

An input pipeline prepares batches and delivers them to the model. A PyTorch
**`DataLoader`** groups samples and can use worker processes for loading. Moving
each batch from CPU memory to GPU memory is a host-to-device (H2D) transfer.
The reverse is a device-to-host (D2H) transfer.

Pinned memory is host memory kept resident for device transfers. It can
enable efficient asynchronous copies. **`non_blocking`** set to `True` asks the transfer to
avoid a host-side wait where supported; it does not by itself arrange overlap
between copying and GPU computation.

The diagram is a deliberately serial pipeline: arrows show the dependencies of
one batch. Overlap across batches requires suitable hardware, streams and data
dependencies; it must be observed, not inferred from the option name.

![A batch crosses preparation, copy and compute](reference/diagrams/input-pipeline.svg)

**CUDA example:** a tiny in-memory dataset makes each stage visible.

**`TensorDataset`** retrieves samples from the first axis of its tensors. Here
the source has six rows, and **`batch_size`** set to 2 groups two rows per batch.
Each dataset sample is a one-item tuple. The loader's default collation, which
combines samples into a batch, returns a one-item list containing a `[2, 4]`
tensor. The loop unpacks that list into the example variable `cpu_batch`.

```python
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

dataset = TensorDataset(torch.arange(24, dtype=torch.float32).reshape(6, 4))
loader = DataLoader(dataset, batch_size=2, pin_memory=True, num_workers=0)
model = nn.Linear(4, 2).to("cuda").eval()

with torch.inference_mode():
    for (cpu_batch,) in loader:          # Two samples, four features
        gpu_batch = cpu_batch.to("cuda", non_blocking=True)
        output = model(gpu_batch)        # Same-stream compute waits for copy
        # Keep results on the GPU while more GPU work needs them.
```

**`num_workers`** set to `0` loads in the main process, keeping this example simple. More
workers can help expensive input work, but use CPU resources and do not guarantee
better throughput. Calling **`pin_memory`** on every tensor in a hot loop can
add its own cost; **`DataLoader`** can manage pinning outside the model computation.

**Watch for:** do not modify a pinned source buffer while an asynchronous copy
is still using it. For an asynchronous D2H copy, wait for completion before
the CPU reads the destination. Avoid GPU → CPU → GPU round trips when all
subsequent computation can remain on the GPU.

**Performance connection:** a faster model cannot process a batch that is not
ready. Look for time spent preparing inputs, transferring bytes and waiting for
dependencies. More workers, pinned memory and nonblocking copies address
different stages; none guarantees that those stages overlap or run faster.

### Mental model

Input readiness, transfer and computation are separate stages. Nonblocking submission does not prove useful overlap.

## 13. Live tensors and reserved memory are different

### Objective

Explain allocated versus reserved CUDA memory and why clearing the cache cannot free live tensors.

### How it works

PyTorch's caching allocator manages reusable GPU memory blocks. Allocated
memory is memory currently occupied by tensors. Reserved memory includes
memory the allocator manages, including cached blocks available for reuse.
Reusing blocks can avoid repeatedly asking the device runtime for allocations.

The outer box below is the reserved pool; the inner regions divide it into
live allocations and cached capacity. The box sizes are schematic, not measured.
Other GPU memory, such as library allocations, can exist outside this pool.

![Live allocations occupy part of the reserved pool](reference/diagrams/allocator.svg)

**CUDA example:** values depend on the allocator, device and other live tensors.

```python
import torch

x = torch.ones(1024, device="cuda", dtype=torch.float32)
print(x.numel() * x.element_size())  # 4096 logical bytes for x
print(torch.cuda.memory_allocated())  # Live tensor bytes in this process
print(torch.cuda.memory_reserved())   # Allocator-managed bytes; may be larger

del x                     # Remove this reference; other aliases could retain it
torch.cuda.empty_cache()  # Release unused cached blocks, not live tensors
```

The counters are process/device observations, not the complete machine's usage.
`nvidia-smi` can show additional device-context and library memory. Do not expect
it to equal **`torch.cuda.memory_allocated`**.

**Common retention pattern:** appending each training `loss` tensor to a Python
list can keep its gradient history reachable. Keep only what later computation
needs. Calling **`detach`** on `loss` removes that history but still retains GPU
storage; calling **`item`** keeps a host scalar but may synchronize. The right choice depends
on whether you need future GPU work, later logging or neither.

Out of memory (OOM) means an allocation could not be satisfied. Check live
inputs, outputs, saved activations, gradients and optimizer state first. Calling
**`torch.cuda.empty_cache`** every iteration does not release them and can defeat useful reuse.

**Performance connection:** reduce unnecessary live references before trying to
shrink the reserved pool. Retained outputs and saved state consume capacity;
allocator reuse can avoid repeated allocation work. A lower reserved-memory
counter by itself is not evidence of a faster or more memory-efficient step.

### Mental model

Allocated memory holds live tensors. Reserved memory also holds reusable capacity. A cache clear cannot remove a live reference.

## 14. Batching expresses repeated work as tensor operations

### Objective

Replace independent per-row tensor work with an equivalent batched expression and explain the tradeoffs.

### How it works

Batching groups several inputs so an operation can process them together.
Vectorization expresses repeated work with operations on whole tensors instead
of a Python loop over individual entries or rows. They are related choices:
batching organizes the inputs, while vectorization changes how we express the
computation. Both describe choices made in the application code.

Consider three independent input vectors, each with two features. Each vector
is multiplied by the same weight matrix, receives the same bias and passes
through ReLU. Since no vector depends on another vector's result, we can put
them in a matrix and apply the same computation to all rows.

The diagram compares the requested work. The repeated boxes mean repeated
Python-level operations, not measured time or a guaranteed number of GPU kernels.
Both paths produce the same output **`shape`** and values for this example.

![Independent rows can share one batched expression](reference/diagrams/batching.svg)

**`torch.stack`** joins equal-shaped tensors along a new axis. Below it collects
three separate row results into a matrix so we can compare the two routes.
**`torch.testing.assert_close`** checks numerical agreement within tolerances;
it does not compare performance.

```python
import torch

x = torch.tensor([[-1., 2.], [3., -4.], [5., 6.]])  # [3, 2], FP32, CPU
w = torch.tensor([[1., 0.], [0., 2.]])              # [2, 2], FP32, CPU
bias = torch.tensor([1., -1.])                      # [2], FP32, CPU

rows = []
for row in x:                                     # Three Python iterations
    rows.append(torch.relu(row @ w + bias))       # Independent [2] result
by_row = torch.stack(rows)                        # Three [2] rows -> [3, 2]

batched = torch.relu(x @ w + bias)                # Work on all rows together
torch.testing.assert_close(batched, by_row)       # Check equivalent work
print(batched)                                   # [[0, 3], [4, 0], [6, 11]]
```

The batched expression removes repeated Python dispatch and lets matrix
multiplication operate on a larger input. It still requests matrix arithmetic,
addition and ReLU; one expression does not promise one kernel. Floating-point
algorithms may sum in different orders, so equivalent formulations need not be
bit-for-bit identical for arbitrary inputs.

**Watch for:** a loop that uses one iteration's result in the next has a data
dependency. Combining iterations indiscriminately can change the computation.
Some model behavior also depends on a batch's composition. The independent,
fixed-parameter example above deliberately avoids those cases.

Throughput is useful work completed per unit time. Latency is the time to finish
a particular request. A larger batch may improve throughput while using more
memory or making a request wait for other inputs to arrive. Keep the application's
latency and capacity limits visible; the next lesson explains timing boundaries.

**Performance connection:** replacing repeated tiny submissions with larger
tensor operations can reduce CPU overhead and expose more parallel work. Check
equivalence first, then measure useful work per unit time and request latency.
This tiny CPU example demonstrates the transformation, not a GPU speedup.

### Mental model

Group independent inputs, express their work together, and compare equivalent results before comparing cost.

## 15. A timer must include completion

### Objective

Explain which interval a CUDA event pair measures and what it excludes.

### How it works

Timing a Python call can measure mostly submission because CUDA work is
asynchronous. A CUDA event marks a position in a stream. Two timed events
measure the device interval between those positions after both have completed.
Warmup runs the same kind of work before measurement so one-time initialization
does not dominate the sample.

The diagram shows queue order. The start and end markers enclose repeated matrix
work. Their spacing is schematic; the host wait comes after the end marker is
submitted and is not an additional event-timing interval.

![Mark the device interval, then wait before reading it](reference/diagrams/timing.svg)

**CUDA example:** a timing pattern, with no claimed measurement or speedup.

```python
import torch

a = torch.randn(256, 256, device="cuda")
b = torch.randn(256, 256, device="cuda")
for _ in range(5):
    result = a @ b                       # Warm the same operation
torch.cuda.synchronize()                 # Finish earlier device work

start = torch.cuda.Event(enable_timing=True)
end = torch.cuda.Event(enable_timing=True)
repeats = 20
start.record()                          # Mark the current stream
for _ in range(repeats):
    result = a @ b
end.record()
end.synchronize()                       # Wait until this marker completes
ms_per_call = start.elapsed_time(end) / repeats
print(ms_per_call)                       # A measured average, in milliseconds
```

This interval excludes input construction, transfers before the start marker
and final printing. It can include gaps between operations while the CPU submits
work; it is not necessarily the sum of kernel execution times. Other streams or
processes can contend for the same GPU and affect the result.

For end-to-end latency, include all work the application actually needs.
A host wall-clock timer with appropriate completion waits can include preparation,
transfers and computation. Throughput measures useful work per unit time;
state the batch size and whether batching waits are included.

Repeat measurements and compare equivalent shapes, dtypes, devices and gradient
modes. Check result correctness. Five warmups here illustrate placement, not a
universal sufficient count. Compilation requires its own warmup and can recur
when input conditions change.

**Performance connection:** an optimization can improve the device interval
while leaving application latency unchanged if preparation or transfers dominate.
Choose the boundary that matches the user's experience, then compare repeated
measurements with the same work and completion rule.

### Mental model

A timing number is meaningful only with a boundary and a completion rule. Events and wall time answer different questions.

## 16. Code suggests a hypothesis; a profile supplies evidence

### Objective

Connect a tensor expression to operator/kernel evidence without assuming one line equals one kernel.

### How it works

An operator is a framework operation such as addition or matrix multiplication.
Profiles often show names such as `aten::add`; ATen is PyTorch's core tensor
operator library. The dispatcher selects implementations using tensor properties
and execution context. A CUDA implementation can call a GPU library or launch
kernels; some view operations need only metadata changes.

The diagram follows these layers. Arrows mean implementation selection and work
submission, not a guaranteed one-to-one mapping. The CPU-side operator interval
and device activity are different observations.

![Follow an expression through the execution layers](reference/diagrams/operator-evidence.svg)

```python
import torch

def transform(x, bias):
    shifted = x + bias              # Broadcast + element-wise addition
    positive = torch.relu(shifted)  # Element-wise activation
    return positive.mean(dim=-1)   # Reduction of the feature axis

x = torch.tensor([[-2., 0., 2.], [1., 2., 3.]])
bias = torch.ones(3)
print(transform(x, bias))           # tensor([1.3333, 3.0000])
```

In ordinary eager execution, operations are dispatched as the Python program
reaches them. The temporary `shifted` and `positive` values help us reason about
the requested computation. A profile can reveal the actual allocation and
execution behavior. The next lesson explains how compilation may change it.

| Question raised by the code | Evidence to look for |
| --- | --- |
| Are many small operations costly? | CPU submission gaps and kernel timeline; compare useful batch sizes. |
| Are intermediate values expensive? | Actual copies, allocations and memory traffic; investigate fusion. |
| Is a matrix product the main cost? | Operator time, shapes and selected kernel behavior. |
| Does **`item`** interrupt submission? | Host wait aligned with the required device work. |

Use a correct, unprofiled baseline first. PyTorch profiler connects operators
to activity; Nsight Systems shows the wider CPU/GPU timeline; Nsight Compute
inspects a selected kernel. Instrumentation can change timings. The
[tools course](../gpu-performance-tools/index.html) owns their detailed commands.
Prefer an appropriate library operation before writing a custom kernel, and
remeasure the unprofiled workload after any change.

**Performance connection:** select an optimization from observed cost, not from
the length of the source code. A wait, a copy and matrix arithmetic have different
causes and remedies. Use profiling to explain the baseline, then validate a
correct change with unprofiled measurements.

### Mental model

Tensor code describes requested work. Profiles reveal its implementation and timing; a plausible optimization still needs validation.

## 17. Compilation can reduce launches and intermediate storage

### Objective

Explain what compilation may change, why the first call differs, and how to check an optimized result.

### How it works

**`torch.compile`** returns an optimized callable that can capture regions of
tensor computation and produce an implementation for them. A computation graph
describes operations and their dependencies. A compiler backend turns captured
work into executable code. Capture and compilation have costs of their own;
they are not required to read or complete this course.

Fusion combines work that would otherwise require separate operations and
intermediate storage. For the function in lesson 16, addition produces `shifted`,
ReLU produces `positive`, and a mean reduces the last axis. An optimized
implementation may avoid materializing some intermediate tensors and reduce
launches. The exact grouping depends on the backend and inputs.

The diagram uses arrows for data dependencies. The lower box shows a possible
combined region, not a promise that all three operations become one kernel.
The final result still has to be produced.

![Compilation may avoid intermediate storage](reference/diagrams/compilation.svg)

**Compiler example:** continue with `transform`, `x` and `bias` from lesson 16
in an environment with a working compile backend. They remain FP32 CPU tensors;
moving computation to CUDA is a separate choice.

```python
# Continue with the function and fixed inputs from lesson 16.
compiled_transform = torch.compile(transform)    # Create optimized callable
reference = transform(x, bias)                   # Ordinary eager result
candidate = compiled_transform(x, bias)          # First call may compile
torch.testing.assert_close(candidate, reference) # Check acceptable agreement
# Time repeated steady-state calls separately from capture/compilation.
```

The first call can include capture, compilation and execution; later compatible
calls can reuse compiled work. Changes in input conditions, such as shapes,
can require another compilation. A one-off request may never recover the initial
cost. Warm up representative inputs and include startup cost when the application's
metric includes startup. Keep **`dtype`**, **`device`**, shapes and gradient mode equivalent
when comparing the eager and compiled paths.

A graph break splits captured computation. Unsupported Python behavior or
data-dependent branching can create such a boundary, reducing opportunities to
optimize across it. Ordinary execution may handle the intervening code and capture
may resume afterward. A graph break is different from producing an incorrect
result, and not every Python statement causes one. For GPU tensors, extracting
a host scalar can additionally require a completion wait, independently of
compilation behavior.

**Watch for:** compilation can change floating-point operation order. Use
**`torch.testing.assert_close`** with tolerances suitable for the workload, and
check the application's numerical quality requirements. Passing one small
example does not validate every input or establish a performance benefit.

**Performance connection:** compilation is worth investigating when repeated
dispatch, launches or intermediate memory traffic matter. Compare startup and
steady-state costs separately, confirm the result, and inspect actual execution
before claiming fusion or a speedup. Prefer an appropriate library operation
before considering a custom kernel.

### Mental model

Compilation changes the implementation, not the intended result. Account for preparation cost, validate output, then measure repeated work.

## 18. Read a complete step like a performance engineer

### Objective

Trace shapes, storage, computation and the host-visible result through a complete inference step.

### How it works

Bring the earlier ideas together in a small linear model. Every dimension is
deliberately small enough to reason about. There is no training update and no
benchmark claim. The diagram follows the output **`shape`** and then the transition
from a CUDA scalar to a Python number.

![Trace shape and device through one inference step](reference/diagrams/read-a-step.svg)

**CUDA example:** all operands for model computation are FP32 on the same GPU.

```python
import torch
from torch import nn

model = nn.Linear(4, 8).to("cuda").eval()
x = torch.ones(2, 3, 4, device="cuda")  # 24 FP32 elements = 96 bytes

with torch.inference_mode():
    y = model(x)             # [2, 3, 8]: six vectors, eight outputs each
    z = torch.relu(y)        # Same shape; a separate eager output tensor
    score = z.mean()        # []: reduce all 48 values to one CUDA scalar

value = score.item()        # Obtain a completed Python number on the CPU
print(value)               # Depends on the randomly initialized model
```

The model has `8 * 4 = 32` weight values and eight bias values. That is 160
logical parameter bytes in FP32. Each of `y` and `z` has 48 elements, or 192
logical bytes. `score` has one four-byte value. These are tensor payload counts,
not allocator reservations or a prediction of total GPU memory.

Read the program in four passes:

| Pass | What this example tells you |
| --- | --- |
| **`shape`** | `[2, 3, 4]` becomes `[2, 3, 8]`, stays that **`shape`**, then reduces to `[]`. |
| Data and state | Input and registered parameters are on CUDA; inference mode avoids a backward graph. |
| Work | **`nn.Linear`** requests matrix-style arithmetic, ReLU is element-wise, mean combines values. |
| Completion | **`item`** is the first explicit demand for a host-visible result. |

**What remains unknown:** kernel selection, whether submission or device work
dominates, and whether lower precision or compilation improves the application's
metric. Small shapes are excellent for learning but poor evidence about large
workloads. GPU Fundamentals explains the hardware; GPU Performance Optimization
teaches how to test these performance hypotheses.

This also revisits the common traps: a small output can require a large reduction;
one model call is not one kernel; disabling gradient tracking is separate from
evaluation mode; and a Python return does not necessarily mean GPU completion.

**Performance connection:** the six input vectors already form a batch. Larger
batches, fewer host reads, another **`dtype`** or compilation are possible changes,
each with different correctness, memory and latency implications. Choose one
based on evidence, keep the work comparable and check the application's metric.
Nothing in this small example establishes which change would help.

### Mental model

Read **`shape`**, state, work and completion in that order. Then choose the evidence needed to test a performance claim.
