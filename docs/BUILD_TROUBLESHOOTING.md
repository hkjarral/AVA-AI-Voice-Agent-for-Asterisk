# Docker build checks and recovery

These source changes are unreleased. Older checkouts, including v7.7.1, need
the [legacy recovery commands](CLI_TOOLS_GUIDE.md#standalone-compose-and-older-helpers).

## Check before building

Run `./preflight.sh` for system, resource, permissions, network and PBX checks.
It is read-only by default; review suggested changes before explicitly running
`sudo ./preflight.sh --apply-fixes`. No build check edits Docker configuration,
creates an insecure builder, restarts Docker or pulls base images.

Run this as the user who will install/update AVA:

```bash
./preflight.sh --build-check
```

The JSON report contains builder, driver, context, UID, engine ID, node status
and `connectivity: not-probed`. It checks Docker access, the actual builder,
Compose `build --print`, and Bake `--allow`/`--load` capabilities. A stopped
container builder is reported without bootstrapping it. A real build is still
required to verify registry access, package downloads and image loading.

`sudo` can change HOME, Docker credentials, context, socket and builder settings.
A successful root check does not qualify the login user. `install.sh` consumes a
fresh preflight build report under its own identity and stops on failure.
Full preflight retains its existing CPU/RAM/disk, permissions and runtime checks;
build readiness alone does not qualify a PBX, GPU or complete fresh installation.

## One builder for each operation

AVA uses the built-in `default` builder attached to the active Docker context.
It ignores a globally selected custom builder unless `BUILDX_BUILDER` explicitly
selects it. The choice applies to Compose, export and Bake retries, CLI updates,
Admin UI builds, updater-image builds and packaged rollback. Global builder
selection is unchanged. Docker endpoint/context overrides remain in force.

To intentionally use a custom builder:

```bash
BUILDX_BUILDER=my-builder ./scripts/compose-build.sh admin_ui
```

That builder must be available to the executing identity. Admin/updater containers
have their own Docker client configuration; a host-only named builder is not
automatically available inside them. An unavailable explicit builder fails with
instructions instead of silently switching. For recurring GUI use, configure the
override in its environment and make the builder configuration accessible there;
otherwise retain `default`. Never copy registry credentials into an issue report.

Build explicit services before starting them. Preserve project and Compose
overlays on both commands. Compose plugin and standalone Compose 2+ are supported.
For example, with the plugin:

```bash
./scripts/compose-build.sh -p asterisk-ai-voice-agent admin_ui &&
  docker compose -p asterisk-ai-voice-agent up -d --no-build --pull never admin_ui
```

The shared runner retries only the exact Compose host-network entitlement error.
It exports the same services/options, validates the exact target set, and runs
Bake with `--allow=network.host --load` and explicit targets. It does not retry
unrelated errors, grant `security.insecure`, or recreate containers after failure.
Engine changes still require the updater's active-call checks and recovery journal.

## Diagnose the failing layer

| Failure | Check / recovery |
| --- | --- |
| `additional privileges requested ... --allow=network.host` | Use the helper; do not pass this flag to `compose up`. Upgrade Compose/Buildx if capability checks fail. |
| `load metadata`, `FROM`, `lookup registry-1.docker.io: i/o timeout` | BuildKit daemon DNS/proxy/egress. A custom `docker-container` builder has its own network. Build-step host networking does not change it. |
| `RUN apt-get/pip/npm`, `Temporary failure resolving` | Build-step DNS, proxy, repository availability, TLS and egress. Read the actual failing command. |
| Unauthorized / rate limited | Registry login/credentials and pull limits for the executing identity. Entitlement retries do not repair these. |
| No space left / disk quota | `docker system df` and filesystem free space. Remove only operator-approved unused resources; avoid blanket prune commands. |
| Permission denied / daemon unavailable | Socket access and actual Docker endpoint/context. Recheck as the installing user; do not assume sudo uses the same daemon. |

After diagnosing a custom builder failure, explicitly test the built-in builder
with `BUILDX_BUILDER=default ./scripts/compose-build.sh admin_ui` (or the selected
service). The same policy applies to `ai_engine`; drain calls before recreation.
Manual `docker pull python:...` / `docker pull node:...` commands are unnecessary.
A host lookup or successful pull proves only that path, not custom BuildKit DNS.

If network configuration needs repair, preserve existing daemon/BuildKit settings,
use resolvers/proxies approved for that environment, validate the merged config,
and schedule any restart with the operator. Never overwrite `daemon.json` with a
generic DNS snippet or restart the host's running containers as an installation fix.

Docker contracts: [builder/context selection](https://docs.docker.com/build/builders/),
[Compose build/export](https://docs.docker.com/reference/cli/docker/compose/build/),
[Bake entitlements and image loading](https://docs.docker.com/reference/cli/docker/buildx/bake/).
