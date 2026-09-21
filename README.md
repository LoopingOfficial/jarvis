# JARVIS — Local AI Command Center for Windows

JARVIS is a Windows-first local AI command center that brings together LLM orchestration, tools, automations, memory, voice, connectors, system actions, and a live web interface in one desktop-oriented runtime.

The project is designed for people who want an AI assistant that can do more than chat: it can coordinate tools, observe task state, interact with local applications and remote systems, keep an audit trail, and expose its activity through a browser-based command center.

> **Project status:** active development. The current Windows distribution is experimental and should be reviewed before use on production machines.

## Why this project exists

Most AI assistants separate the model, tools, automation layer, memory, and user interface into unrelated products. JARVIS experiments with a single local control plane where those pieces can work together while remaining observable.

The current codebase focuses on:

- local-first execution where practical;
- configurable LLM providers, including local Ollama workflows;
- explicit tool execution with permissions and audit logging;
- persistent memory, conversations, tasks, and automations;
- a live browser interface fed by server-sent events;
- Windows desktop integration;
- voice workflows and optional local TTS;
- 3D/avatar and Blender-assisted workflows;
- connectors for external services and remote machines;
- security-sensitive storage through an encrypted secret vault.

## Current capabilities

### AI orchestration

The core runtime coordinates model access, tools, conversations, tasks, memory, agents, and automations. Provider configuration is kept separate from the orchestration layer so the assistant can evolve without being tied to one model vendor.

### Tools and actions

JARVIS includes a tool registry and guarded execution path for system, remote, web, image, avatar, and Blender-related actions. Sensitive operations can be routed through permission checks instead of executing silently.

### Memory and task state

The runtime maintains structured local state for conversations, memories, tasks, automation jobs, and audit events. The goal is to let long-running work remain inspectable instead of disappearing inside a single chat turn.

### Live interface

The local HTTP server exposes a browser UI and a Server-Sent Events stream for real-time state updates. The UI is intended to show what the assistant is doing, not only the final answer.

### Windows integration

The Windows build includes launch helpers for common desktop applications, local process/system monitoring, Windows credential storage support, and PowerShell/cmd-oriented execution paths.

### Voice

Browser voice features are supported, with optional local audio components. Piper-based TTS and clap-trigger experiments are also present in the codebase.

### Avatar and 3D workflows

The project contains an experimental avatar pipeline, Blender integration, revision tracking, live preview jobs, and GPU-aware rendering helpers.

## Architecture

The runtime is intentionally modular. Major subsystems live under `jarvis/` and are assembled by `JarvisCore`.

Key areas include:

- `jarvis/core.py` — application composition and global runtime state;
- `jarvis/orchestrator.py` — orchestration and task flow;
- `jarvis/llm/` — model/provider integration;
- `jarvis/tools/` — registered tools and guarded execution;
- `jarvis/memory.py` — persistent memory;
- `jarvis/automations.py` — scheduled and automated work;
- `jarvis/connectors.py` — external integrations;
- `jarvis/secrets.py` — secret-vault handling;
- `jarvis/audit.py` — audit trail;
- `jarvis/server.py` — local HTTP API, SSE, and static UI serving;
- `jarvis/avatar*.py` and `jarvis/blender*.py` — experimental avatar/3D pipeline;
- `ui/` — browser command center.

More detail is available in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- Windows 10 or Windows 11;
- Python 3.11 or newer;
- Chrome or Edge for the browser UI;
- optional: Ollama for local models;
- optional: Git, Docker, OpenCode, Blender, and remote SSH tooling depending on the features you use.

## Installation

1. Clone or copy the repository to a writable folder.
2. Install Python 3.11+ and make sure Python is available in `PATH`.
3. Run:

```bat
install_windows.bat
```

4. Start JARVIS with:

```bat
run_jarvis.bat
```

5. Open:

```text
http://127.0.0.1:8765/
```

6. Configure your model provider and connectors from the settings interface.

Do not run the application directly from inside a ZIP archive.

## Optional audio dependencies

From a terminal in the repository:

```bat
.venv\Scripts\python.exe -m pip install -r requirements-audio.txt
```

Then enable the optional clap listener through your local environment configuration.

## Tests

Run the Python test suite with:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Some Windows-specific tests use simulated native APIs when executed on macOS or another non-Windows environment.

## Security model

JARVIS can execute tools and interact with local or remote systems, so treat it as privileged software.

Important safeguards in the current design include:

- local binding by default on `127.0.0.1`;
- encrypted/local secret-vault handling;
- permission checks for sensitive actions;
- audit logging;
- explicit handling for destructive command patterns;
- separation between configuration, secrets, and repository files.

Never commit API keys, passwords, private keys, or local data stores. See [SECURITY.md](SECURITY.md) for reporting and operational guidance.

## Data and privacy

Local runtime data is created under the project's data storage path and is intentionally excluded from Git. Credentials and secrets should be configured locally and must never be committed to the repository.

The exact privacy boundary depends on the model provider and connectors you enable. A fully local Ollama workflow has different data exposure than a cloud model or third-party integration.

## Contributing

Contributions that improve reliability, portability, security, tests, documentation, provider support, or the tool system are welcome once the licensing/provenance review described below is complete.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Provenance and licensing status

This repository contains a Windows-focused adaptation of an earlier JARVIS Command Center codebase together with substantial ongoing development.

Before the full repository is presented as reusable open-source software, the provenance and licensing of all inherited portions must be verified and the required notices preserved. This OSS-readiness branch intentionally **does not change the repository license**.

For a separate project from this account that is already distributed under an OSI-approved license, see `claude-workflow-v1` (MIT).

## Roadmap

Near-term priorities:

- complete provenance and license review;
- document supported providers and connectors;
- improve clean-install reproducibility;
- add CI for the Python test suite;
- expand security regression tests;
- publish tagged releases with reproducible changelogs;
- make external contribution paths clearer;
- reduce Windows-specific assumptions where practical.

## Maintainer

Maintained by [@jeromedu-91-cell](https://github.com/jeromedu-91-cell).

Feedback, bug reports, reproducible test cases, and focused pull requests are welcome.
