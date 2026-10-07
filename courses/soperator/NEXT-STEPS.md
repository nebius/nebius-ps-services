# Where to Go Next

Choose an optional direction that matches the work you want to do next.

- **Continue with a GPU course**

  Use the course switcher to read GPU Performance Tools, then
  [PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html)
  before GPU Fundamentals.
  The tools reference introduces the profiler commands and evidence used in labs.
  Follow the
  [shared environment setup](../lab-guide.html#lab-preparation-scripts), then use its lab
  guides for exact resource requests and performance investigations.
  This introduction explains the scheduler behavior behind those commands.

- **Explore job arrays**

  For larger collections of similar independent jobs, study Slurm job arrays:
  an array associates indexed instances with one submission, and a concurrency
  limit bounds simultaneous execution. Ask how input selection and output naming
  must change so instances do not overwrite one another.

- **Coordinate dependent jobs**

  For pipelines, study Slurm dependencies: a downstream job can wait for an
  upstream outcome instead of using a shell polling loop. Ask when successful
  process exit is sufficient and when an application artifact must also be checked.

- **Read the administrator references**

  For cluster administrators, the versioned Soperator API and Helm chart sources
  explain deployment options and reconciliation. Reading them does not require
  changing a cluster. Keep user job investigation separate from administrative
  changes to workers, partitions or accounts. Public references follow below.
