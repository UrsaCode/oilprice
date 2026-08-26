# Security

## What is worth reporting

This project has no users to authenticate, no server, and no secrets — the
collection workflow runs with the default `GITHUB_TOKEN` and nothing else. So
the realistic reports are:

- **A dependency advisory** that affects `requirements.txt`.
- **A workflow permissions problem** — anything letting a fork or a pull
  request write to this repository or its data.
- **A parser that can be made to write something dangerous**, rather than
  merely something wrong. A source publishing a bad number is a data issue and
  belongs in a normal issue; a source able to make the parser write outside
  `data/`, execute anything, or exhaust the runner is a security issue.
- **A published page problem** — `docs/` is static and loads no third-party
  script, so anything that changes that is worth telling us about.

A scraper reading a figure incorrectly is **not** a security issue. Please open
a [source-broken issue](https://github.com/UrsaCode/oilprice/issues/new?template=source-broken.yml)
for that; it will be fixed faster in the open.

## How to report

Open a [private security advisory](https://github.com/UrsaCode/oilprice/security/advisories/new).
Please do not open a public issue for something exploitable.

We will acknowledge within a week and tell you what we intend to do. If we
disagree that it is a vulnerability we will say why rather than going quiet.

## Supported versions

There are no releases. The default branch is the only supported version, and
fixes land there.
