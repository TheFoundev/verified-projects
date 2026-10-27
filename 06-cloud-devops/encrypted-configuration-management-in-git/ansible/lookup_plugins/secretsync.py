"""secretsync lookup plugin — community.sops-style, backed by the CLI.

    vars:
      app_secrets: "{{ lookup('secretsync', 'ansible/secrets/dev.secrets.enc.yaml', env='dev') }}"

The lookup runs `python3 -m secretsync decrypt`, so the CI gate
(`decrypt_policy`) and the audit trail apply exactly as they do from the
command line.  Outside CI it fails with the policy's refusal reason
unless `decrypt_policy.enforce_local_deny` is switched off.

Requires ansible-core at runtime (this example is source-only in the
Foundev environment, where ansible is not installed).
"""

from __future__ import annotations

import subprocess
import sys

from ansible.errors import AnsibleLookupError
from ansible.plugins.lookup import LookupBase

try:  # ansible-core >= 2.19 exposes the singleton as a Display instance
    from ansible.utils.display import Display

    display = Display()
except ImportError:  # pragma: no cover - older ansible-core
    from ansible.utils.display import display


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):  # noqa: ANN001
        if not terms:
            raise AnsibleLookupError("secretsync lookup needs a ciphertext file path")
        env_name = kwargs.get("env")
        if not env_name:
            raise AnsibleLookupError(
                "secretsync lookup requires env=<environment> "
                "(e.g. env='dev')"
            )
        root = kwargs.get("root")
        python_bin = "python3"
        if variables:
            python_bin = variables.get("ansible_playbook_python", "python3")

        results = []
        for term in terms:
            cmd = [python_bin, "-m", "secretsync", "decrypt", str(term), "--env", str(env_name)]
            if root:
                cmd += ["--root", str(root)]
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    text=True,
                )
            except OSError as exc:  # pragma: no cover - exotic runners
                raise AnsibleLookupError(f"secretsync lookup could not run: {exc}") from None
            if proc.returncode != 0:
                raise AnsibleLookupError(
                    f"secretsync decrypt failed for {term} (exit {proc.returncode}): "
                    f"{proc.stderr.strip()}"
                )
            display.vvvv(f"secretsync: decrypted {term} for env {env_name}")
            results.append(proc.stdout)
        return results
