# Engineering Conventions

Language: English | [简体中文](engineering_conventions.zh-CN.md)

This document describes the stable engineering conventions for developing, validating, and maintaining SharedAutonomy-VLA. Machine-specific commissioning procedures, local configuration values, experiment notebooks, and internal planning records are outside this public project guide.

## 1. Environment and checks

- The supported Python range is `>=3.12,<3.13`; LeRobot is pinned in `pyproject.toml`.
- Ruff provides formatting and static checks; pytest provides offline behavioral checks.
- Install only the optional dependency groups needed for the work.
- Do not introduce a new framework or service unless the change requires it and the dependency decision is documented.

For a routine code change, run checks proportional to its scope:

```powershell
python -m ruff check <changed paths>
python -m ruff format --check <changed paths>
pytest -m core
```

Run the full test suite before hardware sessions, release staging, or changes to dataset mappings, gripper behavior, or other extended paths. Hardware-dependent verification is a separate, operator-controlled step and is never implied by an offline test pass.

## 2. Code and interface rules

- Code, identifiers, log messages, public API docstrings, and schema fields use English.
- Public classes, functions, configuration objects, and cross-module data structures use type annotations.
- Prefer typed `dataclass` interfaces for observations, actions, configuration, and episode metadata. Use unstructured dictionaries only at explicit serialization or external-protocol boundaries.
- Encode units in field names: `_m`, `_deg`, `_s`, `_hz`, and `_ns`.
- Vendor SDK calls stay in robot and device adapters. Collection, policy, data, and evaluation layers do not call hardware SDKs directly.
- Hardware and optional dependencies use lazy imports. Core imports, offline tools, and unit tests must work without connected devices or hardware packages.
- Safety checks stay close to command execution and remain testable. No human or policy path may bypass `sharedautonomy.robot.safety`.

## 3. Logging and episode data

Text logs explain low-frequency runtime events. Episode records store high-frequency observations, actions, timing, and safety metadata. Do not mix the two.

- Library modules use `logging.getLogger(__name__)` and do not configure handlers.
- Process entry points configure console and file logging.
- `INFO` is for connection state, mode changes, run start/finish, effective configuration, saved outputs, and safety interventions—not per-step control data.
- `WARNING` and `ERROR` identify the operation, device, and recoverability without printing credentials, raw images, or large payloads.
- Observations and the separate `human`, `assist`, and safety-filtered `executed` actions belong in the structured episode recorder.
- Interrupted runs retain available metadata, events, and a reason for termination.

A normal runtime output may contain:

```text
outputs/runs/<run_id>/
├── effective_config.yaml
├── metadata.json
├── run.log
├── events.jsonl
└── episode/
```

Runtime outputs are local artifacts and are not committed. The effective configuration must exclude credentials and other secrets.

## 4. Configuration and hardware safety

YAML is the human-readable project configuration format. Precedence runs from shared defaults to workflow configuration, machine-local overrides, and explicit CLI arguments. A real run records the merged effective configuration.

Shared configuration contains only reusable, publishable defaults. Controller addresses, ports, device identities, calibration, workspace geometry, ready poses, data roots, and credentials remain in ignored local files or environment variables. Public templates live in `configs/local/*.example.yaml`; real values use ignored `*.local.yaml` files.

Physical motion is disabled by default. A motion-capable run requires both a valid local configuration gate and an explicit command-line motion gate. Mock, dry-run, read-only, and low-speed operator-controlled checks precede task execution. See [`hardware.md`](hardware.md) for the public safety boundary.

## 5. Documentation and compatibility

- `README.md` is the public entry point; stable method, task, data, training, result, limitation, and hardware facts belong in their topic documents.
- Public narrative Markdown has an English default file and a mirrored Simplified Chinese `.zh-CN.md` file. Both pages link to each other at the top and use same-language links in their prose.
- Code, JSON, CSV, YAML, figures, and other language-neutral artifacts are shared by both documentation languages.
- Public facts should come from code, configuration, machine-readable result records, or other frozen sources; do not maintain conflicting numbers in prose.
- A breaking change to a schema, dataset feature layout, configuration contract, or public protocol requires an explicit compatibility note and version change where applicable.

Keep changes local and verifiable. Avoid unrelated refactoring, repository-wide formatting, or dependency changes while fixing a focused issue.
