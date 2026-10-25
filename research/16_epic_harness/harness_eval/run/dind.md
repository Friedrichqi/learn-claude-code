# Docker-in-Docker mode (from 2026-10-12 ~18:30 UTC, the Qwen phase onward)

Docker's root disk on this machine (`/`, ~33 GB usable, shared with other users) limited how many SWE-bench / Pro
task images could be present at once. That made disk, not the GPUs, the bottleneck, and caused four disk-guard
kills. Rootless Docker would need `uidmap` (newuidmap/newgidmap) and an AppArmor userns profile, both of which
require an admin. So task containers now run in our own Docker daemon inside one privileged container on the
host daemon, with its data root on `/data` (local XFS, 17 TB free).

| item | value |
|---|---|
| container | `s15-dind` (image `docker:29.4.3-dind`, same version as the host daemon), privileged, its own network namespace on the host's default bridge (the inner daemon's iptables rules never touch the host's) |
| inner data root | `/data/fritz/docker` (owned by root: delete it through a container, see teardown) |
| socket | `/data/fritz/dind-run/docker.sock`, mode 660, group 988 (docker), in a mode-700 directory. **No TCP listener.** The image's entrypoint adds `tcp://0.0.0.0:2375` unless the command starts with `dockerd`, so it does. |
| inner networks | default bridge 10.213.0.0/16, compose networks from 10.214.0.0/15 (/24 each), MTU 1460 (GCP), nofile 1048576 (as the host daemon) |
| project dir | `/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval` mounted at the same path, so Harbor's compose bind mounts resolve inside the inner daemon |

Start (once):

    docker run -d --privileged --name s15-dind \
      -v /data/fritz/docker:/var/lib/docker -v /data/fritz/dind-run:/run/dind \
      -v /data/fritz/learn-claude-code/research/16_epic_harness/harness_eval:/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval -e DOCKER_TLS_CERTDIR= docker:29.4.3-dind \
      dockerd --host=unix:///run/dind/docker.sock --group=988 --storage-driver=overlay2 --mtu=1460 \
      --bip=10.213.0.1/16 --default-address-pool base=10.214.0.0/15,size=24 --default-ulimit nofile=1048576:1048576

Then clamp TCP MSS to the path MTU on forwarded connections (added 2026-10-15, e4):

    docker exec s15-dind iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

`--mtu` sets only the inner default bridge. Harbor's compose networks (`<trial>__env_default`) and their containers get
MTU 1500 behind the dind container's eth0 at 1460, so TLS downloads can stall. In e4's oracle check, before the clamp,
3 of 33 verifiers failed to download `uv` from GitHub ("curl: (28) SSL connection timeout") and scored 0. e1-e3's 2,347
verifier logs had none. With the clamp, the same download from a fresh 1500-MTU network succeeded 3 of 3.

Use: export `S15_DOCKER_SOCK=/data/fritz/dind-run/docker.sock S15_DOCKER_ROOT=/data/fritz/docker
S15_KEEP_IMAGES=1` before `run/batch.py` / `run/qwen_phase.py`. With those:
- `run/eval.sh` mounts the inner socket into the eval container, which itself still runs on the host daemon;
- `run/images.py` measures free space on `/data` and keeps images: no snapshot, cleanup or sweep;
- the disk governor is not used (it watches `/`), and `run/state/slots.json` is set by hand.

Teardown (end of study):

    docker exec s15-dind sh -c 'docker -H unix:///run/dind/docker.sock ps -aq | xargs -r docker -H unix:///run/dind/docker.sock rm -f'
    docker stop s15-dind && docker rm s15-dind
    docker run --rm -v /data/fritz:/p alpine:3.23 rm -rf /p/docker /p/dind-run
    docker rmi docker:29.4.3-dind

Results are comparable with the host-daemon runs: the same images and resource limits (cgroup v2), and the same
Harbor and adapter versions; only where the containers run changed. The profile tables do not split by mode.
