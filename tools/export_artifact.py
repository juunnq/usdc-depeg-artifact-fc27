#!/usr/bin/env python3
"""Build a clean, anonymous content export of the FC'27 artifact.

Produces a fresh directory OUTSIDE this repository by COPYING files, never by
`git clone`, `git worktree`, or any operation that transports `.git/` history. This
matters concretely: this repository's history includes commits that predate its
current, anonymized state, and a `.git`-carrying copy would expose that full history.
A content export cannot, by construction, carry history it never touches.

This file ships inside the export, so it must itself survive the sweep it runs. It
therefore holds NO sensitive literals: every identity-bearing search term (competition
vocabulary, the excluded thesis filename, third-party PII filename stems) is loaded at
run time from tools/sweep_vocab.local.json, which is git-ignored and excluded from the
export. The tool refuses to run without that file.

This replaces an earlier design in which the sweep exempted its own source, on the
reasoning that a search tool must contain its search terms as data. That reasoning is
correct for a tool that stays home and wrong for one that ships: the exemption meant
the export's own copy of this file carried the competition vocabulary in plain sight
past a sweep that reported the export clean. The exemption is gone; this file is swept
like every other, and passing that sweep is the check that the split worked.

Steps (see the four top-level functions below, run in order by main()):
  1. copy_whitelist()   -- copy exactly the reproducibility-relevant tree
  2. sweep_identifiers(), author/reviewer/process-ID sweep over the COPY only
  3. verify_reproduction(), run the documented five-step sequence + the short build
     inside the copy, with no key in the environment, and check the results
  4. finalize_export()  -- write EXPORT_MANIFEST.sha256 + a top-level README.md,
     then init a fresh, single-commit git repo under a generic "Anonymous" identity

Usage:
    python tools/export_artifact.py [--out PATH] [--skip-repro] [--force]

--out PATH      export destination (default: a sibling directory of this repo,
                 named usdc_depeg_export)
--skip-repro    skip step 3 (useful for iterating on steps 1/2/4 quickly; the
                 export is NOT submission-ready without a clean run of step 3)
--force         remove an existing export directory at --out before starting,
                 without the "does this look like a prior export" safety check
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKLIST = REPO_ROOT / "review" / "submission_checklist.md"

# Every sensitive literal this tool needs lives here, never in this file. Git-ignored
# and excluded from the export, so the shipped copy of this script carries none of it.
SWEEP_VOCAB_FILE = REPO_ROOT / "tools" / "sweep_vocab.local.json"


def _load_sweep_vocab() -> dict:
    """Load the sensitive-literal lists. Refuses to run without them: a missing file
    would otherwise silently degrade the sweep to 'found nothing' and ship an
    unswept export, which is the failure mode this whole split exists to prevent."""
    if not SWEEP_VOCAB_FILE.exists():
        raise SystemExit(
            f"refusing to run: {SWEEP_VOCAB_FILE.name} not found in tools/.\n"
            f"It holds the sweep's identity-bearing search terms and is deliberately "
            f"git-ignored, so a fresh clone must recreate it before an export can be "
            f"built. Required JSON keys: competition_vocab (list[str]), "
            f"pii_filename_stems (list[str]), exclude_top_level (list[str]), "
            f"process_id_patterns (list[str])."
        )
    try:
        vocab = json.loads(SWEEP_VOCAB_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"refusing to run: {SWEEP_VOCAB_FILE.name} unreadable: {exc}")
    missing = [k for k in ("competition_vocab", "pii_filename_stems", "exclude_top_level",
                           "process_id_patterns")
               if not isinstance(vocab.get(k), list)]
    if missing:
        raise SystemExit(f"refusing to run: {SWEEP_VOCAB_FILE.name} missing or "
                          f"non-list key(s): {', '.join(missing)}")
    if not vocab["competition_vocab"]:
        raise SystemExit(f"refusing to run: {SWEEP_VOCAB_FILE.name} has an empty "
                          f"competition_vocab; an empty needle list sweeps nothing.")
    return vocab


_VOCAB = _load_sweep_vocab()

# Third-party PII filenames excluded defensively, in case either is ever regenerated
# or restored by accident. Named by stem only; content is never read or printed,
# matching the handling rule the removal itself was done under.
PII_FILENAME_STEMS = set(_VOCAB["pii_filename_stems"])

# Directory names never copied, wherever they occur.
EXCLUDE_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", "review", "research",
                     ".claude", ".hyperresearch"}

# File names / suffixes never copied.
EXCLUDE_FILE_SUFFIXES = {".aux", ".log", ".out", ".toc", ".bbl", ".blg",
                          ".synctex.gz", ".fls", ".fdb_latexmk", ".pyc"}
# sweep_vocab.local.json is git-ignored, but the export copies the FILESYSTEM, not the
# index, so it must be named here or it would travel into the export carrying exactly
# the literals this split removed from the tool.
EXCLUDE_FILE_NAMES = {".env", "REVIEW-PROMPT.md", SWEEP_VOCAB_FILE.name}

# Top-level repo entries never copied. The identity-bearing names (a thesis filename
# among them) come from the git-ignored vocab file, not from this source.
EXCLUDE_TOP_LEVEL = {"CLAUDE.md", "hyperresearch", ".gitignore"} | set(_VOCAB["exclude_top_level"])

# Specific, individually-verified exclusions by repo-relative path (not by name or
# pattern, three files share the name "trades_raw.jsonl", and only this one is
# both oversized and confirmed unused). GitHub hard-rejects any file over 100MB;
# this raw capture is 100.34MB. Verified NOT load-bearing before excluding it: grepped
# every .py file for a read of this specific path (not just the filename, which also
# matches its two siblings) and found only its own fetch-writer
# (data_fetch_n1.py, the LIVE-fetch path, never invoked by the documented offline
# sequence) and one citation string in a table-fragment comment (tables.py), no
# reader anywhere in the offline reproduction path. Its sibling,
# data/n1/kraken_usdcusd/trades_raw.jsonl (50MB, under the limit), IS genuinely
# opened by trough_corroboration.py and stays in the export.
EXCLUDE_RELATIVE_PATHS = {
    "usdc_depeg/data/n1/kraken_usdtusd/trades_raw.jsonl",
}

# THE ON-CHAIN ATTRIBUTION PACKAGE, excluded because only the SHORT paper is submitted
# and it uses none of it. Numbers 2 (redemption concentration) and 4 (DAI contagion) and
# the FIFO rule beneath them are long-paper material that works from wallet-level
# Etherscan data. Leaving them in would hand a reviewer following the short paper's own
# artifact URL 776 raw addresses and ~147k third-party address labels that nothing in the
# submitted paper reads. Each entry names why it goes.
EXCLUDE_ONCHAIN_PATHS = {
    # code
    "usdc_depeg/redemptions.py",            # FIFO burn attribution to wallets; keyed pull
    "usdc_depeg/herfindahl.py",             # Number 2, redemption concentration
    "usdc_depeg/herfindahl_entity.py",      # entity-resolved HHI; reads the label corpora
    "usdc_depeg/dai_contagion.py",          # Number 4, on-chain DAI/PSM pass-through
    "usdc_depeg/fifo_rule.py",              # reads redemptions_by_wallet.csv, nothing else
    # tests of the above
    "usdc_depeg/tests/test_herfindahl.py",
    "usdc_depeg/tests/test_herfindahl_entity.py",
    "usdc_depeg/tests/test_dai_contagion.py",
    "usdc_depeg/tests/test_fifo_rule.py",
    # data: the wallet table itself, and the third-party label corpora only the
    # entity-resolved HHI consumes
    "usdc_depeg/data/redemptions_by_wallet.csv",
    # results: outputs of the excluded analyses, orphaned without them
    "usdc_depeg/results/number2_redemption_hhi.json",   # carries 4 distinct addresses
    "usdc_depeg/results/number4_dai_contagion.json",
    "usdc_depeg/results/fifo_rule.json",
}

# Whole trees, matched by prefix rather than exact path.
EXCLUDE_PATH_PREFIXES = (
    "usdc_depeg/data/n1/labels/",           # 147k third-party address labels
)

EXCLUDE_RELATIVE_PATHS |= EXCLUDE_ONCHAIN_PATHS

# The same set, relative to data/, for pruning MANIFEST.json (see prune_data_manifest).
EXCLUDED_DATA_RELATIVE = {
    "redemptions_by_wallet.csv",
    "n1/labels/dawsbot_eth-labels/accounts.csv",
    "n1/labels/brianleect_etherscan-labels/combinedAccountLabels.json",
}

# GitHub hard-rejects any file at or above 100MB. Anything at or above this
# threshold that ISN'T already in EXCLUDE_RELATIVE_PATHS gets a loud WARNING (never
# a silent skip), a data freeze that grows a new oversized raw capture must be
# looked at and either confirmed non-load-bearing (and added to the exclusion list
# above, the way trades_raw.jsonl was) or handled another way (Git LFS, a
# smaller re-freeze), not discovered again at push time.
LARGE_FILE_WARN_BYTES = 90 * 1024 * 1024

# Known-vocabulary terms the sweep checks for verbatim. Loaded from the git-ignored
# vocab file so this shipped source contains none of them (see the module docstring).
COMPETITION_VOCAB = list(_VOCAB["competition_vocab"])

# Process-ID patterns, re-run here as an independent regression check on the export
# specifically (this tool's exclusion rules were derived from an earlier, separate
# sweep of the source repository; this list checks the shipped artifact itself,
# which is the thing that actually matters). Loaded from the git-ignored vocab file
# for the same reason the competition vocabulary is: these patterns name the internal
# process, so a copy of them shipping inside the export is itself the meta-leak the
# sweep exists to find. With them here, the tool's first self-sweep flagged its own
# pattern list -- correctly. A scanner that always reports one known-benign hit
# teaches its reader to skim past hits, which is how the real one gets missed.
PROCESS_ID_PATTERNS = list(_VOCAB["process_id_patterns"])

def _rmtree_writable(path: Path) -> None:
    """shutil.rmtree, robust to read-only files, git marks loose object files
    read-only on Windows, so a plain rmtree() fails removing a .git directory with
    PermissionError. Clear the read-only bit and retry on failure."""
    def _on_rm_error(func, p, exc_info):
        os.chmod(p, 0o700)
        func(p)
    shutil.rmtree(path, onerror=_on_rm_error)


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dXx]\b")
WIN_PATH_RE = re.compile(r"[Cc]:[\\/][Uu]sers[\\/][^\\/\s\"']+")


# ======================================================================================
# Step 1, copy
# ======================================================================================

def _should_skip_dir(name: str) -> bool:
    return name in EXCLUDE_DIR_NAMES


def _should_skip_file(path: Path) -> bool:
    if path.name in EXCLUDE_FILE_NAMES:
        return True
    if path.suffix in EXCLUDE_FILE_SUFFIXES:
        return True
    if path.stem in PII_FILENAME_STEMS:
        return True
    try:
        rel = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = None
    if any(rel.startswith(pref) for pref in EXCLUDE_PATH_PREFIXES):
        return True
    if rel in EXCLUDE_RELATIVE_PATHS:
        return True
    return False


def _copy_tree(src: Path, dst: Path, file_count: list[int], large_files: list[tuple[Path, int]]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src.iterdir()):
        if entry.is_dir():
            if _should_skip_dir(entry.name):
                continue
            _copy_tree(entry, dst / entry.name, file_count, large_files)
        else:
            if _should_skip_file(entry):
                continue
            size = entry.stat().st_size
            if size >= LARGE_FILE_WARN_BYTES:
                large_files.append((entry, size))
            shutil.copy2(entry, dst / entry.name)
            file_count[0] += 1


# Variable names and target names pulled out of paper/Makefile for the export. Kept
# as an explicit list rather than "everything the fc27 target happens to reference"
# so a change to the source Makefile that this list hasn't been updated for fails
# LOUDLY (missing variable/target -> RuntimeError below) instead of silently
# shipping a Makefile with an undefined variable, exactly the class of bug an
# earlier version of this function had (FIGSRC was referenced by fc27-figs but its
# `FIGSRC = ...` definition line was never selected, so `cp $(FIGSRC)/...` expanded
# to a nonsense path and the export's own `make fc27` would have failed). Since
# G3b.2, the fc27 target is a one-line call into tools/build_fc27.py (which now
# owns figure/table regeneration and the includegraphics cross-check itself), so
# FIGSRC and fc27-figs no longer exist in the source Makefile -- this list was
# updated alongside that change, not left to drift from it.
_MAKEFILE_VARS = ["PAPER", "FC27DIR"]
_MAKEFILE_TARGETS = ["fc27", "fc27-clean", "clean", "cleanall", ".PHONY"]


def _extract_makefile_var(src: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\s*=.*$", src, re.MULTILINE)
    if not m:
        raise RuntimeError(f"paper/Makefile no longer defines {name!r}, update "
                            f"_MAKEFILE_VARS/_write_trimmed_makefile")
    return m.group(0)


def _extract_makefile_target(src: str, name: str) -> str:
    """Return a target's full block: its header line(s) (a target may have more than
    one header, e.g. `fc27: export ...` followed by `fc27: fc27-figs`), every recipe
    line (starts with a tab), comment lines immediately preceding the first header,
    and blank lines within the block, stopping at the next top-level (unindented,
    non-comment) line that isn't part of this same target."""
    lines = src.splitlines(keepends=True)
    header_re = re.compile(rf"^{re.escape(name)}\s*:")
    starts = [i for i, l in enumerate(lines) if header_re.match(l)]
    if not starts:
        raise RuntimeError(f"paper/Makefile no longer has a {name!r} target, update "
                            f"_MAKEFILE_TARGETS/_write_trimmed_makefile")
    first = starts[0]
    # walk backward over immediately preceding comment lines to capture the target's
    # own explanatory comment block
    start = first
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    # walk forward through every header line for this target plus all recipe/blank
    # lines that follow each one, stopping at the next non-recipe, non-blank,
    # non-comment, non-matching-header line
    end = first
    i = first
    while i < len(lines):
        if header_re.match(lines[i]):
            end = i + 1
            i += 1
            continue
        if lines[i].startswith("\t") or not lines[i].strip():
            end = i + 1
            i += 1
            continue
        break
    return "".join(lines[start:end])


