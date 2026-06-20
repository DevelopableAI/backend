import subprocess
import sys
from pathlib import Path


class GitClient:
    """
    Thin wrapper around the git CLI scoped to a single working directory.

    Replaces the repeated local git() closures that appeared inside
    _push_gitignore_to_github, _push_workflow_to_github (Deployment), and
    _git (VersionControl) — all three had identical implementations.
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def run(self, *args: str, check: bool = True) -> tuple[int, str]:
        """Run a git subcommand. Returns (returncode, combined stdout+stderr)."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.cwd,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                ["git", *args],
                result.stdout,
                result.stderr,
            )
        return result.returncode, (result.stdout + result.stderr).strip()

    def ok(self, *args: str) -> bool:
        """Run a git subcommand; return True on success, False on failure."""
        rc, _ = self.run(*args, check=False)
        return rc == 0

    def output(self, *args: str) -> str:
        """Run a git subcommand and return stdout on success, '' on failure."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.cwd,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def remote_url(self, name: str = "origin") -> str | None:
        """Return the URL of a remote, or None if it is not configured."""
        url = self.output("remote", "get-url", name)
        return url or None

    def has_remote(self, name: str = "origin") -> bool:
        return self.remote_url(name) is not None

    def commit(self, message: str, *paths: str) -> bool:
        """Stage paths (or all if none given) and commit. Returns True on success."""
        if paths:
            if not self.ok("add", *paths):
                return False
        else:
            if not self.ok("add", "."):
                return False
        return self.ok("commit", "-m", message)

    def push(self, remote: str = "origin", branch: str = "main") -> bool:
        """Push branch to remote. Prints a warning on failure (non-fatal)."""
        rc, out = self.run("push", remote, branch, check=False)
        if rc != 0:
            print(
                f"\n  Warning: could not push to {remote}/{branch}.\n"
                f"  Push manually: cd {self.cwd} && git push {remote} {branch}\n"
                f"  ({out})",
                file=sys.stderr,
            )
            return False
        return True

    def config_get(self, key: str, scope: str = "--global") -> str:
        """Read a git config value. Returns '' if not set."""
        result = subprocess.run(
            ["git", "config", scope, key],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def config_set(self, key: str, value: str, scope: str = "--global") -> None:
        subprocess.run(["git", "config", scope, key, value], check=False)

    def config_unset(self, key: str, scope: str = "--local") -> None:
        subprocess.run(
            ["git", "config", scope, "--unset", key],
            cwd=self.cwd,
            capture_output=True,
        )
