"""
verifier_core.py — Examen Labs shared verifier library
=======================================================

Every task verifier imports this module. It owns:
  - The canonical flag definitions and emitters
  - Safe file/process/JSON/pickle helpers with edge-case handling
  - A Result accumulator for multi-check verifiers
  - A top-level run() wrapper that catches PANIC

FLAG SYSTEM
-----------
PASS        requirement satisfied
FAIL        agent code violated a stated requirement
CHEATED     agent bypassed a constraint or tampered with protected files
INVALID     output present but malformed / unparseable / wrong type
TIMEOUT     subprocess did not finish within the allowed time limit
WARN        non-fatal issue noted; does not affect pass/fail outcome
ENVIRONMENT required infrastructure is missing — not the agent's fault
PANIC       verifier itself raised an unhandled exception (bug in verifier)

EXIT CODES
----------
0   all checks passed
1   one or more FAIL / CHEATED / INVALID checks
2   PANIC (verifier bug)
3   ENVIRONMENT (infrastructure missing — do not penalise the agent)
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import re
import subprocess
import sys
import textwrap
import traceback
from pathlib import Path
from typing import Any, Callable

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_FILE_BYTES   = 50 * 1024 * 1024   # 50 MB — refuse to read larger files
MAX_OUTPUT_LINES = 10_000             # guard against runaway output flooding


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Flag emitters (always call these, never print flags directly)
# ══════════════════════════════════════════════════════════════════════════════

def _emit(flag: str, message: str, *, file=sys.stderr) -> None:
    """Write one flag line. All output goes through here."""
    line = f"{flag}: {message}"
    print(line, file=file if flag in ("ENVIRONMENT", "PANIC") else sys.stdout)


def passed(message: str) -> None:
    """Requirement satisfied."""
    _emit("PASS", message, file=sys.stdout)


def failed(message: str) -> None:
    """Agent code violated a stated requirement. Raises SystemExit(1)."""
    _emit("FAIL", message)
    raise SystemExit(1)


def cheated(message: str) -> None:
    """Agent bypassed a constraint or tampered with protected files. Raises SystemExit(1)."""
    _emit("CHEATED", message)
    raise SystemExit(1)


def invalid(message: str) -> None:
    """Output is present but malformed, unparseable, or has wrong types. Raises SystemExit(1)."""
    _emit("INVALID", message)
    raise SystemExit(1)


def timed_out(message: str) -> None:
    """Subprocess did not finish within the allowed time limit. Raises SystemExit(1)."""
    _emit("TIMEOUT", message)
    raise SystemExit(1)


def warn(message: str) -> None:
    """Non-fatal issue — logged but does not affect pass/fail."""
    _emit("WARN", message, file=sys.stdout)


def environment_error(message: str) -> None:
    """Required infrastructure is missing — not the agent's fault. Raises SystemExit(3)."""
    _emit("ENVIRONMENT", message)
    raise SystemExit(3)


