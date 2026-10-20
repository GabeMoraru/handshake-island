# 0003. Keep the networkless sandbox and the stdio worker

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

The game loop handles model output all day: tool inputs, speech and provider content blocks. The worker never executes it, and the kernel validates every act, so the main defence is that model output stays inert data. Two risks remain: a kernel or loop bug reachable from crafted JSON, and API keys sharing a process with the code that handles model text. The earlier project already had a container sandbox (Apple Container on macOS, Docker or Podman on Linux) with an isolation probe and negative controls in CI.

## Decision

1. **The game runs in the container.** The kernel and the game loop run in the worker, inside a container with:
   - no network, a read-only root, a non-root user and no capabilities;
   - no credentials, and only the game's directory mounted;
   - limits on CPU, memory and scratch space.

   The worker uses only the standard library and refuses to run if its isolation probe fails.
2. **The broker is the only egress.** The worker asks for model calls over stdio, and the host broker makes them. The host relay refuses more than 3 attempts per resident turn and 162 calls per game. The broker pins each seat's model from the config, checks the spend ledger and calls fixed URLs with keys that only the host holds.
3. **One container and one broker per game.** Parallel games run in parallel containers.
4. **CI exercises the boundary.** It builds the image, probes it under Docker and Podman, runs a scripted game in the worker, and requires the probe to catch each weakened control.

## Consequences

- API keys never share a process with the code that parses model output.
- Each call carries the whole conversation across stdio, which is small (about 22k tokens at most) and capped at 8 MB a line.
- The image, setup scripts and CI job are maintained, and container start-up adds seconds per game.
- Linux engines share the host kernel. The threat model names gVisor or a VM as the step up if the worker ever runs untrusted code.
