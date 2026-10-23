"""Validate public GitHub repository URLs and clone them safely for analysis.

Only https://github.com/<owner>/<repo> is accepted. The clone URL is rebuilt
from the validated owner and name, so user input never reaches git directly.
Cloning:
- runs git with an argument list (no shell) and a timeout;
- disables credential prompts and stored credentials, so only public
  repositories can be cloned and the server's own GitHub access is never used;
- allows only the https protocol and no redirects;
- uses --no-checkout: mining reads git history only, so no repository files
  are written to disk and nothing from the repository is ever executed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

GITHUB_HOST = "github.com"
# GitHub's own limits: owners are 1-39 alphanumerics or single hyphens; repository
# names are up to 100 letters, digits, ".", "-" and "_".
OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class InvalidGitHubURL(ValueError):
    """The URL is not a supported public GitHub repository URL."""


class CloneError(RuntimeError):
    """Cloning failed; the message is safe to show to users (no server paths)."""


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://{GITHUB_HOST}/{self.owner}/{self.name}.git"

    @property
    def web_url(self) -> str:
        return f"https://{GITHUB_HOST}/{self.owner}/{self.name}"


def parse_github_url(url: str) -> GitHubRepo:
    """Return the repository for an https://github.com/<owner>/<repo> URL, or raise InvalidGitHubURL."""
    text = (url or "").strip()
    if not text or len(text) > 300 or any(c.isspace() or ord(c) < 32 for c in text):
        raise InvalidGitHubURL("Enter a GitHub repository URL like https://github.com/owner/repo.")
    parts = urlsplit(text)
    if parts.scheme.lower() != "https":
        raise InvalidGitHubURL("Only https:// GitHub URLs are supported.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidGitHubURL("URLs with credentials are not allowed.")
    try:
        port = parts.port
    except ValueError:
        raise InvalidGitHubURL("The URL has an invalid port.")
    host = (parts.hostname or "").lower()
    if host not in (GITHUB_HOST, f"www.{GITHUB_HOST}") or port not in (None, 443):
        raise InvalidGitHubURL("Only repositories on github.com are supported.")
    if parts.query or parts.fragment:
        raise InvalidGitHubURL("Use the repository's main URL, without ?query or #fragment.")
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) != 2:
        raise InvalidGitHubURL("Use the repository's main URL: https://github.com/owner/repo.")
    owner, name = segments
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not OWNER_PATTERN.match(owner) or not NAME_PATTERN.match(name) or name in (".", ".."):
        raise InvalidGitHubURL("That is not a valid GitHub owner/repository name.")
    return GitHubRepo(owner, name)


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_TERMINAL_PROMPT="0",       # never wait for a username/password
        GIT_ASKPASS="echo",            # ...even if a GUI askpass is configured
        GCM_INTERACTIVE="never",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_LFS_SKIP_SMUDGE="1",
    )
    return env


def clone_repository(repo: GitHubRepo, dest: Path, timeout: float) -> None:
    """Clone the default branch's full history (no working tree) into `dest`."""
    cmd = [
        "git",
        "-c", "credential.helper=",          # don't use this machine's stored credentials
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        "-c", "http.followRedirects=false",
        "-c", "core.hooksPath=/dev/null",
        "clone", "--quiet", "--no-checkout", "--single-branch", "--no-tags",
        "--", repo.clone_url, str(dest),
    ]
    try:
        subprocess.run(cmd, env=_git_env(), capture_output=True, text=True, timeout=timeout,
                       check=True, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise CloneError(f"Cloning {repo.full_name} took longer than {int(timeout)} seconds and was stopped.")
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").lower()
        if "not found" in stderr or "could not read username" in stderr or "authentication" in stderr:
            raise CloneError(f"{repo.full_name} was not found on GitHub, or it is not public.")
        if "could not resolve host" in stderr or "unable to access" in stderr:
            raise CloneError("GitHub could not be reached from the server. Check its network connection.")
        raise CloneError(f"git could not clone {repo.full_name} (exit code {exc.returncode}).")
    except FileNotFoundError:
        raise CloneError("git is not installed on the server.")


def directory_size_mb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total / (1024 * 1024)


def commit_count(path: Path, timeout: float = 60) -> int:
    out = subprocess.run(["git", "-C", str(path), "rev-list", "--count", "HEAD"], env=_git_env(),
                         capture_output=True, text=True, timeout=timeout, check=True)
    return int(out.stdout.strip() or 0)


def check_limits(repo: GitHubRepo, path: Path, max_mb: float, max_commits: int) -> None:
    size = directory_size_mb(path)
    if size > max_mb:
        raise CloneError(f"{repo.full_name} is {size:.0f} MB, above this server's {max_mb:.0f} MB limit.")
    try:
        commits = commit_count(path)
    except (subprocess.SubprocessError, ValueError):
        raise CloneError(f"{repo.full_name} has no readable commit history.")
    if commits > max_commits:
        raise CloneError(f"{repo.full_name} has {commits:,} commits, above this server's {max_commits:,} limit.")


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
