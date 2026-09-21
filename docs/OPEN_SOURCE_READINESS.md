# Open-Source Readiness Checklist

This document tracks the work required before JARVIS should be presented as a fully reusable open-source project.

## Current status

- Public repository: **yes**
- Active development: **yes**
- Recent public activity: **yes**
- Tests: **present**
- Architecture documentation: **present**
- Contribution guide: **added on this branch**
- Security policy: **added on this branch**
- OSI-approved repository license: **not yet verified for the full JARVIS codebase**
- Provenance review: **required**
- Tagged public releases: **not yet established**
- External contributor history: **not yet established**
- Package registry distribution: **not yet established**

## Blocker 1 — provenance and license

The existing README describes this repository as an adaptation of an earlier JARVIS Command Center codebase.

Before adding a repository-wide OSI license:

1. identify the exact upstream source or sources;
2. record each upstream repository and commit/release used;
3. verify the upstream license at that revision;
4. preserve copyright and attribution notices;
5. determine whether modifications must use the same license;
6. identify third-party assets separately from source code;
7. add the correct LICENSE and NOTICE/COPYING files;
8. document provenance in the README.

Do not replace an upstream copyleft license with MIT merely for simplicity.

## Blocker 2 — reproducible public installation

A clean Windows machine should be able to:

1. clone the repository;
2. run the documented installer;
3. start the local server;
4. open the UI;
5. configure at least one local or cloud provider;
6. run the test suite.

Any machine-specific assumptions should be moved to optional configuration.

## Blocker 3 — public maintenance signals

For a healthy OSS project, establish:

- tagged releases;
- changelog discipline;
- issue templates;
- continuous integration;
- clear contribution labels;
- security reporting;
- documented support boundaries.

## Evidence for external programs

For programs such as Claude for Open Source, only submit claims that can be verified publicly.

Useful evidence includes:

- repository age and recent activity;
- OSI-approved licensing;
- package downloads;
- dependent repositories/packages;
- merged external pull requests;
- unique external contributors;
- recognized foundation maintainer status;
- OpenSSF criticality score;
- concrete production or ecosystem dependence.

Do not manufacture stars, downloads, forks, dependents, contributors, or pull requests. Artificially inflated metrics can invalidate an application.