def _write_trimmed_makefile(dst: Path) -> None:
    """paper/Makefile carries both the non-anonymized `paper` build and the fc27
    build. Only the fc27-relevant variables and targets belong in the export."""
    src_makefile = (REPO_ROOT / "paper" / "Makefile").read_text(encoding="utf-8")

    var_lines = [_extract_makefile_var(src_makefile, v) for v in _MAKEFILE_VARS]
    target_blocks = [_extract_makefile_target(src_makefile, t) for t in _MAKEFILE_TARGETS]

    trimmed = (
        "# Trimmed from the repository's paper/Makefile: fc27-build variables and\n"
        "# targets only. The non-anonymized `paper` build (paper.tex) is\n"
        "# intentionally not part of this artifact.\n"
        + "\n".join(var_lines) + "\n\n"
        + "\n".join(target_blocks)
    )

    for required in ("fc27:", "build_fc27.py"):
        if required not in trimmed:
            raise RuntimeError(f"trimmed Makefile is missing {required!r}, "
                                f"fix the trim logic, don't ship a broken Makefile")
    (dst / "Makefile").write_text(trimmed, encoding="utf-8")


def copy_whitelist(out: Path) -> tuple[int, list[tuple[Path, int]]]:
    """Copy exactly the reproducibility-relevant tree into `out`. Returns the file
    count and a list of (path, size) for any file at or above LARGE_FILE_WARN_BYTES
    that was copied anyway (not in EXCLUDE_RELATIVE_PATHS), the caller should
    treat a non-empty list as a warning to investigate, not ignore. Raises if `out`
    looks unsafe to write into (see main()'s pre-check)."""
    file_count = [0]
    large_files: list[tuple[Path, int]] = []

    _copy_tree(REPO_ROOT / "usdc_depeg", out / "usdc_depeg", file_count, large_files)

    # ONLY THE SHORT PAPER SHIPS. paper/fc27/ is the full manuscript: a different,
    # longer document that is not under review at this venue, is no longer tracked in
    # the source repository, and would hand a reviewer following the short paper's own
    # artifact URL the files for a paper they were not asked to read. It is excluded
    # here rather than by .gitignore, because this tool copies from the WORKING TREE and
    # would pick it up regardless of what git tracks.
    short_src = REPO_ROOT / "paper" / "fc27short"
    if not short_src.is_dir():
        raise AssertionError(f"the submitted paper is missing: {short_src}")
    _copy_tree(short_src, out / "paper" / "fc27short", file_count, large_files)

    # NO MAKEFILE. paper/Makefile's only build target is `fc27`, the full paper, which
    # this export no longer carries -- shipping it would hand a reviewer a target that
    # fails. The documented build is `python tools/build_fc27.py --target short`, which
    # needs no make at all. _write_trimmed_makefile is kept for the day a short target
    # exists to trim.
    (out / "paper").mkdir(parents=True, exist_ok=True)

    tools_dst = out / "tools"
    tools_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), tools_dst / "export_artifact.py")
    file_count[0] += 1

    # tests/test_figures.py imports figqa (`sys.path.insert(..., ".../tools")`) as
    # a genuine test-time dependency, not just a standalone dev convenience -- found
    # missing when step 3's reproduction inside a real export tried to collect that
    # test and failed with ModuleNotFoundError. Copied like export_artifact.py
    # itself, not swept into _copy_tree's usdc_depeg/ walk, because it lives under
    # tools/, one directory outside that tree.
    figqa_src = REPO_ROOT / "tools" / "figqa.py"
    shutil.copy2(figqa_src, tools_dst / "figqa.py")
    file_count[0] += 1

    # The trimmed Makefile's fc27 target (see _write_trimmed_makefile) is a one-line
    # call into this script, so it must exist in the export at the same relative
    # path (tools/build_fc27.py) the Makefile invokes, or `make fc27` inside the
    # export fails immediately.
    build_fc27_src = REPO_ROOT / "tools" / "build_fc27.py"
    shutil.copy2(build_fc27_src, tools_dst / "build_fc27.py")
    file_count[0] += 1

    # Same case as figqa.py above, and found the same way. G5b added
    # usdc_depeg/tests/test_punct_pass.py, which does
    # sys.path.insert(..., ".../tools") and imports punct_pass to check the
    # manuscript carries no em dash or prose semicolon. Without this copy, step 3's
    # reproduction inside a real export fails at test COLLECTION with
    # ModuleNotFoundError, before running anything.
    punct_src = REPO_ROOT / "tools" / "punct_pass.py"
    shutil.copy2(punct_src, tools_dst / "punct_pass.py")
    file_count[0] += 1

    return file_count[0], large_files


