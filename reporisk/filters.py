"""Decide which file paths are noise and should be left out of rankings.

Every rule lives in one of the sets below; add to them to extend the filter.
Paths are matched as repo-relative POSIX paths, e.g. "src/app/models.py".
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Iterable

# Exact file names that are never hand-written source.
EXCLUDED_FILENAMES = frozenset({
    "Pipfile.lock",
    "poetry.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    "go.sum",
    ".DS_Store",
})

# File suffixes (lowercase) for certificates, logs, compiled output,
# minified/generated bundles and binary assets.
EXCLUDED_SUFFIXES = (
    # certificates and keys
    ".pem", ".key", ".crt",
    # logs
    ".log",
    # compiled / cache
    ".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe",
    # generated web assets
    ".min.js", ".min.css", ".map",
    # binary design, image and font assets
    ".ai", ".psd", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
    ".woff", ".woff2", ".ttf", ".eot",
)

# Directory names that hold caches, build output or third-party code,
# matched anywhere in the path.
EXCLUDED_DIRS = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox",
    ".venv", "venv", "node_modules", "bower_components",
    "vendor", "_vendor", "vendored", "site-packages",
    "dist", "htmlcov", ".next",
})

# Directory names treated as vendored only when nested inside another
# directory. A top-level "packages/" is usually first-party monorepo code,
# while "<pkg>/packages/" is the old Python vendoring layout
# (e.g. requests/packages/urllib3).
NESTED_ONLY_EXCLUDED_DIRS = frozenset({"packages"})

# Documentation directories, matched anywhere in the path.
DOC_DIRS = frozenset({"docs", "doc", "documentation"})

# Documentation and changelog file names, matched case-insensitively on the part
# before the first dot (so "README", "readme.md" and "README.zh-CN.md" all match),
# and only when the file has no extension or a documentation extension. This keeps
# source files such as "changes.py" in the ranking.
DOC_FILE_STEMS = frozenset({
    "README", "HISTORY", "CHANGELOG", "CHANGES", "NEWS",
    "RELEASE_NOTES", "RELEASES", "CONTRIBUTING",
})
DOC_EXTENSIONS = frozenset({"", ".md", ".markdown", ".rst", ".txt", ".adoc", ".org"})

TEST_DIRS = frozenset({"test", "tests", "__tests__", "testing", "spec", "specs"})
TEST_FILE_PATTERNS = (
    "test_*.py", "*_test.py", "*_test.go",
    "*.test.js", "*.test.jsx", "*.test.ts", "*.test.tsx",
    "*.spec.js", "*.spec.jsx", "*.spec.ts", "*.spec.tsx",
    "*Test.java", "*Tests.java", "*_test.cpp", "*_test.cc",
)


def _parts(path: str) -> PurePosixPath:
    return PurePosixPath(path.replace("\\", "/"))


def is_test_file(path: str) -> bool:
    """True if the path looks like a test file or lives in a test directory."""
    p = _parts(path)
    if any(d in TEST_DIRS for d in p.parts[:-1]):
        return True
    return any(fnmatch(p.name, pattern) for pattern in TEST_FILE_PATTERNS)


def is_doc_file(path: str) -> bool:
    """True if the path is a README/changelog-style file or lives in a docs directory."""
    p = _parts(path)
    if any(d in DOC_DIRS for d in p.parts[:-1]):
        return True
    stem = p.name.split(".", 1)[0].upper()
    return stem in DOC_FILE_STEMS and p.suffix.lower() in DOC_EXTENSIONS


def is_noise(path: str, extra_patterns: Iterable[str] = ()) -> bool:
    """True if the path is a lock file, key, log, cache, generated or vendored file.

    `extra_patterns` are glob patterns matched against the whole path,
    e.g. "docs/*" or "*.rst".
    """
    p = _parts(path)
    if p.name in EXCLUDED_FILENAMES:
        return True
    if p.name.lower().endswith(EXCLUDED_SUFFIXES):
        return True

    dirs = p.parts[:-1]
    if any(d in EXCLUDED_DIRS for d in dirs):
        return True
    if any(d in NESTED_ONLY_EXCLUDED_DIRS for d in dirs[1:]):
        return True

    posix = p.as_posix()
    return any(fnmatch(posix, pattern) for pattern in extra_patterns)


def is_relevant(
    path: str,
    *,
    include_tests: bool = True,
    include_docs: bool = False,
    extra_patterns: Iterable[str] = (),
) -> bool:
    """True if the path should appear in a ranking."""
    if is_noise(path, extra_patterns):
        return False
    if not include_docs and is_doc_file(path):
        return False
    if not include_tests and is_test_file(path):
        return False
    return True
