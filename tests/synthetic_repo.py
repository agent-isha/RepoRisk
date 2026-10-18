"""Build a small, deterministic git repository with dated commits.

Used by the API tests and by scripts/capture_frontend_fixtures.py, so the
frontend fixtures are real API output for a real (synthetic) repository.
"""

import os
import random
import subprocess
from datetime import datetime, timedelta, timezone

START = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
FILES = {
    "src/core.py": 0.45,       # probability that a change to this file is a bug fix
    "src/models.py": 0.35,
    "src/utils.py": 0.1,
    "src/cli.py": 0.05,
    "src/io.py": 0.05,
}
EXTRA = ["tests/test_core.py", "README.md"]


def _git(repo, *args, date=None, email="dev@example.com"):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Dev", "GIT_COMMITTER_NAME": "Dev",
           "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date.isoformat()
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def build(repo: str, days: int = 240, step: int = 2, seed: int = 7) -> str:
    """Create the repository at `repo` (an empty or missing directory) and return its path."""
    rng = random.Random(seed)
    os.makedirs(repo, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    contents = {path: [f"value_{i} = {i}" for i in range(12)] for path in list(FILES) + EXTRA}
    authors = ["ana@example.com", "ben@example.com", "chen@example.com"]

    def write(path):
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
        with open(full, "w") as f:
            f.write("\n".join(contents[path]) + "\n")

    for path in contents:
        write(path)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial project layout", date=START, email=authors[0])

    for n, day in enumerate(range(step, days, step), start=1):
        path = rng.choice(list(FILES))
        is_fix = rng.random() < FILES[path]
        line = rng.randrange(len(contents[path]))
        contents[path][line] = f"value_{line} = {rng.randrange(1000)}  # rev {n}"
        write(path)
        touched = [path]
        if rng.random() < 0.3:  # sometimes a test changes in the same commit
            contents["tests/test_core.py"].append(f"assert check_{n}()")
            write("tests/test_core.py")
            touched.append("tests/test_core.py")
        name = os.path.basename(path)
        message = (f"Fix crash in {name} when input is empty" if is_fix
                   else rng.choice([f"Add option to {name}", f"Refactor {name}", f"Tidy {name} logic"]))
        _git(repo, "add", *touched)
        _git(repo, "commit", "-q", "-m", message, date=START + timedelta(days=day),
             email=rng.choice(authors))
    return repo
