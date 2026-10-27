# dnstwist

Generate typosquatting candidates and check whether they resolve.

> A domain permutation engine that detects typosquatting and brand-impersonation phishing domains.

## Overview

An original, self-contained implementation of the **dnstwist** concept, built for the Foundev portfolio. It focuses on the core workflow described in the project brief and keeps the architecture small enough to read end to end.

## Features

- The permutations of a domain: a letter left out, a letter written twice,
  the keyboard next door, the homoglyphs, two letters swapped and the other
  top level domains
- The lookup of a host runs in a thread with the time it may take, so the
  timeout belongs to the call and not to the whole process
- The candidates are looked up at the same time, so a hundred of them do not
  take minutes
- The report of every candidate

## Stack

`Python`
The CSV stack is Python. The standard library resolver is used instead of a DNS library, so probes are simple A lookups and there is no WHOIS or certificate data.

## Run

```bash
pip install -r requirements.txt
python main.py run
python main.py demo
pytest -q
```

## Structure

```text
dnstwist/
├── main.py                 pipeline CLI
├── src/pipeline.py         data pipeline
├── tests/test_pipeline.py  smoke test
├── requirements.txt
```

## Testing

```bash
pytest -q
```

12 tests: the permutations (a letter left out, a letter written twice, the
keyboard, the homoglyphs, two letters swapped, the other top level domains),
that the timeout of the lookup does not leak into the process and the report.

## Notes and limitations

- DNS probes need network access; offline runs report every candidate as unresolved.
- There is no WHOIS, TLS certificate or screenshot enrichment.
- Homoglyphs are a short hard-coded map, not a full Unicode confusables list.

## License

Apache-2.0. See `LICENSE`.

## About the scope

This is a third-year student project, not a production data platform. It uses local files and SQLite so the pipeline can run on one machine. The notes above list the production pieces that were replaced or skipped.
