# Container Runtime from Scratch

Plan and record Linux namespace and cgroup isolation steps.

> A workshop that rebuilds Docker from scratch to teach Linux namespaces and cgroups used by containers.

## Overview

An original, self-contained implementation of the **Container Runtime from Scratch** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- Container spec with root filesystem, network, memory and cpu limits
- An ordered plan of namespace, mount and cgroup operations, where every step
  is a dataclass with the arguments it needs, so the plan is a program
- The namespace mask of the container, the way the kernel takes it
- A generated rootfs with the layout of an image and a shell entry point
- The cgroup limits really written into cgroup v2 when the rights are there
- The command really started inside the namespaces when the rights are there
- A dry run, so the project always shows what it would do

## Stack

`Python; Linux namespaces; cgroups`

The CSV stack is Python + Linux namespaces + cgroups. Both need rights, so
the runtime checks them: with the rights it writes the cgroup limits and
starts the command inside the namespaces, without them it writes the plan
down and says why nothing ran.

## Run

```bash
pip install -r requirements.txt
python main.py run
python main.py demo
pytest -q
```

## Structure

```text
container-runtime-from-scratch/
├── main.py                 pipeline CLI
├── src/pipeline.py         data pipeline
├── tests/test_pipeline.py  smoke test
├── requirements.txt
├── runtime.py              container planner library
```

## Testing

```bash
pytest -q
```

13 tests: the namespaces and the mask, the plan and the steps it has to
have, the cgroup limits, the rootfs layout, the command, the cgroup writes
and the run.

## Notes and limitations

- The namespaces need root or user namespaces. Without them the runtime
  writes the plan down and says what it would have done.
- The rootfs holds a script, not a real distribution: there is no dynamic
  loader and no libc, so a real binary cannot be started inside it.
- There is no network setup: a container without a network namespace has no
  interface at all, and there is no veth or bridge.
- The cgroup limits are written when cgroup v2 is mounted and the directory
  can be written; otherwise the answer says which limit was refused.

## License

MIT. See `LICENSE`.

## About the scope

This is a third-year student project, not a production data platform. It uses local files and SQLite so the pipeline can run on one machine. The notes above list the production pieces that were replaced or skipped.
