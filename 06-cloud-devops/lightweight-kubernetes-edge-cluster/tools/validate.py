#!/usr/bin/env python3
"""Structural validation gate for the Lightweight Kubernetes Edge Cluster repo.

This environment has no k3s, kubectl, helm or docker, so we cannot render the
chart or talk to a cluster. Instead this script performs the checks that *can*
run anywhere:

  1. every YAML under helm/, manifests/, k3s/ parses (systemd .conf excluded);
  2. the Helm chart has a valid structure (Chart.yaml, values, required kinds);
  3. local-path StorageClass references and volumeClaimTemplates are present;
  4. no secrets are committed (private keys, AWS keys, literal node-tokens,
     non-placeholder passwords);
  5. every bootstrap/*.sh and systemd/* file referenced from the README and
     Makefile actually exists;
  6. no unguarded destructive commands (rm -rf /, mkfs, dd of=/dev/...).

Exit code is 0 on PASS and 1 on FAIL. Stdlib only, plus PyYAML.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is expected to be installed
    print("FATAL: PyYAML is required but not installed")
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent

# Directories whose *.yaml / *.yml files must parse. systemd/ is included but
# its *.service / *.timer / *.conf files are skipped (not YAML).
YAML_DIRS = ["helm", "manifests", "k3s", "systemd"]
YAML_EXTS = {".yaml", ".yml"}

CHART_DIR = ROOT / "helm" / "edge-stateful"

# Kinds the chart must render at least once.
REQUIRED_CHART_KINDS = {"StatefulSet", "Service", "PodDisruptionBudget"}

# Files that must never contain committed secrets.
SECRET_SCAN_DIRS = ["bootstrap", "helm", "manifests", "k3s", "systemd", "tools", "docs"]
SECRET_SCAN_EXTS = {".sh", ".yaml", ".yml", ".tpl", ".txt", ".md", ".conf", ".service", ".timer"}

PASS = "PASS"
FAIL = "FAIL"

results: list[tuple[str, str, str]] = []  # (status, check, detail)


def record(status: str, check: str, detail: str = "") -> None:
    results.append((status, check, detail))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# Matches a Helm template action: {{ ... }}, including the whitespace-trim
# markers {{- ... -}} and comment blocks {{/* ... */}}.
HELM_ACTION_RE = re.compile(r"\{\{-?.*?-?\}\}", re.DOTALL)
HELM_COMMENT_RE = re.compile(r"\{\{/\*.*?\*/\}\}", re.DOTALL)


def strip_helm(text: str) -> str:
    """Remove Helm template actions so the surrounding skeleton parses as YAML.

    This is NOT a renderer: conditional blocks that would disappear are left
    behind as their literal bodies, which can look odd but still lets us check
    the document skeleton (kinds, keys, structure). Good enough for a
    structural gate when no `helm` binary is available.

    Actions that occupy a whole line are dropped entirely; actions embedded
    inline within a line are replaced with a placeholder scalar so that flow
    collections like `["a", {{ x }}, "b"]` remain syntactically valid.
    """
    text = HELM_COMMENT_RE.sub("", text)

    def repl(match: "re.Match[str]") -> str:
        action = match.group(0)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end == -1:
            line_end = len(text)
        before = text[line_start:match.start()]
        after = text[match.end():line_end]
        if before.strip() == "" and after.strip() == "":
            return ""  # whole-line action: drop it
        return "__helm_value__"

    return HELM_ACTION_RE.sub(repl, text)


def walk(base: Path, exts: set[str]) -> list[Path]:
    out: list[Path] = []
    if not base.exists():
        return out
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix in exts:
                out.append(p)
    return sorted(out)


# ---------------------------------------------------------------------------
# 1. YAML parses
# ---------------------------------------------------------------------------
def check_yaml_parses() -> None:
    files = []
    for d in YAML_DIRS:
        files.extend(walk(ROOT / d, YAML_EXTS))
    files = sorted(set(files))

    if not files:
        record(FAIL, "yaml-parse", "no YAML files found under " + ", ".join(YAML_DIRS))
        return

    bad = []
    for f in files:
        text = read_text(f)
        # Helm templates are not valid YAML on their own; strip the Go
        # templating actions before parsing so we validate the skeleton.
        if "helm" in f.relative_to(ROOT).parts:
            text = strip_helm(text)
        try:
            list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            bad.append(f"{f.relative_to(ROOT)}: {exc}")

    if bad:
        record(FAIL, "yaml-parse", f"{len(bad)} file(s) failed to parse: " + "; ".join(bad))
    else:
        record(PASS, "yaml-parse", f"{len(files)} YAML file(s) parsed cleanly")


# ---------------------------------------------------------------------------
# 2. Helm chart structure
# ---------------------------------------------------------------------------
def chart_docs() -> tuple[list[dict], list[str]]:
    """Return (parsed docs from templates, error strings)."""
    docs: list[dict] = []
    errors: list[str] = []
    tpl_dir = CHART_DIR / "templates"
    for f in walk(tpl_dir, {".yaml", ".yml"}):
        try:
            for doc in yaml.safe_load_all(strip_helm(read_text(f))):
                if isinstance(doc, dict):
                    docs.append(doc)
        except yaml.YAMLError as exc:
            errors.append(f"{f.relative_to(ROOT)}: {exc}")
    return docs, errors


def check_chart_structure() -> None:
    errors: list[str] = []

    chart_yaml = CHART_DIR / "Chart.yaml"
    if not chart_yaml.exists():
        errors.append("Chart.yaml missing")
    else:
        try:
            meta = yaml.safe_load(read_text(chart_yaml)) or {}
            if not str(meta.get("apiVersion", "")).startswith("v2"):
                errors.append("Chart.yaml apiVersion must be v2")
            if not meta.get("name"):
                errors.append("Chart.yaml name missing")
            if not meta.get("version"):
                errors.append("Chart.yaml version missing")
        except yaml.YAMLError as exc:
            errors.append(f"Chart.yaml does not parse: {exc}")

    for required in ("values.yaml",):
        if not (CHART_DIR / required).exists():
            errors.append(f"{required} missing")

    if not (CHART_DIR / "templates").exists():
        errors.append("templates/ directory missing")

    if errors:
        record(FAIL, "chart-structure", "; ".join(errors))
        return

    docs, doc_errors = chart_docs()
    if doc_errors:
        record(FAIL, "chart-structure", "template parse errors: " + "; ".join(doc_errors))
        return

    kinds = {str(d.get("kind", "")) for d in docs if d.get("kind")}
    missing = REQUIRED_CHART_KINDS - kinds
    if missing:
        record(FAIL, "chart-structure",
               f"templates missing required kind documents: {sorted(missing)}")
    else:
        record(PASS, "chart-structure",
               f"Chart.yaml valid, {len(docs)} template doc(s), kinds={sorted(kinds)}")


# ---------------------------------------------------------------------------
# 3. local-path + volumeClaimTemplates
# ---------------------------------------------------------------------------
def check_local_path_and_vct() -> None:
    chart_docs_list, _ = chart_docs()

    # Every storageClassName that appears in chart templates must be local-path.
    bad_sc: list[str] = []
    vct_count = 0
    statefulsets = 0
    for doc in chart_docs_list:
        kind = doc.get("kind")
        if kind == "StatefulSet":
            statefulsets += 1
            spec = doc.get("spec", {}) or {}
            vcts = spec.get("volumeClaimTemplates") or []
            if vcts:
                vct_count += len(vcts)
                for vct in vcts:
                    sc = ((vct or {}).get("spec", {}) or {}).get("storageClassName")
                    # Templates may express the StorageClass through a Helm
                    # value that our stripper renders as __helm_value__; accept
                    # that as long as values.yaml pins local-path (checked below).
                    if sc == "__helm_value__":
                        continue
                    if sc != "local-path":
                        bad_sc.append(f"StatefulSet {doc.get('metadata', {}).get('name')} sc={sc!r}")
        if kind == "PersistentVolumeClaim":
            sc = (doc.get("spec", {}) or {}).get("storageClassName")
            if sc != "local-path":
                bad_sc.append(f"PVC {doc.get('metadata', {}).get('name')} sc={sc!r}")

    # Textual check: values.yaml should reference local-path for storage.
    values_text = read_text(CHART_DIR / "values.yaml") if (CHART_DIR / "values.yaml").exists() else ""
    if "local-path" not in values_text:
        bad_sc.append("values.yaml does not mention local-path")

    errors = []
    if statefulsets == 0:
        errors.append("no StatefulSet documents found in chart templates")
    if vct_count == 0:
        errors.append("no volumeClaimTemplates found in chart StatefulSets")
    if bad_sc:
        errors.append("non-local-path storage references: " + "; ".join(bad_sc))

    if errors:
        record(FAIL, "local-path-storage", "; ".join(errors))
    else:
        record(PASS, "local-path-storage",
               f"{statefulsets} StatefulSet(s), {vct_count} volumeClaimTemplate(s), all use local-path")


# ---------------------------------------------------------------------------
# 4. Secret scan
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    ("private-key", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("literal-node-token", re.compile(r"node-token:\s*(?![\"']?\$\{)(?![\"']?<)(?![\"']?$)\S+")),
    ("literal-k3s-secure-token", re.compile(r"\bK10[0-9a-fA-F]{40,}")),
]

# password: with a value that is neither a placeholder nor a reference.
PASSWORD_RE = re.compile(
    r"password:\s*(?P<val>.+?)\s*$",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"^\s*[\"']?(?:"
    r"\$\{[^}]*\}|<[^>]*>|\{\{[^}]*\}\}|"
    r"changeme|change-me|REPLACE|placeholder|example|redacted|"
    r"\"\"|''|\"|\s*$"
    r")",
    re.IGNORECASE,
)


def check_secrets() -> None:
    files: list[Path] = []
    for d in SECRET_SCAN_DIRS:
        files.extend(walk(ROOT / d, SECRET_SCAN_EXTS))
    files.append(ROOT / "README.md")
    files.append(ROOT / "Makefile")

    findings: list[str] = []
    for f in sorted(set(files)):
        if not f.exists() or f.is_dir():
            continue
        rel = f.relative_to(ROOT)
        text = read_text(f)
        for i, line in enumerate(text.splitlines(), start=1):
            for label, pat in SECRET_PATTERNS:
                if pat.search(line):
                    findings.append(f"{rel}:{i} [{label}]")
            m = PASSWORD_RE.search(line)
            if m:
                val = m.group("val")
                # A b64enc / env / secretKeyRef reference is fine.
                if "b64enc" in val or "secretKeyRef" in val or "valueFrom" in val:
                    continue
                if not PLACEHOLDER_RE.match(val):
                    findings.append(f"{rel}:{i} [password-with-literal-value]")

    # Also assert no obvious secret *files* are tracked.
    for pattern in ("*.token", "secrets/*", ".kubeconfig"):
        for hit in ROOT.glob(pattern):
            findings.append(f"tracked secret file: {hit.relative_to(ROOT)}")

    if findings:
        record(FAIL, "secret-scan", f"{len(findings)} finding(s): " + "; ".join(findings[:10]))
    else:
        record(PASS, "secret-scan",
               f"scanned {len(set(files))} file(s); no committed secrets detected")


# ---------------------------------------------------------------------------
# 5. Referenced files exist
# ---------------------------------------------------------------------------
def extract_refs(text: str) -> set[str]:
    """Find bootstrap/ and systemd/ path references in prose.

    Handles plain paths and brace-glob shorthand like
    `systemd/k3s-backup.{service,timer}`, which is expanded into two refs.
    """
    refs: set[str] = set()
    pat = re.compile(r"(?<![\w./-])(?:\./)?((?:bootstrap|systemd)/[\w./{},-]+)")
    for m in pat.finditer(text):
        ref = m.group(1).rstrip(".,:;)`'\"")
        brace = re.match(r"^(.*)\{([^}]+)\}(.*)$", ref)
        if brace:
            prefix, options, suffix = brace.groups()
            for opt in options.split(","):
                refs.add(f"{prefix}{opt.strip()}{suffix}")
        else:
            refs.add(ref)
    return refs


def check_referenced_files() -> None:
    docs = [ROOT / "README.md", ROOT / "Makefile"]
    docs += walk(ROOT / "docs", {".md"})
    docs += [CHART_DIR / "README.md"]

    missing: list[str] = []
    referenced: set[str] = set()
    for d in docs:
        if not d.exists():
            continue
        for ref in extract_refs(read_text(d)):
            # Only validate concrete file references (not directories/globs).
            if ref.endswith("/") or "*" in ref:
                continue
            # A real file reference has an extension (foo.sh, backup.service);
            # bare words like "bootstrap/systemd" from prose are not files.
            if "." not in os.path.basename(ref):
                continue
            referenced.add(ref)
            if not (ROOT / ref).exists():
                missing.append(f"{d.relative_to(ROOT)} -> {ref}")

    if missing:
        record(FAIL, "referenced-files", "dangling references: " + "; ".join(sorted(set(missing))))
    else:
        record(PASS, "referenced-files",
               f"{len(referenced)} referenced bootstrap/systemd path(s) all exist")


# ---------------------------------------------------------------------------
# 6. Destructive-command guard
# ---------------------------------------------------------------------------
DESTRUCTIVE_PATTERNS = [
    ("rm-rf-root", re.compile(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+/\s*(?:$|\s)")),
    ("rm-rf-root-glob", re.compile(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+/\*")),
    ("mkfs", re.compile(r"\bmkfs(?:\.\w+)?\b")),
    ("dd-to-device", re.compile(r"\bdd\b[^\n]*\bof=/dev/")),
    ("wipefs", re.compile(r"\bwipefs\b")),
]


def check_no_unguarded_destructive() -> None:
    sh_files = walk(ROOT / "bootstrap", {".sh"}) + walk(ROOT / "systemd", {".sh"})
    findings: list[str] = []
    for f in sh_files:
        rel = f.relative_to(ROOT)
        text = read_text(f)
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # comments describing commands are fine
            for label, pat in DESTRUCTIVE_PATTERNS:
                if pat.search(line):
                    # Allowed if the file clearly gates on --yes / confirmation.
                    findings.append(f"{rel}:{i} [{label}] {stripped[:60]}")

    if findings:
        record(FAIL, "destructive-guard", "unexpected destructive patterns: " + "; ".join(findings))
    else:
        # Confirm the uninstall script actually has the confirmation gate.
        uninstall = ROOT / "bootstrap" / "99-uninstall.sh"
        gated = uninstall.exists() and "--yes" in read_text(uninstall) and "CONFIRM" in read_text(uninstall)
        if not gated:
            record(FAIL, "destructive-guard",
                   "99-uninstall.sh does not appear to gate uninstall behind --yes")
        else:
            record(PASS, "destructive-guard",
                   f"{len(sh_files)} script(s) scanned; destructive ops gated behind --yes")


# ---------------------------------------------------------------------------
# 7. Shell scripts exist and are executable-ish (structural presence)
# ---------------------------------------------------------------------------
EXPECTED_BOOTSTRAP = [
    "00-preflight.sh",
    "10-install-server.sh",
    "11-install-server-join.sh",
    "20-install-agent.sh",
    "30-token-helper.sh",
    "40-verify-cluster.sh",
    "99-uninstall.sh",
]


def check_expected_layout() -> None:
    missing = [n for n in EXPECTED_BOOTSTRAP if not (ROOT / "bootstrap" / n).exists()]
    if missing:
        record(FAIL, "layout", f"missing bootstrap scripts: {missing}")
        return
    record(PASS, "layout", f"all {len(EXPECTED_BOOTSTRAP)} bootstrap scripts present")


# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 70)
    print("Lightweight Kubernetes Edge Cluster - validation gate")
    print("=" * 70)
    print(f"repo root: {ROOT}")
    print()

    check_expected_layout()
    check_yaml_parses()
    check_chart_structure()
    check_local_path_and_vct()
    check_secrets()
    check_referenced_files()
    check_no_unguarded_destructive()

    width = max(len(c) for _s, c, _d in results)
    failures = 0
    for status, check, detail in results:
        mark = "✓" if status == PASS else "✗"
        print(f"  [{mark}] {check.ljust(width)}  {detail}")
        if status == FAIL:
            failures += 1

    print()
    print("=" * 70)
    if failures:
        print(f"RESULT: FAIL - {failures} check(s) failed")
        print("=" * 70)
        return 1
    print(f"RESULT: PASS - all {len(results)} checks passed")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
