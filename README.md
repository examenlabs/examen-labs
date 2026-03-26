# Examen Labs — Eval Suite v1.0

> *Examen* (Latin): the precise weighing of something. We weigh AI models.

**Examen Labs** builds boutique, verified coding evaluation suites for AI labs
and fine-tuning companies.

---

## Task ID Convention

```
EXM-{DOMAIN}-{SEQ}
```

| Domain | Meaning |
|--------|---------|
| `ROS`  | Robotics / DevOps |
| `CPP`  | C++ Systems |
| `WEB`  | Web Backend |
| `ALG`  | Algorithms |
| `GPU`  | CUDA / GPU |
| `TTS`  | Audio / ML |

Sequence numbers are zero-padded 3 digits, assigned per domain.

---

## Eval Suite Contents

| Task ID | Title | Domain | Difficulty |
|---------|-------|--------|------------|
| [EXM-ROS-001](EXM-ROS-001/) | ROS 2 Devcontainer Mount Debugging | DevOps / ROS 2 / Docker | Medium |
| [EXM-CPP-001](EXM-CPP-001/) | Chess Engine Optimization (Nextfish v1) | Systems / C++ | Hard |
| [EXM-WEB-001](EXM-WEB-001/) | Exchange Backend — Django REST API | Backend / Finance | Hard |
| [EXM-ALG-001](EXM-ALG-001/) | Sokoban Solver — C++17 with A*/IDA* | Algorithms / Systems | Hard |
| [EXM-GPU-001](EXM-GPU-001/) | CUDA Stream Race Condition Debugging | CUDA / GPU | Hard |
| [EXM-CPP-002](EXM-CPP-002/) | Chess Engine Optimization (Nextfish v2) | Systems / C++ | Hard |
| [EXM-TTS-001](EXM-TTS-001/) | Qwen3-TTS Voice Library Pipeline | ML / Audio / Python | Medium-Hard |

---

## Task Folder Structure

Every task folder follows this exact layout:

```
EXM-{DOMAIN}-{SEQ}/
├── prompt.md          ← what the model sees (YAML frontmatter + task body)
├── problem.md         ← human-readable problem statement
├── requirements.md    ← explicit hard requirements list
├── scope.md           ← scope-down justification + bijection check
├── review.md          ← quality assessment
├── environment/       ← Dockerfile + workspace seed files
└── verifier/
    ├── verify.py      ← executable verifier (imports verifier_core)
    └── verifier.sh    ← shell entrypoint
```

### File naming conventions

| File | Purpose |
|------|---------|
| `prompt.md` | Agent-facing task prompt. YAML frontmatter with `title`, `difficulty`, `domain`, `author`. |
| `problem.md` | Internal problem statement for task authors and buyers. |
| `requirements.md` | Explicit numbered hard requirements, machine-readable style. |
| `scope.md` | Scope-down document. Must answer: what was reduced, why, and why capability is preserved. Includes bijection check. |
| `review.md` | Quality review of the bundle. Covers env match, verifier coverage, priority issues. |
| `verify.py` | Standalone Python verifier. Imports `verifier_core`. Emits structured flags. |

---

## Flag System

All verifiers emit exactly one of these flags per check:

| Flag | Exit Code | Meaning |
|------|-----------|---------|
| `PASS` | 0 | Requirement satisfied |
| `FAIL` | 1 | Agent code violated a stated requirement |
| `CHEATED` | 1 | Agent tampered with protected files or bypassed a constraint |
| `INVALID` | 1 | Output present but malformed, unparseable, or wrong type |
| `TIMEOUT` | 1 | Subprocess did not finish within the allowed time limit |
| `WARN` | — | Non-fatal issue — logged but does not affect outcome |
| `ENVIRONMENT` | 3 | Required infrastructure missing — do not penalise the agent |
| `PANIC` | 2 | Verifier itself has a bug — do not penalise the agent |

---

## Running a Task

```bash
# 1. Build the environment
docker build -t exm-env EXM-ROS-001/environment/

# 2. Run the model inside the environment with prompt.md
# 3. Score the output
python3 EXM-ROS-001/verifier/verify.py
```

---

## Methodology

See [METHODOLOGY.md](METHODOLOGY.md) for the full bijection, verifier design,
and scope-down protocol.

---

## Contact

**Examen Labs** — Custom eval suites for AI companies. $300–500 per task.

- Email: examenlabs@gmail.com
- GitHub: github.com/examenlabs
- Web: examen.houseofstk.com