def panic(message: str) -> None:
    """Verifier itself has a bug. Raises SystemExit(2)."""
    _emit("PANIC", message, file=sys.stderr)
    raise SystemExit(2)


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Result accumulator (for multi-check verifiers that don't
#              want to stop on first failure)
# ══════════════════════════════════════════════════════════════════════════════

class Results:
    """
    Collects pass/fail/warn/etc without raising immediately.
    Call .finish() at the end to raise SystemExit with correct code.

    Usage:
        r = Results()
        r.check("label", condition, "reason if false")
        r.check_cheated("label", not_tampered, "reason")
        r.finish()
    """

    def __init__(self) -> None:
        self._pass: list[str] = []
        self._fail: list[str] = []
        self._cheated: list[str] = []
        self._invalid: list[str] = []
        self._timeout: list[str] = []
        self._warn: list[str] = []
        self._environment: list[str] = []

    # ── Checkers ──────────────────────────────────────────────────────────────

    def check(self, label: str, condition: bool, reason: str = "") -> bool:
        if condition:
            _emit("PASS", label, file=sys.stdout)
            self._pass.append(label)
            return True
        msg = f"{label}" + (f" — {reason}" if reason else "")
        _emit("FAIL", msg, file=sys.stdout)
        self._fail.append(msg)
        return False

    def check_cheated(self, label: str, condition: bool, reason: str = "") -> bool:
        if condition:
            _emit("PASS", label, file=sys.stdout)
            self._pass.append(label)
            return True
        msg = f"{label}" + (f" — {reason}" if reason else "")
        _emit("CHEATED", msg, file=sys.stdout)
        self._cheated.append(msg)
        return False

    def check_invalid(self, label: str, condition: bool, reason: str = "") -> bool:
        if condition:
            _emit("PASS", label, file=sys.stdout)
            self._pass.append(label)
            return True
        msg = f"{label}" + (f" — {reason}" if reason else "")
        _emit("INVALID", msg, file=sys.stdout)
        self._invalid.append(msg)
        return False

    def add_warn(self, message: str) -> None:
        _emit("WARN", message, file=sys.stdout)
        self._warn.append(message)

    def add_environment(self, message: str) -> None:
        _emit("ENVIRONMENT", message, file=sys.stderr)
        self._environment.append(message)

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> None:
        total = len(self._pass) + len(self._fail) + len(self._cheated) + \
                len(self._invalid) + len(self._timeout)
        bad = len(self._fail) + len(self._cheated) + \
              len(self._invalid) + len(self._timeout)
        print(f"\n── Summary: {len(self._pass)}/{total} checks passed"
              f"{', '+str(bad)+' failed' if bad else ''} ──")

    def finish(self) -> None:
        """Print summary and raise SystemExit with the right code."""
        self.summary()
        if self._environment:
            raise SystemExit(3)
        if self._cheated or self._invalid or self._fail or self._timeout:
            raise SystemExit(1)
        # All good
        raise SystemExit(0)

    @property
    def passed_all(self) -> bool:
        return not (self._fail or self._cheated or self._invalid
                    or self._timeout or self._environment)


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Safe file helpers
# ══════════════════════════════════════════════════════════════════════════════

def require_path(path: Path, *, description: str = "") -> Path:
    """
    Assert path exists and is not a symlink to a protected location.
    Raises failed() if missing, cheated() if symlink attack detected.
    """
    label = description or str(path)
    if not path.exists():
        failed(f"required path missing: {label}")
    if path.is_symlink():
        target = path.resolve()
        # Symlink is fine as long as it stays inside the workspace
        workspace = path.parents[-2]  # best-effort
        try:
            target.relative_to(workspace)
        except ValueError:
            cheated(f"symlink {path} points outside workspace to {target}")
    return path


def read_text_safe(path: Path, *, max_bytes: int = MAX_FILE_BYTES) -> str:
    """
    Read a text file with full edge-case handling:
      - missing → failed()
      - too large → invalid()
      - binary / null bytes → invalid()
      - non-UTF-8 → invalid()
      - symlink attack → cheated()
    """
    require_path(path, description=str(path))
    size = path.stat().st_size
    if size == 0:
        invalid(f"{path} is an empty file (0 bytes)")
    if size > max_bytes:
        invalid(f"{path} is {size:,} bytes — exceeds {max_bytes:,} byte limit")
    try:
        raw = path.read_bytes()
    except PermissionError:
        environment_error(f"permission denied reading {path}")
    if b"\x00" in raw:
        invalid(f"{path} contains null bytes — likely a binary file, not text")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        invalid(f"{path} is not valid UTF-8 text")


def read_json_safe(path: Path) -> Any:
    """
    Read and parse a JSON file with full edge-case handling:
      - missing / empty / null bytes → delegates to read_text_safe
      - invalid JSON syntax → invalid()
      - valid JSON but wrong root type → let caller handle
    """
    text = read_text_safe(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        invalid(f"{path} is not valid JSON: {exc}")


def read_json_object(path: Path) -> dict:
    """Like read_json_safe but asserts the root is a JSON object (dict)."""
    data = read_json_safe(path)
    if not isinstance(data, dict):
        invalid(f"{path} must be a JSON object ({{...}}), got {type(data).__name__}")
    return data


def read_json_list(path: Path) -> list:
    """Like read_json_safe but asserts the root is a JSON array."""
    data = read_json_safe(path)
    if not isinstance(data, list):
        invalid(f"{path} must be a JSON array ([...]), got {type(data).__name__}")
    return data


def read_pickle_safe(path: Path) -> Any:
    """
    Deserialize a pickle file with edge-case handling:
      - missing / empty / binary check → delegates to require_path
      - unpickleable → invalid()
      - hand-authored (wrong type) → cheated()
    """
    require_path(path, description=str(path))
    if path.stat().st_size == 0:
        invalid(f"{path} is an empty pickle file (0 bytes)")
    try:
        with open(path, "rb") as fh:
            return pickle.load(fh)
    except (pickle.UnpicklingError, EOFError, ImportError, AttributeError) as exc:
        invalid(f"{path} could not be unpickled: {exc}")
    except Exception as exc:
        invalid(f"{path} failed to load: {exc}")


def assert_json_fields(
    data: dict,
    required: list[str],
    *,
    path: Path | None = None,
    types: dict[str, type] | None = None,
) -> None:
    """
    Assert a dict has all required keys.
    Optionally assert value types (types dict: field → expected type).
    Raises invalid() on structural problems, failed() on wrong types.
    """
    label = str(path) if path else "JSON object"
    missing = [k for k in required if k not in data]
    if missing:
        invalid(f"{label} missing required fields: {', '.join(missing)}")
    if types:
        for field, expected_type in types.items():
            if field in data and not isinstance(data[field], expected_type):
                got = type(data[field]).__name__
                invalid(
                    f"{label} field '{field}' must be {expected_type.__name__}, "
                    f"got {got} ({data[field]!r})"
                )


def sha256_file(path: Path) -> str:
    """Return lowercase hex SHA256 of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_file_unchanged(path: Path, expected_sha256: str, *, description: str = "") -> None:
    """Assert a seed file has not been modified. Raises cheated() if hash differs."""
    label = description or str(path)
    require_path(path, description=label)
    actual = sha256_file(path)
    if actual != expected_sha256.lower():
        cheated(
            f"{label} has been modified (expected SHA256 "
            f"{expected_sha256[:12]}…, got {actual[:12]}…). "
            "This file must remain unchanged."
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Safe subprocess helpers
# ══════════════════════════════════════════════════════════════════════════════

class RunResult:
    """Thin wrapper around CompletedProcess with convenience properties."""

    def __init__(self, cp: subprocess.CompletedProcess) -> None:
        self._cp = cp

    @property
    def returncode(self) -> int:
        return self._cp.returncode

    @property
    def stdout(self) -> str:
        return self._cp.stdout or ""

    @property
    def stderr(self) -> str:
        return self._cp.stderr or ""

    @property
    def output(self) -> str:
        """Combined stdout + stderr."""
        return self.stdout + self.stderr

    def assert_success(self, label: str = "") -> "RunResult":
        if self._cp.returncode != 0:
            msg = label or f"command failed (exit {self._cp.returncode})"
            failed(
                f"{msg}\n"
                f"  exit: {self._cp.returncode}\n"
                f"  stdout: {self.stdout[-800:]}\n"
                f"  stderr: {self.stderr[-800:]}"
            )
        return self

    def contains(self, needle: str, *, case_sensitive: bool = True) -> bool:
        haystack = self.output if case_sensitive else self.output.lower()
        n = needle if case_sensitive else needle.lower()
        return n in haystack


def run_safe(
    cmd: list[str | Path],
    *,
    cwd: Path | None = None,
    timeout: int = 300,
    env: dict | None = None,
    label: str = "",
    check: bool = False,
    fail_on_timeout: bool = True,
) -> RunResult:
    """
    Run a subprocess with comprehensive edge-case handling:
      - TimeoutExpired           → timed_out()  (or returned with rc=-1 if fail_on_timeout=False)
      - FileNotFoundError        → environment_error()  (binary not found)
      - PermissionError          → environment_error()  (not executable)
      - MemoryError / OOM signal → failed()
      - check=True raises failed() if returncode != 0
    """
    cmd_str = " ".join(str(c) for c in cmd)
    try:
        cp = subprocess.run(
            [str(c) for c in cmd],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        msg = label or cmd_str
        if fail_on_timeout:
            timed_out(f"{msg} — did not finish within {timeout}s")
        # Return a synthetic result
        return RunResult(subprocess.CompletedProcess(cmd, -1, "", f"timed out after {timeout}s"))
    except FileNotFoundError:
        environment_error(f"executable not found: {cmd[0]}")
    except PermissionError:
        environment_error(f"permission denied executing: {cmd[0]}")
    except OSError as exc:
        environment_error(f"OS error running {cmd_str}: {exc}")

    result = RunResult(cp)

    # Heuristic: detect OOM kill (exit code -9 / 137)
    if cp.returncode in (-9, 137):
        failed(
            f"{label or cmd_str} was killed by the OS "
            f"(exit {cp.returncode}) — likely OOM. "
            "Check for memory leaks or excessive allocation."
        )

    if check:
        result.assert_success(label or cmd_str)

    return result


def assert_binary_exists(path: Path, *, description: str = "") -> None:
    """Assert a built binary exists and is executable."""
    label = description or str(path)
    require_path(path, description=label)
    if not os.access(path, os.X_OK):
        failed(f"{label} exists but is not executable")


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Cheating / tampering detectors
# ══════════════════════════════════════════════════════════════════════════════

def assert_only_allowed_modified(
    workspace: Path,
    allowed: set[str],
    *,
    use_git: bool = True,
) -> None:
    """
    Assert that only files in `allowed` (relative paths) have been changed.
    Uses `git diff --name-only HEAD` if use_git=True, otherwise skips git check.
    Raises cheated() if a forbidden file was modified.
    """
    if not use_git:
        return
    result = run_safe(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=workspace,
        timeout=30,
        fail_on_timeout=False,
    )
    if result.returncode != 0:
        warn("git diff unavailable — skipping tamper check")
        return
    changed = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    forbidden = [f for f in changed if f not in allowed]
    if forbidden:
        cheated(
            f"Modified forbidden file(s): {', '.join(forbidden)}. "
            f"Only these paths may be changed: {', '.join(sorted(allowed))}"
        )


def assert_no_magic_string(
    path: Path,
    forbidden_patterns: list[str],
    *,
    description: str = "",
) -> None:
    """
    Assert that a file does not contain hardcoded magic strings that
    suggest the agent copy-pasted expected values rather than computing them.
    Raises cheated() on any match.
    """
    label = description or str(path)
    text = read_text_safe(path)
    for pattern in forbidden_patterns:
        if re.search(pattern, text):
            cheated(
                f"{label} contains a forbidden pattern ({pattern!r}) that "
                "suggests hardcoded or copy-pasted expected values."
            )


def assert_not_empty_submission(path: Path, *, min_bytes: int = 10) -> None:
    """Raise failed() if a file is effectively empty."""
    require_path(path, description=str(path))
    if path.stat().st_size < min_bytes:
        failed(
            f"{path} is too small ({path.stat().st_size} bytes). "
            "The submission appears to be an empty placeholder."
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Markdown / doc helpers
# ══════════════════════════════════════════════════════════════════════════════

def assert_headings_present(
    path: Path,
    required_headings: list[str],
    *,
    case_sensitive: bool = False,
) -> None:
    """
    Assert that a Markdown file contains all required headings.
    Raises failed() listing every missing heading.
    Edge cases: empty file, binary, non-UTF-8 all handled via read_text_safe.
    """
    text = read_text_safe(path)
    if not case_sensitive:
        text_check = text.lower()
        headings_check = [h.lower() for h in required_headings]
    else:
        text_check = text
        headings_check = required_headings

    missing = [h for h in headings_check if h not in text_check]
    if missing:
        failed(
            f"{path} is missing required headings: "
            + ", ".join(f"'{h}'" for h in missing)
        )


def assert_keywords_present(
    path: Path,
    keywords: list[str],
    *,
    case_sensitive: bool = False,
    description: str = "",
) -> None:
    """
    Assert a document mentions all required keywords.
    Edge: empty content raises failed() before keyword scan.
    """
    label = description or str(path)
    text = read_text_safe(path)
    if not case_sensitive:
        text_check = text.lower()
        kw_check = [k.lower() for k in keywords]
    else:
        text_check = text
        kw_check = keywords

    missing = [k for k, kc in zip(keywords, kw_check) if kc not in text_check]
    if missing:
        failed(
            f"{label} is missing required keywords: "
            + ", ".join(f"'{k}'" for k in missing)
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — PGN helpers
# ══════════════════════════════════════════════════════════════════════════════

def parse_pgn(pgn_path: Path) -> dict:
    """
    Parse a PGN file and return game statistics.
    Edge cases: empty file, non-UTF-8, no Result tags, malformed tags.
    Returns: {games, wins_white, wins_black, draws, incomplete}
    """
    text = read_text_safe(pgn_path)
    games = 0
    wins_white = 0
    wins_black = 0
    draws = 0
    incomplete = 0

    for line in text.splitlines():
        line = line.strip()
        if line.startswith('[Result "'):
            try:
                res = line.split('"')[1]
            except IndexError:
                incomplete += 1
                continue
            if res == "1-0":
                wins_white += 1
                games += 1
            elif res == "0-1":
                wins_black += 1
                games += 1
            elif res == "1/2-1/2":
                draws += 1
                games += 1
            elif res == "*":
                incomplete += 1
            else:
                incomplete += 1

    if games == 0 and incomplete == 0:
        invalid(f"{pgn_path} contains no game Result tags — not a valid PGN")

    return {
        "games": games,
        "wins_white": wins_white,
        "wins_black": wins_black,
        "draws": draws,
        "incomplete": incomplete,
    }


def assert_pgn_complete(pgn_path: Path, *, min_games: int = 1) -> dict:
    """
    Parse PGN and assert minimum game count, no incomplete games.
    Raises invalid() for malformed PGN, failed() for count violations.
    """
    stats = parse_pgn(pgn_path)
    if stats["incomplete"] > 0:
        invalid(
            f"{pgn_path} has {stats['incomplete']} incomplete game(s) (Result \"*\"). "
            "All games must have a decisive result."
        )
    if stats["games"] < min_games:
        failed(
            f"{pgn_path} has {stats['games']} complete game(s) "
            f"but at least {min_games} required."
        )
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Top-level PANIC wrapper
# ══════════════════════════════════════════════════════════════════════════════

def run_verifier(main_fn: Callable[[], None]) -> None:
    """
    Top-level wrapper for all verifier main functions.
    Catches any unhandled exception and emits PANIC instead of a raw traceback.

    Usage (at the bottom of every verify.py):
        from verifier_core import run_verifier
        def main(): ...
        if __name__ == "__main__":
            run_verifier(main)
    """
    try:
        main_fn()
    except SystemExit:
        raise  # Normal exit — let it propagate
    except KeyboardInterrupt:
        environment_error("Verifier interrupted by user (KeyboardInterrupt)")
    except MemoryError:
        panic(
            "Verifier ran out of memory. "
            "The agent's output may be unexpectedly large."
        )
    except RecursionError:
        panic("Verifier hit recursion limit — possible infinite loop in helper code.")
    except Exception:
        tb = traceback.format_exc()
        panic(
            f"Unhandled exception in verifier — this is a verifier bug, "
            f"not an agent failure:\n{textwrap.indent(tb, '  ')}"
        )
