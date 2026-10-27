# Ansible integration

Two documented ways to consume the encrypted environment files.  Both
end up at the same CLI gate — `python3 -m secretsync decrypt` — so the
CI check and the audit entry are identical no matter which one you pick.

> ansible-core is **not** installed in the Foundev environment, so these
> examples are source-only and were validated structurally (Python
> compiled, ciphertext files verified) rather than executed.

## 1. Task-based decryption (main path, used by `playbook.yml`)

```bash
# in CI: CI=true and SECRETSYNC_RUN_ID are exported by the workflow
python3 -m secretsync decrypt ansible/secrets/dev.secrets.enc.yaml \
    --env dev --output "$RUNNER_TEMP/dev.dec.yaml"

ansible-playbook -i ansible/inventory/dev.yml ansible/playbook.yml
```

`playbook.yml` wraps exactly that flow:

1. a pre-task **asserts the CI gate** (`CI` + `SECRETSYNC_RUN_ID`),
2. a `command` task runs `secretsync decrypt --output <tmp>`,
3. `include_vars` loads the plaintext the same way the
   [`community.sops`](https://github.com/ansible-collections/community.sops)
   collection would load decrypted vars,
4. an `always:` block shreds the decrypted file.

## 2. Lookup plugin (community.sops-style)

`lookup_plugins/secretsync.py` exposes a lookup that shells out to the
CLI:

```yaml
- debug:
    msg: >-
      db host is {{ lookup('secretsync',
      playbook_dir ~ '/secrets/' ~ inventory_environment ~ '.secrets.enc.yaml',
      env=inventory_environment) }}
```

Point Ansible at the plugin directory with
`ANSIBLE_LOOKUP_PLUGINS=./ansible/lookup_plugins` (or copy it into
`~/.ansible/plugins/lookup/`).

## Layout

```text
ansible/
├── playbook.yml            decrypt → include_vars → use → shred
├── inventory/              one inventory per environment
│   ├── dev.yml
│   ├── staging.yml
│   └── prod.yml
├── lookup_plugins/
│   └── secretsync.py       community.sops-style lookup on the CLI
└── secrets/                ciphertext only (rotated by secretsync)
    ├── dev.secrets.enc.yaml
    ├── staging.secrets.enc.yaml
    └── prod.secrets.enc.yaml
```

## Rotation

```bash
python3 -m secretsync rotate --env prod          # re-key, keep grace
python3 -m secretsync rotate --env prod --drop-previous   # end grace
```

`rotate` re-encrypts every file listed for that environment in
`.sops.yaml` (including the three files in `ansible/secrets/`), moves
the retired recipient into `previous_recipients`, and appends one audit
event per file.
