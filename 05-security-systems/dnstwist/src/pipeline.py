"""Domain permutation and a simple DNS probe."""
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KEYBOARD = {
    "a": "s", "s": "a", "d": "s", "f": "g", "g": "h", "h": "g", "j": "k",
    "q": "w", "w": "q", "e": "r", "r": "e", "t": "y", "y": "t",
    "u": "i", "i": "o", "o": "p", "p": "o", "z": "x", "x": "z",
    "c": "v", "v": "c", "b": "n", "n": "b", "m": "n",
}
HOMOGLYPHS = {"o": "0", "l": "1", "i": "1", "e": "3", "a": "4", "s": "5"}
TLDS = ["com", "net", "org", "io"]


def split_domain(domain):
    parts = domain.split(".")
    return parts[0], ".".join(parts[1:])


def permutations(domain):
    name, tld = split_domain(domain)
    results = set()
    for index in range(len(name)):
        results.add(name[:index] + name[index + 1:] + "." + tld)
        results.add(name[:index] + name[index] * 2 + name[index + 1:] + "." + tld)
        if name[index] in KEYBOARD:
            results.add(name[:index] + KEYBOARD[name[index]] + name[index + 1:] + "." + tld)
        if name[index] in HOMOGLYPHS:
            results.add(name[:index] + HOMOGLYPHS[name[index]] + name[index + 1:] + "." + tld)
    for index in range(len(name) - 1):
        swapped = name[:index] + name[index + 1] + name[index] + name[index + 2:]
        results.add(swapped + "." + tld)
    for other in TLDS:
        results.add(name + "." + other)
    results.discard(domain)
    return sorted(results)


def probe(host, timeout=1.5):
    """The address of a host, or None when there is none.

    The lookup runs in a thread that is given the time it may take, so the
    timeout belongs to this call and not to the whole process.
    """
    answer = {}

    def lookup():
        try:
            answer["address"] = socket.gethostbyname(host)
        except (socket.gaierror, socket.timeout, OSError):
            answer["address"] = None

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(timeout)
    return answer.get("address")


def run(quick=False, base_dir="artifacts"):
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    # A domain that really exists, so the demo shows what a result looks
    # like. A name under .test never resolves.
    domain = "example.com"
    candidates = permutations(domain)
    if quick:
        candidates = candidates[:12]
    rows = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        addresses = list(pool.map(probe, candidates))
    for candidate, address in zip(candidates, addresses):
        rows.append({"domain": candidate, "resolved": bool(address), "address": address or ""})

    report = base / "candidates.csv"
    report.write_text(
        "domain,resolved,address\n" + "\n".join(
            f"{row['domain']},{int(row['resolved'])},{row['address']}" for row in rows
        ) + "\n",
        encoding="utf-8",
    )
    resolved = [row for row in rows if row["resolved"]]
    return {
        "domain": domain,
        "candidates": len(rows),
        "resolved": len(resolved),
        "unresolved": len(rows) - len(resolved),
        "sample": rows[0]["domain"] if rows else "",
        "report": str(report),
    }


def demo(base_dir="artifacts"):
    summary = run(quick=True, base_dir=base_dir)
    rows = (Path(base_dir) / "candidates.csv").read_text().strip().splitlines()[1:]
    print(f"Generated {summary['candidates']} candidates for {summary['domain']}")
    for line in rows[:10]:
        print("  " + line)
    print(f"Resolved: {summary['resolved']}, unresolved: {summary['unresolved']}")
