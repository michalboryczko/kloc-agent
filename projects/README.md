# projects/

Operator-owned directory of project source trees that the runner-side
`read_project_file` Strands tool serves to the orchestrator.

## Seeding

The `projects-init` compose sidecar copies every subtree under
`./projects/` into the `kloc-projects` named volume on first boot. Each
runner container mounts the volume read-only at `/projects` so the
orchestrator can read `/projects/<project_name>/<path>` on demand.

Drop a project tree into a subdirectory keyed by the name the analyst
will use (`./projects/kyc/`, `./projects/order-api/`, …). A symlink
target is fine; the sidecar's `cp -R` resolves symlinks and writes a
frozen snapshot into the volume.

## Re-seeding

The volume is sticky across restarts. To pick up new contents after
editing `./projects/<name>/`:

```bash
docker compose down -v
docker compose up -d
```

The `down -v` removes the named volume so `projects-init` re-runs `cp -R`
from `./projects/` on the next boot. Without `-v` the existing volume
content survives the restart unchanged.

## Naming

Project subdirectory names must match `^[a-z][a-z0-9-]{0,63}$`; the
runner-side tool rejects every other shape before touching the
filesystem.
