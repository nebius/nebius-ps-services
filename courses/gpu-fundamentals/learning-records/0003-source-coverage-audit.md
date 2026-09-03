# Source coverage audit

The second-pass audit confirmed that execution geometry needed a concrete
Python kernel exercise. The course now includes a masked Triton program and
element-tile sweep. The final review keeps covered element lanes distinct from
the physical warp team selected by `num_warps`. Architecture-specific TMA/DSM
work remains explicitly advanced and outside the required Python/PyTorch path.