# ======================================================================================
# Step 2, identifier sweep
# ======================================================================================

def _git_config(key: str) -> str:
    try:
        return subprocess.run(["git", "config", key], cwd=REPO_ROOT,
                               capture_output=True, text=True, check=False
                               ).stdout.strip()
    except FileNotFoundError:
        return ""


# Below this length a "literal identity string" check is unreliable, collisions
# with ordinary vocabulary (a 3-character hostname, say, matching every "Jun" month
# abbreviation in a third-party dataset) swamp any real signal. Longer needles
# (typical names, emails, multi-word hostnames) are still checked, with a word
# boundary so a needle doesn't match as a substring of an unrelated longer word.
MIN_LITERAL_NEEDLE_LEN = 6

# Exact (relative POSIX path, line number) matches, individually investigated and
# recorded as benign in this project's own verification log -- NOT a pattern
# exemption. A pattern-level exemption (e.g. "ignore every ORCID in refs.bib") would
# silently swallow a genuine future leak of the same shape; this whitelist only ever
# suppresses the SAME line that was actually read and judged, so a leak on a
# different line of the same file still surfaces. Each entry names what was verified,
# not just that it was:
#   - refs.bib ORCID: a cited author's own public ORCID in a bibliography provenance
#     comment -- the author being cited, not this paper's own author.
#   - the labels-dataset line: a third-party Ethereum-address-labels CSV whose own
#     token vocabulary happens to collide with one of this tool's own internal
#     process-identifier patterns -- confirmed by inspection to be that dataset's
#     own label text, not
#     an injected process reference.
# Extend this set only after the same individual-file verification, never to silence
# a hit class wholesale.
KNOWN_BENIGN_HITS: dict[tuple[str, int], str] = {
    # refs.bib moved to the short paper when the full paper left the repository; the
    # line, and the ORCID on it, are unchanged (Crossref provenance for a CITED
    # author, Friedhelm Victor, not this paper's own).
    ("paper/fc27short/refs.bib", 519): "cited author's public ORCID, not this paper's own",
    ("usdc_depeg/data/n1/labels/dawsbot_eth-labels/accounts.csv", 137643):
        "third-party dataset's own label vocabulary, coincidental pattern match",
}


def _identity_needle_patterns() -> tuple[list[tuple[str, "re.Pattern"]], list[str]]:
    """Dynamic values (git identity, OS username, hostname), read at sweep time,
    never hardcoded anywhere in this module's own source (this file ships inside
    the export, so nothing PII-bearing may be written into it). Returns
    (needle_patterns, short_needles) -- the latter too short (<MIN_LITERAL_NEEDLE_LEN)
    to sweep reliably, reported as an advisory note by the caller rather than swept.
    Shared by sweep_identifiers() (the export-copy sweep) and
    tools/precommit_sweep.py (the staged-content sweep), so both apply the exact
    same identity check, not two independently-maintained copies of it."""
    git_name = _git_config("user.name")
    git_email = _git_config("user.email")
    os_user = getpass.getuser()
    hostname = socket.gethostname()

    literal_needles = [n for n in (git_name, git_email, os_user, hostname)
                        if n and len(n) >= MIN_LITERAL_NEEDLE_LEN]
    short_needles = [n for n in (git_name, git_email, os_user, hostname)
                      if n and 0 < len(n) < MIN_LITERAL_NEEDLE_LEN]
    needle_patterns = [(n, re.compile(r"\b" + re.escape(n) + r"\b")) for n in literal_needles]
    return needle_patterns, short_needles


