"""secretsync — encrypted configuration management in Git.

A stdlib-first CLI wrapper around Mozilla SOPS + age that enforces a
secrets-in-version-control workflow: encrypt per environment, commit
only ciphertext, decrypt only in CI, rotate recipients with a grace
period, and keep a hash-chained audit trail.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