def _scan_text(rel_posix: str, text: str, needle_patterns) -> list[tuple[str, int, str]]:
    """The actual per-line check, factored out so sweep_identifiers() (a directory
    walk over the export copy) and tools/precommit_sweep.py (a walk over staged
    file CONTENT, not paths on disk) run the identical scan rather than two
    independently-maintained copies of it. Returns unclassified (rel_posix, lineno,
    reason) tuples; the caller splits these into hits/benign against
    KNOWN_BENIGN_HITS."""
    located: list[tuple[str, int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for needle, pat in needle_patterns:
            if pat.search(line):
                located.append((rel_posix, lineno, f"literal identity string {needle!r}"))
        if EMAIL_RE.search(line) and "example.org" not in line and "example.com" not in line:
            located.append((rel_posix, lineno, "email-pattern match"))
        if ORCID_RE.search(line):
            located.append((rel_posix, lineno, "ORCID-pattern match"))
        if WIN_PATH_RE.search(line):
            located.append((rel_posix, lineno, "Windows user-path pattern"))
        for term in COMPETITION_VOCAB:
            if term in line:
                located.append((rel_posix, lineno, f"competition vocabulary {term!r}"))
        for pat in PROCESS_ID_PATTERNS:
            if re.search(pat, line):
                located.append((rel_posix, lineno, f"process-ID pattern {pat!r}"))
    return located


def _classify(located: list[tuple[str, int, str]]) -> tuple[list[str], list[str]]:
    """Split formatted (rel_posix, lineno, reason) hits into (hits, benign) against
    KNOWN_BENIGN_HITS, by exact (path, line) -- never by pattern (see
    KNOWN_BENIGN_HITS's own comment for why). Shared by sweep_identifiers() and
    tools/precommit_sweep.py so the two tools can never disagree about which hits
    are pre-verified benign."""
    hits: list[str] = []
    benign: list[str] = []
    for rel_posix, lineno, reason in located:
        formatted = f"{rel_posix}:{lineno}: {reason}"
        key = (rel_posix, lineno)
        if key in KNOWN_BENIGN_HITS:
            benign.append(f"{formatted}  [known-benign: {KNOWN_BENIGN_HITS[key]}]")
        else:
            hits.append(formatted)
    return hits, benign


def sweep_identifiers(out: Path) -> dict[str, list[str]]:
    """Sweep the EXPORT COPY (never the source repo) for identity- and process-
    revealing strings. Returns {"hits": [...], "benign": [...], "notes": [...]}:
    "hits" is what gates the export (empty means clean); "benign" holds the exact-
    match KNOWN_BENIGN_HITS entries, individually pre-verified, reported so a human
    still sees them rather than a scanner that silently drops known items -- the
    project's own standing rule (see this module's PROCESS_ID_PATTERNS comment: "a
    scanner that always reports one known-benign hit teaches its reader to skim past
    hits") is respected by keeping them VISIBLE and merely not gating, not by hiding
    them; "notes" holds advisory, non-location findings (the short-needle notice)
    that were never per-line hits to begin with and so were never eligible for the
    exact-match whitelist above."""
    needle_patterns, short_needles = _identity_needle_patterns()

    notes: list[str] = []
    if short_needles:
        notes.append(f"NOTE (not a per-line hit): {len(short_needles)} identity value(s) "
                      f"were too short (<{MIN_LITERAL_NEEDLE_LEN} chars) to sweep reliably "
                      f"and were skipped, verify manually that none of them appear "
                      f"identity-revealingly in the export.")

    located: list[tuple[str, int, str]] = []  # (rel_posix, lineno, reason)
    text_suffixes = {".py", ".md", ".tex", ".bib", ".json", ".txt", ".csv",
                      ".cfg", ".ini", ".gitattributes", ""}
    for path in sorted(out.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(out)
        rel_posix = rel.as_posix()
        if path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
            continue  # binary/rendered; anonymity checked separately for the PDF
        if path.suffix not in text_suffixes and path.name not in {".gitattributes"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        located.extend(_scan_text(rel_posix, text, needle_patterns))

    hits, benign = _classify(located)
    hits.extend(_sweep_binary_for_paths(out))
    return {"hits": hits, "benign": benign, "notes": notes}


def _sweep_binary_for_paths(out: Path) -> list[str]:
    """Defense-in-depth for exactly one confirmed failure mode: a compiled .pyc file
    embeds the ABSOLUTE SOURCE PATH of the .py it was built from in its code object
    (verified by inspection on this export: co_filename was the full local path, OS
    username included), and that is binary content the line-based text sweep above
    never looks at (.pyc isn't in its text_suffixes, deliberately, since decoding
    bytecode as UTF-8 text would be nonsense). Prevention (PYTHONDONTWRITEBYTECODE,
    -p no:cacheprovider, and clean_run_artifacts()'s post-hoc sweep) should mean no
    .pyc ever reaches this point, but this check exists so a future change to how
    step 3 runs, one that reintroduces bytecode caching without anyone noticing --
    still gets caught here rather than shipping silently. Scans every file's raw
    bytes for the Windows-path pattern, regardless of extension."""
    hits: list[str] = []
    win_path_bytes = re.compile(rb"[Cc]:[\\/][Uu]sers[\\/][^\\/\s\"']+")
    # The vocabulary is checked in raw bytes too, not only line-by-line as text: the
    # text pass skips any file whose suffix isn't in text_suffixes, so a term sitting
    # in a PDF, a .pyc, or an unrecognised extension would never be seen by it.
    vocab_bytes = [(term, re.compile(re.escape(term.encode("utf-8")), re.IGNORECASE))
                   for term in COMPETITION_VOCAB]
    for path in sorted(out.rglob("*")):
        if path.is_dir():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for m in win_path_bytes.finditer(data):
            hits.append(f"{path.relative_to(out)}: Windows user-path pattern in raw "
                        f"bytes (offset {m.start()}), likely embedded in a binary "
                        f"file such as a .pyc's code object")
        for term, pat in vocab_bytes:
            m = pat.search(data)
            if m:
                hits.append(f"{path.relative_to(out)}: competition vocabulary in raw "
                            f"bytes (offset {m.start()})")
    return hits


def sweep_pdf(out: Path) -> list[str]:
    """Separately check the exported PDF's metadata/XMP for identity leaks, a PDF
    is binary and the text sweep above skips it. Best-effort: uses pypdf/fitz if
    available, else reports that it could not check and the caller should verify by
    hand."""
    pdf_path = out / "paper" / "fc27short" / "fc27short.pdf"
    if not pdf_path.exists():
        return ["fc27short.pdf not present in export to check (expected after step 3)"]
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ["fitz (PyMuPDF) not available, PDF metadata NOT checked; verify by hand"]
    # Tool-signature and build-date fields, not identity fields, a standard
    # pdfTeX/hyperref build always populates creator/producer/format, and
    # creationDate/modDate are DELIBERATELY pinned to a fixed constant
    # (SOURCE_DATE_EPOCH, see paper/Makefile) for byte-reproducibility, not left at
    # the true build wall-clock. None of these five names the author. Flagging them
    # on every run would be noise a human has to re-dismiss each time, not a signal.
    benign_keys = {"creator", "producer", "trapped", "format", "creationdate", "moddate"}
    expected_date = "D:20260917000000Z"  # the pinned SOURCE_DATE_EPOCH, for reference
    hits = []
    doc = fitz.open(str(pdf_path))
    meta = doc.metadata or {}
    for k, v in meta.items():
        if k.lower() in benign_keys:
            if k.lower() in {"creationdate", "moddate"} and v != expected_date:
                hits.append(f"fc27.pdf metadata[{k}] = {v!r}, expected the pinned "
                            f"{expected_date!r}, SOURCE_DATE_EPOCH may not be taking "
                            f"effect in this build")
            continue
        if v and str(v).strip():
            hits.append(f"fc27.pdf metadata[{k}] = {v!r} (expected empty/generic)")
    if doc.xref_xml_metadata():
        hits.append("fc27.pdf carries an XMP metadata stream (expected none)")
    return hits


# ======================================================================================
# Step 3, reproduction
# ======================================================================================

def _run(cmd: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess:
    print(f"    $ {' '.join(cmd)}  (cwd={cwd})")
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _count_pytest_progress(stdout: str) -> tuple[int, int, int]:
    """(passed, failed, skipped), counted from a captured (non-TTY) `pytest -q`
    run, whose output under this project's pytest config never carries the usual
    "N passed in Xs" summary line -- only the dot/F/E/s/x/X progress characters,
    even on a fully green run.

    MEASURED BUG THIS FUNCTION FIXES: counting F/E anywhere across the WHOLE
    stdout (an earlier version of this logic, inlined separately at two call
    sites) double-counts characters from ORDINARY PROSE elsewhere in the
    output -- concretely, an absolute filesystem path embedded verbatim in a
    warning message can itself contain capital letters that happen to fall in
    "FE", and a warning class name (e.g. one ending "...Warning") can start
    with one too. None of that is a test result, and together they turned two
    genuinely 0-failure runs into reports of "3 failures" and "5 failures" at
    the two call sites -- confirmed by isolating the exact stdout of a run
    independently re-verified clean, running it through the old counting
    logic, and finding every hit sitting inside a file path or a warning
    name, never a "FAILED ..." line anywhere in the output.

    FIX: restrict counting to LINES made up ENTIRELY of progress-indicator
    characters (optionally followed by a "[ NN%]" marker) -- that is what
    actually isolates pytest's own progress row from prose that happens to
    contain the same letters; a path or warning line always carries other
    characters (spaces, other letters, backslashes) that this stricter
    per-line pattern excludes."""
    progress_lines = [
        ln for ln in stdout.splitlines()
        if re.fullmatch(r"[.FEsxX]+(\s*\[\s*\d{1,3}%\])?", ln.strip())
    ]
    progress_text = "".join(progress_lines)
    passed = progress_text.count(".")
    failed = sum(1 for c in progress_text if c in "FE")
    skipped = sum(1 for c in progress_text if c in "sxX")
    return passed, failed, skipped


def verify_reproduction(out: Path) -> dict:
    """Run the documented five-step sequence, then the short-paper build, inside the export,
    with ETHERSCAN_API_KEY explicitly absent from the environment. Returns a report
    dict; raises AssertionError on any check failure (test count, artifact hashes,
    PDF hash) so a caller cannot silently ship a broken export."""
    pkg = out / "usdc_depeg"
    env = {k: v for k, v in os.environ.items() if k != "ETHERSCAN_API_KEY"}
    assert "ETHERSCAN_API_KEY" not in env, "failed to strip the key from the child environment"
    # Running the reproduction sequence INSIDE the export directory writes bytecode
    # caches and a pytest cache into it as a side effect, and .pyc files embed the
    # ABSOLUTE source path in their code object (confirmed by inspection: co_filename
    # was the full local export path, OS username included). Left in place, those
    # would ship as part of the artifact and this tool's own identifier sweep would
    # NOT catch them (it only scans known text suffixes; .pyc is binary bytecode, not
    # text an anonymity-string search would reliably match). Preventing their creation
    # is more robust than cleaning up afterward, a cleanup pass depends on knowing
    # every place a cache could appear, prevention doesn't.
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    report: dict = {}

    steps = [
        (["python", "data_fetch.py", "verify"], "VERIFY OK"),
        (["python", "data_fetch_n1.py", "verify"], "VERIFY OK"),
    ]
    # The FIFO re-validation belongs to the on-chain attribution package, which this
    # artifact does not carry (see EXCLUDE_ONCHAIN_PATHS). Run it only where it shipped,
    # rather than failing on a documented step this artifact has no code for.
    if (pkg / "redemptions.py").exists():
        steps.append((["python", "redemptions.py", "--from-frozen"], None))
    steps += [
        (["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"], None),
        (["python", "report.py"], None),
    ]
    for cmd, expect_substr in steps:
        r = _run(cmd, pkg, env)
        if r.returncode != 0:
            raise AssertionError(
                f"reproduction step failed: {' '.join(cmd)}\nSTDOUT:\n{r.stdout[-3000:]}\n"
                f"STDERR:\n{r.stderr[-3000:]}")
        if expect_substr and expect_substr not in r.stdout:
            raise AssertionError(f"{' '.join(cmd)} did not report {expect_substr!r}:\n{r.stdout}")
        if cmd[:3] == ["python", "-m", "pytest"]:
            # This project's pytest config never emits the usual "N passed in Xs"
            # summary line under a captured (non-TTY) subprocess, confirmed
            # empirically, not a guess: only the dot/F/E progress characters appear,
            # even on a fully green run. Count those directly instead of regexing
            # for a line that will not be there -- via _count_pytest_progress
            # below, shared with the source-repo comparison later in this file so
            # the same counting bug can't recur in two places independently.
            m_passed, m_failed, m_skipped = _count_pytest_progress(r.stdout)
            n_passed = m_passed
            n_failed = m_failed
            report["pytest_passed"] = n_passed
            report["pytest_failed"] = n_failed
            report["pytest_skipped"] = m_skipped
            if n_failed:
                # A blind stdout[-3000:] tail can miss the actual FAILED lines
                # entirely when the warnings summary (which pytest prints AFTER
                # the failures) is itself long -- found exactly this gap while
                # diagnosing a real 3-failure run, where the tail carried only
                # the warnings summary. Pull out every "FAILED ..." line
                # explicitly, in addition to the tail, so the raised message is
                # never just noise.
                failed_lines = [ln for ln in r.stdout.splitlines() if ln.startswith("FAILED ")]
                raise AssertionError(
                    f"export pytest run has {n_failed} failure(s):\n"
                    + "\n".join(failed_lines)
                    + f"\n--- stdout tail ---\n{r.stdout[-3000:]}"
                )
            if n_passed == 0:
                raise AssertionError(f"export pytest run reports 0 passed, something is "
                                      f"wrong with output parsing or the run itself:\n"
                                      f"{r.stdout[-2000:]}")

    # The short target is the submission and the only paper the export carries, so it
    # is the one the export must prove rebuilds. There is no `make fc27` step any more:
    # the full paper is not here to build.
    short_tex = out / "paper" / "fc27short" / "fc27short.tex"
    if not short_tex.exists():
        raise AssertionError(f"the submitted paper is missing from the export: {short_tex}")
    r = _run([sys.executable, "tools/build_fc27.py", "--target", "short"], out, env)
    if r.returncode != 0:
        raise AssertionError(f"short-target build failed inside the export:\n"
                              f"STDOUT:\n{r.stdout[-3000:]}\nSTDERR:\n{r.stderr[-3000:]}")
    short_pdf = out / "paper" / "fc27short" / "fc27short.pdf"
    report["short_pdf_sha256"] = hashlib.sha256(short_pdf.read_bytes()).hexdigest()

    return report


def _artifact_hash_diff(out: Path) -> dict:
    """Compare every regenerated results/figs/tables artifact in the export against
    the same file in the source repo's working tree. Returns {agree, differ, missing,
    details}."""
    groups = [
        (REPO_ROOT / "usdc_depeg" / "results", out / "usdc_depeg" / "results", "*.json"),
        (REPO_ROOT / "usdc_depeg" / "figs", out / "usdc_depeg" / "figs", "*.pdf"),
        (REPO_ROOT / "usdc_depeg" / "figs", out / "usdc_depeg" / "figs", "*.sources.txt"),
        (REPO_ROOT / "paper" / "fc27short" / "tables",
         out / "paper" / "fc27short" / "tables", "*.tex"),
    ]
    agree = differ = missing = 0
    details = []
    for src_dir, dst_dir, pattern in groups:
        for src_file in sorted(src_dir.glob(pattern)):
            dst_file = dst_dir / src_file.name
            if not dst_file.exists():
                missing += 1
                details.append(("MISSING", str(src_file.relative_to(REPO_ROOT))))
                continue
            h1 = hashlib.sha256(src_file.read_bytes()).hexdigest()
            h2 = hashlib.sha256(dst_file.read_bytes()).hexdigest()
            if h1 == h2:
                agree += 1
            else:
                differ += 1
                details.append(("DIFFER", str(src_file.relative_to(REPO_ROOT))))
    return {"agree": agree, "differ": differ, "missing": missing, "details": details}


# ======================================================================================
# Step 4, finalize
# ======================================================================================

def clean_run_artifacts(out: Path) -> int:
    """Remove __pycache__/.pytest_cache directories, and any file matching
    EXCLUDE_FILE_SUFFIXES, anywhere under `out`. copy_whitelist() keeps these out of
    what's COPIED from the source repo, but verify_reproduction() runs the actual
    fc27 pdflatex/bibtex build INSIDE the export as its own reproduction check, which
    freshly CREATES fc27.aux/.log/.bbl/.blg/.out there -- a path copy_whitelist never
    sees. fc27.log in particular embeds the local machine's absolute MiKTeX path
    (so the OS username) dozens of times; caught only by a post-run sweep of the
    export tree, not by anything that runs before step 3. Belt and suspenders
    alongside PYTHONDONTWRITEBYTECODE/-p no:cacheprovider in verify_reproduction()
    for the __pycache__/.pytest_cache half: those prevent creation for the specific
    commands this tool runs, but this sweep catches anything created by another path
    (a tool version that skips step 3, a future step 3 command that doesn't inherit
    the same env, a package with its own caching behavior). Returns the count removed."""
    removed = 0
    for path in list(out.rglob("__pycache__")) + list(out.rglob(".pytest_cache")):
        if path.is_dir():
            _rmtree_writable(path)
            removed += 1
    for path in out.rglob("*"):
        if path.is_file() and path.suffix in EXCLUDE_FILE_SUFFIXES:
            path.unlink()
            removed += 1
    return removed


# Extensions normalized to LF. Deliberately a whitelist: a file not named here keeps its
# bytes exactly, which is the safe default for anything hash-pinned elsewhere.
TEXT_SUFFIXES_FOR_LF = {
    ".py", ".md", ".tex", ".bib", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".sha256", ".gitignore", ".bst", ".cls", ".sty",
}
# Never normalized, whatever the extension: usdc_depeg/data/ is the frozen snapshot, and
# usdc_depeg/data/MANIFEST.json pins a SHA-256 over each file's exact bytes. Rewriting a
# line ending there would change a hash the paper's own provenance rests on and break
# `python data_fetch.py verify` inside the export.
LF_EXCLUDE_PREFIXES = ("usdc_depeg/data/",)


def normalize_line_endings(out: Path) -> tuple[int, int]:
    """Rewrite CRLF to LF in the export's text files. Returns (converted, skipped_frozen).

    This exists because of a real, measured failure. `git config core.autocrlf` is true on
    the machine that builds the export and there is no .gitattributes, so git converted
    CRLF to LF when committing. The mirror then served LF while EXPORT_MANIFEST.sha256 had
    been computed over the CRLF working tree, and `sha256sum -c EXPORT_MANIFEST.sha256`
    failed on every text file. The failure presents as tampering, not as a line-ending
    conversion, which is the worst way for it to present in an anonymized artifact.

    Normalizing here, and writing a .gitattributes that stops git converting anything
    (see write_export_gitattributes), makes working-tree bytes, committed bytes and
    served bytes the same object for every file in the export.
    """
    converted = skipped = 0
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(out).as_posix()
        if rel.startswith(LF_EXCLUDE_PREFIXES):
            skipped += 1
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES_FOR_LF and path.name != "Makefile":
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:4096] or b"\r\n" not in raw:
            continue
        path.write_bytes(raw.replace(b"\r\n", b"\n"))
        converted += 1
    return converted, skipped


def _verify_manifest_matches_git(out: Path, manifest_path: Path) -> list[str]:
    """Check every manifest entry against the bytes git actually stored.

    The manifest is computed over the working tree. What a mirror serves is what git
    stored. Those were different objects once (core.autocrlf rewrote line endings on
    commit) and the divergence was invisible until a file was fetched back and hashed.
    This closes the loop locally instead: `git cat-file` gives the stored blob, and its
    SHA-256 must equal the manifest line.
    """
    entries = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        entries[rel] = digest

    r = subprocess.run(["git", "ls-files", "-s"], cwd=out, capture_output=True, text=True)
    if r.returncode != 0:
        return [f"git ls-files failed: {r.stderr.strip()}"]

    drift = []
    for line in r.stdout.splitlines():
        meta, rel = line.split("\t", 1)
        blob = meta.split()[1]
        if rel == manifest_path.name:
            continue
        want = entries.get(rel)
        if want is None:
            drift.append(f"{rel}: tracked by git but absent from the manifest")
            continue
        stored = subprocess.run(["git", "cat-file", "blob", blob], cwd=out,
                                capture_output=True)
        got = hashlib.sha256(stored.stdout).hexdigest()
        if got != want:
            drift.append(f"{rel}: manifest {want[:12]}... but git stored {got[:12]}...")
    return drift


def write_export_gitattributes(out: Path) -> Path:
    """Stop git rewriting line endings on commit, in either direction.

    Without this, `core.autocrlf=true` re-converts on `git add`, the committed and served
    bytes diverge from the working tree again, and normalize_line_endings above is undone
    silently between the manifest being written and the repository being pushed.
    """
    p = out / ".gitattributes"
    p.write_bytes(
        b"# Store every file's bytes verbatim.\n"
        b"#\n"
        b"# EXPORT_MANIFEST.sha256 is computed over the working tree. If git converted\n"
        b"# line endings on commit (core.autocrlf), the bytes this repository serves\n"
        b"# would not be the bytes the manifest attests, and `sha256sum -c` would fail\n"
        b"# on every text file. It has done exactly that before.\n"
        b"#\n"
        b"# Text files in this export are LF. The frozen snapshot under usdc_depeg/data/\n"
        b"# keeps its original bytes, because usdc_depeg/data/MANIFEST.json pins a\n"
        b"# SHA-256 over each of them.\n"
        b"* -text\n")
    return p


def prune_data_manifest(out: Path) -> dict:
    """Drop MANIFEST.json entries for data files this artifact does not carry.

    data/MANIFEST.json pins a SHA-256 over every frozen input. Excluding a data file
    without pruning its entry leaves a manifest that DESCRIBES A FILE THAT IS NOT THERE:
    `python data_fetch.py verify` fails, the manifest tests fail, and a reviewer
    reasonably concludes the download is incomplete rather than deliberately scoped.

    The pruned entries are recorded in the manifest itself, under a key that says why,
    so the absence reads as a decision rather than as loss.
    """
    path = out / "usdc_depeg" / "data" / "MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))

    dropped_files = sorted(k for k in manifest.get("files", {})
                            if k in EXCLUDED_DATA_RELATIVE)
    for k in dropped_files:
        del manifest["files"][k]

    before = len(manifest.get("sources", []))
    manifest["sources"] = [s for s in manifest.get("sources", [])
                            if str(s.get("file", "")) not in EXCLUDED_DATA_RELATIVE]
    dropped_sources = before - len(manifest["sources"])

    manifest["excluded_from_this_artifact"] = {
        "reason": "Only the short paper is submitted, and it uses no wallet-level "
                   "on-chain attribution. The redemption-concentration and DAI-contagion "
                   "analyses, and the address-label corpora they consume, are not part of "
                   "this artifact; their manifest entries are removed so that every entry "
                   "here describes a file that is present.",
        "files": sorted(EXCLUDED_DATA_RELATIVE),
    }

    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"files_dropped": dropped_files, "sources_dropped": dropped_sources,
             "files_remaining": len(manifest.get("files", {})),
             "sources_remaining": len(manifest.get("sources", []))}


def write_export_manifest(out: Path) -> Path:
    manifest_path = out / "EXPORT_MANIFEST.sha256"
    lines = []
    for path in sorted(out.rglob("*")):
        if path.is_dir() or path == manifest_path:
            continue
        # Never hash .git/. Today the manifest is written before init_export_git_repo, so
        # there is nothing there to hash, and a --force re-run removes any prior copy. Both
        # of those are ordering accidents. If either changed, this walk would hash
        # .git/config, which carries the push remote and therefore the author's account
        # name, straight into the file a reviewer is told to trust.
        if ".git" in path.relative_to(out).parts:
            continue
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        rel = path.relative_to(out).as_posix()
        lines.append(f"{h}  {rel}")
    # newline="\n" is load-bearing, not tidiness. Python's text mode translates "\n" to
    # the platform ending, so on Windows every manifest line ended CRLF and `sha256sum -c`
    # read each path with a trailing carriage return: "paper/fc27/fc27.pdf\r: No such file
    # or directory", for all 259 entries. The manifest was the last file still carrying
    # the defect the normalization pass exists to remove, because it is written after it.
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return manifest_path


def _pdf_digest(out: Path, rel: str) -> str:
    p = out / rel
    if not p.exists():
        return "(not built in this export)"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_export_readme(out: Path) -> None:
    """The README a reviewer reads first. It must say which paper was submitted.

    The export carries two manuscripts built from one analysis package. A reviewer who
    followed the short paper's artifact URL and found only the regular paper's files would
    reasonably conclude the link was wrong.
    """
    readme = out / "README.md"
    short_hash = _pdf_digest(out, "paper/fc27short/fc27short.pdf")
    reg_hash = _pdf_digest(out, "paper/fc27/fc27.pdf")
    readme.write_text(
        "# Anonymized artifact, FC 2027 submission\n\n"
        "This is a content export: a snapshot of files, not a git clone. It carries "
        "no commit history from the source repository.\n\n"
        "## The two manuscripts\n\n"
        "One body of work was written up as two separate submissions, built by one "
        "script from one set of generated figures and table fragments.\n\n"
        "| | Path | Pages | SHA-256 of the built PDF |\n"
        "|---|---|---|---|\n"
        "| **Short paper (SUBMITTED)** | `paper/fc27short/fc27short.pdf` | 8 of main "
        f"text, 11 including references | `{short_hash}` |\n"
        "| Regular paper (not submitted) | `paper/fc27/fc27.pdf` | 15 of main text, 26 "
        f"including references and appendices | `{reg_hash}` |\n\n"
        "**The short paper is the submission.** The regular version is included because "
        "it is the source of every number the short paper carries and it holds the "
        "printed claim map, the redemption-concentration result and the appendices that "
        "the 8-page budget excluded. It is kept correct, and it is not under review.\n\n"
        "## Layout\n\n"
        "- `usdc_depeg/`, the analysis package: code, the frozen data snapshot "
        "(`data/`, `data/MANIFEST.json`), computed results (`results/`), generated "
        "figures (`figs/`), and the test suite (`tests/`).\n"
        "- `paper/fc27short/`, the submitted manuscript: `fc27short.tex`, its sections, "
        "table fragments (`tables/`), figures (`figs/`), and the built PDF.\n"
        "- `paper/fc27/`, the regular manuscript: `fc27.tex`, `refs.bib`, table "
        "fragments, figures, and the built PDF. Both papers share `paper/fc27/refs.bib`.\n"
        "- `paper/Makefile`, trimmed to the `fc27` build target.\n"
        "- `EXPORT_MANIFEST.sha256`, SHA-256 of every file in this export "
        "(`sha256sum -c EXPORT_MANIFEST.sha256` to verify after transfer).\n"
        "- `.gitattributes`, marking every file `-text` so the stored bytes are the "
        "bytes the manifest attests.\n\n"
        "## Reproduction\n\n"
        "Requires Python 3 with the packages in `usdc_depeg/requirements.txt`, and a "
        "LaTeX distribution providing the `llncs` document class and `splncs04` "
        "bibliography style (both are standard Springer LNCS files; not vendored here "
        "since they are not part of this repository's own history).\n\n"
        "From `usdc_depeg/`, offline and keyless (no `ETHERSCAN_API_KEY` is read or "
        "required):\n\n"
        "```\n"
        "python data_fetch.py verify           # check data/*.csv against MANIFEST.json's hashes\n"
        "python data_fetch_n1.py verify        # same check for the N1 cross-venue series\n"
        "python -m pytest                      # full test suite\n"
        "python report.py                      # regenerate every results/*.json, every\n"
        "                                       # figure, and every paper table fragment\n"
        "```\n\n"
        "Then, from the export root, build the paper:\n\n"
        "```\n"
        "python tools/build_fc27.py --target short    # the submitted paper\n"
        "```\n\n"
        "Each build is byte-reproducible: `SOURCE_DATE_EPOCH` is pinned by the build "
        "script and `\\pdftrailerid{}` is empty, so a rebuild of unmodified source "
        "reproduces the SHA-256 in the table above. The build fails rather than warns on "
        "an undefined reference, a multiply-defined label, an over-wide table, a float "
        "too large for the page, or any text set outside the page box.\n\n"
        "None of these steps overwrites an already-frozen file under `data/`, and none "
        "requires network access.\n\n"
        "## Line endings\n\n"
        "Text files here use LF. The frozen snapshot under `usdc_depeg/data/` keeps its "
        "original bytes, because `usdc_depeg/data/MANIFEST.json` pins a SHA-256 over "
        "each of them and rewriting a line ending there would break that check. "
        "`EXPORT_MANIFEST.sha256` is computed after normalization, so it attests the "
        "bytes this repository actually stores and serves.\n",
        encoding="utf-8", newline="\n")


def init_export_git_repo(out: Path) -> None:
    """A fresh, single-commit git repository under a generic identity, distinct
    from `git clone`, this repo's history starts here and carries nothing from the
    source repository (which is the whole point of a content export)."""
    env = dict(os.environ)
    git_id = ["-c", "user.name=Anonymous", "-c", "user.email=anonymous@example.org"]

    def run_git(*args: str) -> subprocess.CompletedProcess:
        r = subprocess.run(["git", *git_id, *args], cwd=out, capture_output=True,
                            text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")
        return r

    if (out / ".git").exists():
        _rmtree_writable(out / ".git")
    run_git("init", "-q")
    run_git("add", "-A")
    run_git("commit", "-q", "-m", "Artifact for FC 2027 submission")

    log = run_git("log", "--format=%an <%ae>").stdout.strip().splitlines()
    if any(line != "Anonymous <anonymous@example.org>" for line in log):
        raise AssertionError(f"export git log carries a non-anonymous identity: {log}")
    count = run_git("log", "--oneline").stdout.strip().splitlines()
    if len(count) != 1:
        raise AssertionError(f"export git log has {len(count)} commits, expected 1")


# ======================================================================================
# main
# ======================================================================================

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                     default=REPO_ROOT.parent / "usdc_depeg_export")
    ap.add_argument("--skip-repro", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out: Path = args.out.resolve()

    if out.exists():
        looks_like_prior_export = (out / "usdc_depeg").is_dir() or (out / "EXPORT_MANIFEST.sha256").exists()
        if not args.force and not looks_like_prior_export:
            print(f"refusing to write into {out}: it exists and does not look like a "
                  f"prior export of this tool (no usdc_depeg/ or EXPORT_MANIFEST.sha256 "
                  f"found there). Pass --force to overwrite anyway, or choose a "
                  f"different --out.", file=sys.stderr)
            return 1
        _rmtree_writable(out)
    out.mkdir(parents=True)

    print(f"[1/4] copying whitelist to {out} ...")
    n_files, large_files = copy_whitelist(out)
    print(f"      {n_files} files copied")
    if large_files:
        print(f"      WARNING: {len(large_files)} file(s) at or above "
              f"{LARGE_FILE_WARN_BYTES // (1024*1024)}MB were copied, GitHub hard-rejects "
              f"any file >= 100MB. Investigate each: is it load-bearing (grep every .py "
              f"for a READ of it, not just its filename, three files here share one name "
              f"and only one turned out to matter)? If not, add its exact repo-relative "
              f"path to EXCLUDE_RELATIVE_PATHS. If it is, this export will need Git LFS or "
              f"a smaller re-freeze before it can be pushed.")
        for p, size in large_files:
            print(f"        {p.relative_to(REPO_ROOT).as_posix()}  ({size / (1024*1024):.1f}MB)")

    pruned = prune_data_manifest(out)
    print(f"      data manifest pruned: {len(pruned['files_dropped'])} files{{}} entr(ies) "
          f"and {pruned['sources_dropped']} sources[] entr(ies) removed for excluded "
          f"data; {pruned['files_remaining']} files / {pruned['sources_remaining']} "
          f"sources remain")

    print("[2/4] sweeping the export for identifiers ...")
    sweep = sweep_identifiers(out)
    hits = sweep["hits"]
    if hits:
        print(f"      {len(hits)} hit(s) found:")
        for h in hits:
            print(f"        {h}")
    else:
        print("      0 hits")
    for n in sweep["notes"]:
        print(f"      {n}")
    if sweep["benign"]:
        print(f"      {len(sweep['benign'])} known-benign item(s), pre-verified, "
              f"not gating (KNOWN_BENIGN_HITS, exact match):")
        for b in sweep["benign"]:
            print(f"        {b}")

    repro_report = {}
    if not args.skip_repro:
        print("[3/4] running the documented reproduction inside the export ...")
        repro_report = verify_reproduction(out)
        diff = _artifact_hash_diff(out)
        print(f"      pytest: {repro_report.get('pytest_passed')} passed, "
              f"{repro_report.get('pytest_failed')} failed")
        # Compared against the SOURCE repo's own count, not a hardcoded number --
        # a hardcoded expected count goes stale the next time a test is added,
        # exactly the class of self-inflicted fragility this whole export tool
        # exists to avoid (see the ordering_null.py fix this same pass depends on).
        # `--collect-only` here would be cheaper than a full re-run, but this
        # project's pytest config prints per-FILE counts under `--collect-only`
        # ("tests/test_x.py: N"), not a single collected-total line, so a full
        # run, dot-counted the same way as the export's own run above, is what
        # actually gives a comparable number.
        src_r = subprocess.run(["python", "-m", "pytest", "-q"],
                                cwd=REPO_ROOT / "usdc_depeg", capture_output=True, text=True)
        src_n, src_failed, _src_skipped = _count_pytest_progress(src_r.stdout)
        # ... minus the tests the export DELIBERATELY does not carry. The on-chain
        # attribution package is excluded (EXCLUDE_ONCHAIN_PATHS), and so are its tests,
        # so the source repo legitimately runs more tests than the export. This number is
        # DERIVED from the exclusion list, not pinned, for the same reason src_n is: a
        # hardcoded difference goes stale the moment a test is added to either side. A
        # warning that fires on a difference the operator already knows about is worse
        # than no warning -- it teaches them to scroll past the one that matters.
        excluded_rel = sorted(p[len("usdc_depeg/"):] for p in EXCLUDE_ONCHAIN_PATHS
                               if p.startswith("usdc_depeg/tests/test_"))
        n_excluded_tests = 0
        if excluded_rel:
            cr = subprocess.run(["python", "-m", "pytest", "-q", "--collect-only",
                                 *excluded_rel],
                                cwd=REPO_ROOT / "usdc_depeg", capture_output=True, text=True)
            # This project's pytest config prints one "tests/test_x.py: N" line per file.
            for line in cr.stdout.splitlines():
                m = re.match(r"^\s*tests[\\/]\S+\.py:\s*(\d+)\s*$", line)
                if m:
                    n_excluded_tests += int(m.group(1))
            if n_excluded_tests == 0:
                print(f"      WARNING: could not count the {len(excluded_rel)} excluded "
                      f"test file(s) in the source repo, so the comparison below is "
                      f"against an unadjusted total")
        src_expected = src_n - n_excluded_tests
        if src_failed:
            print(f"      WARNING: the SOURCE repo's own test suite has {src_failed} "
                  f"failure(s) right now, fix that before trusting any comparison here")
        elif src_expected != repro_report.get("pytest_passed") + repro_report.get("pytest_skipped", 0):
            print(f"      WARNING: export passed {repro_report.get('pytest_passed')} "
                  f"and skipped {repro_report.get('pytest_skipped', 0)} tests but the "
                  f"source repo passes {src_n} less {n_excluded_tests} deliberately "
                  f"excluded = {src_expected}, reconcile before "
                  f"submitting (a whitelist/exclude rule may be dropping a test file)")
        elif repro_report.get("pytest_skipped"):
            # Not a dropped test. The documented sequence runs pytest BEFORE building
            # the paper, and at least one test needs the build's .aux file, which the
            # export deliberately excludes. Counting a skip as a loss sent a previous
            # run hunting a whitelist bug that was not there.
            print(f"      test count reconciles with the source repo: "
                  f"{repro_report.get('pytest_passed')} passed + "
                  f"{repro_report['pytest_skipped']} skipped = {src_expected} "
                  f"({src_n} in the repo less {n_excluded_tests} in the "
                  f"{len(excluded_rel)} deliberately excluded test file(s)). Skips here "
                  f"are tests that need a built paper, which the sequence builds after.")
        else:
            print(f"      test count matches the source repo: {src_expected} "
                  f"({src_n} less {n_excluded_tests} deliberately excluded)")
        print(f"      artifact hashes: {diff['agree']} agree / {diff['differ']} differ / "
              f"{diff['missing']} missing")
        for status, name in diff["details"]:
            print(f"        {status} {name}")
        print(f"      SHORT PDF SHA-256  : {repro_report.get('short_pdf_sha256')} "
              f"<- the submission")
        if CHECKLIST.exists():
            checklist_text = CHECKLIST.read_text(encoding="utf-8")
            for label, key in (("SHORT", "short_pdf_sha256"),):
                h = repro_report.get(key)
                if h and h not in checklist_text:
                    print(f"      WARNING: the {label} PDF hash is not present in "
                          f"{CHECKLIST.relative_to(REPO_ROOT)}, reconcile before submitting")
        pdf_hits = sweep_pdf(out)
        if pdf_hits:
            print(f"      PDF metadata sweep: {len(pdf_hits)} item(s):")
            for h in pdf_hits:
                print(f"        {h}")
        else:
            print("      PDF metadata sweep: clean")
    else:
        print("[3/4] SKIPPED (--skip-repro), export is NOT submission-ready")

    print("[4/4] finalizing: manifest, README, git init ...")
    n_cleaned = clean_run_artifacts(out)
    if n_cleaned:
        print(f"      removed {n_cleaned} __pycache__/.pytest_cache director"
              f"{'y' if n_cleaned == 1 else 'ies'} created by step 3's reproduction run")
    # Order matters and is load-bearing. Normalize first, then write the files the
    # manifest must cover, then hash. The manifest has to be the LAST thing computed, or
    # it attests bytes that a later step has already changed -- which is precisely the
    # defect this normalization exists to fix.
    converted, skipped_frozen = normalize_line_endings(out)
    print(f"      line endings: {converted} file(s) converted to LF, "
          f"{skipped_frozen} frozen file(s) under usdc_depeg/data/ left byte-identical")
    gitattributes_path = write_export_gitattributes(out)
    write_export_readme(out)  # written BEFORE the manifest so the manifest covers it
    manifest_path = write_export_manifest(out)
    init_export_git_repo(out)

    # The manifest is only worth anything if it matches what git stored. Re-hash the
    # working tree against the committed blobs and fail loudly on any divergence.
    drift = _verify_manifest_matches_git(out, manifest_path)
    if drift:
        print(f"      ERROR: {len(drift)} file(s) differ between the manifest and what "
              f"git stored, so `sha256sum -c` will fail against the mirror:")
        for d in drift[:10]:
            print(f"        {d}")
        raise SystemExit(3)
    print(f"      manifest verified against git's stored blobs: no divergence")
    print(f"      wrote {gitattributes_path.name}")
    print(f"      wrote {manifest_path.relative_to(out)}, README.md, and a single-commit "
          f"git repo under the Anonymous identity")

    print()
    print(f"Export complete: {out}")
    print(f"  files: {n_files}")
    print(f"  identifier sweep hits: {len(hits)}")
    if repro_report:
        print(f"  pytest: {repro_report.get('pytest_passed')} passed")
        print(f"  PDF SHA-256: {repro_report.get('short_pdf_sha256')}")
    return 0 if not hits else 2


if __name__ == "__main__":
    raise SystemExit(main())
