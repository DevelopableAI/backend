import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

_PUSH_MAX_RETRIES = 3
_PUSH_BACKOFF_BASE_S = 2

from core.vc_planner import VCPlanner
from core.assembler import Assembler
from core.gitignore import DEFAULT_GITIGNORE_CONTENT


class VersionControl:
    """
    The Version Control agent.

    Responsible for:
    1. Generating CI/CD infrastructure files (Dockerfile, docker-compose.yml,
       GitHub Actions workflow, .gitignore) into the output directory.
    2. Initialising a git repository and creating an initial commit.
    3. Creating a new GitHub repository via the REST API.
    4. Pushing the main branch to GitHub.

    Follows the same Planner → Assembler pattern as Developer and Tester.

    GitHub credentials (github_token, github_user, repo_name) are optional when
    only generate_infra() is needed. They are required when publish() is called.
    """

    def __init__(
        self,
        out_dir: Path,
        github_token: str = "",
        github_user: str = "",
        repo_name: str = "",
        private: bool = False,
        project_name: str = "",
    ):
        self.out_dir = out_dir
        self.github_token = github_token
        self.github_user = github_user
        self.repo_name = repo_name
        self.private = private
        self.project_name = project_name
        self.assembler = Assembler(out_dir=out_dir, use_llm=False)

    def generate_infra(self, spec: dict[str, Any]) -> None:
        """
        Generate infrastructure files and .gitignore only — no git operations,
        no GitHub API calls. Always called as part of every generation run so
        the output directory is always deployment-ready.
        """
        print("  Generating infrastructure files (Dockerfile, docker-compose, CI)...")
        self._generate_infra_files(spec)
        print("  Writing .gitignore...")
        self._write_gitignore()

    def publish(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> str:
        """
        Init git, create GitHub repo, push.

        Requires generate_infra() to have been called first — infra files must
        already exist in out_dir before this method runs.

        Args:
            spec:     Parsed Prisma spec from PrismaParser.
            api_plan: File plan returned by the Developer agent.

        Returns:
            The HTML URL of the created GitHub repository.
        """
        if not self.github_token or not self.github_user or not self.repo_name:
            print(
                "Error: github_token, github_user, and repo_name are required for publish().",
                file=sys.stderr,
            )
            sys.exit(1)

        self._spec = spec

        print("  Initialising git repository...")
        self._init_git()

        print(f"  Creating GitHub repository '{self.github_user}/{self.repo_name}'...")
        repo_html_url, clone_url = self._create_github_repo()

        print(f"  Pushing to {repo_html_url} ...")
        self._push_to_github(clone_url)

        return repo_html_url

    # ── Private helpers ────────────────────────────────────────────────────────

    def _generate_infra_files(self, spec: dict[str, Any]) -> None:
        plan = VCPlanner().plan(spec)
        print(f"  Planned {len(plan['files'])} infrastructure files")
        self.assembler.assemble(spec, plan)

    def _write_gitignore(self) -> None:
        (self.out_dir / ".gitignore").write_text(DEFAULT_GITIGNORE_CONTENT)

    def _ensure_git_identity(self) -> None:
        """
        Ensure git user.name and user.email are set before committing.

        If either is missing from global config, tell the user, offer to set
        them now, and only fall back to the Developable placeholder if the
        user explicitly declines.
        """
        fallback_name = "Developable"
        fallback_email = "generated@developable.ai"

        def _global(key: str) -> str:
            r = subprocess.run(
                ["git", "config", "--global", key],
                capture_output=True, text=True,
            )
            return r.stdout.strip() if r.returncode == 0 else ""

        def _local(key: str) -> str:
            r = subprocess.run(
                ["git", "config", "--local", key],
                cwd=self.out_dir,
                capture_output=True,
                text=True,
            )
            return r.stdout.strip() if r.returncode == 0 else ""

        def _unset_local(key: str) -> None:
            subprocess.run(
                ["git", "config", "--local", "--unset", key],
                cwd=self.out_dir,
                capture_output=True,
                text=True,
            )

        name = _global("user.name")
        email = _global("user.email")
        local_name = _local("user.name")
        local_email = _local("user.email")

        # Clean up stale repo-local fallback identity from older generated repos
        # so the user's real global identity takes precedence again.
        if name and local_name == fallback_name:
            _unset_local("user.name")
            local_name = ""
        if email and local_email == fallback_email:
            _unset_local("user.email")
            local_email = ""

        # Respect an explicit non-fallback repo-local identity.
        effective_name = local_name or name
        effective_email = local_email or email
        if (
            effective_name and effective_email
            and effective_name != fallback_name
            and effective_email != fallback_email
        ):
            return

        if name and email:
            return  # user's own identity is already configured — nothing to do

        # Try GitHub API before prompting (token is available when --github is used).
        if self.github_token and (not name or not email):
            gh_name, gh_email = self._fetch_github_identity()
            if not name and gh_name:
                name = gh_name
                subprocess.run(["git", "config", "--global", "user.name", name])
            if not email and gh_email:
                email = gh_email
                subprocess.run(["git", "config", "--global", "user.email", email])
            if name and email:
                print(f"  ✓ Git identity set from GitHub account: {name} <{email}>")
                return

        missing = []
        if not effective_name or effective_name == fallback_name:
            missing.append("user.name")
        if not effective_email or effective_email == fallback_email:
            missing.append("user.email")

        print(
            f"\n  ⚠  No global git identity found ({', '.join(missing)}).\n"
            "  Commits will be attributed to whoever you set here.\n"
        )
        choice = input(
            "  Set your git identity now? [Y/n] "
        ).strip().lower()

        if choice in ("", "y", "yes"):
            if not effective_name or effective_name == fallback_name:
                name = input("  Your name  (e.g. Jane Smith): ").strip()
                if name:
                    subprocess.run(["git", "config", "--global", "user.name", name])
                    effective_name = name
            if not effective_email or effective_email == fallback_email:
                email = input("  Your email (e.g. jane@example.com): ").strip()
                if email:
                    subprocess.run(["git", "config", "--global", "user.email", email])
                    effective_email = email
            if effective_name and effective_email:
                print("  ✓ Git identity saved to global config.")
                return

        # User declined or provided nothing — use placeholder so git commit works.
        print(
            "  Using fallback identity for this commit "
            f"(name: {fallback_name}, email: {fallback_email}).\n"
            "  Set your own anytime: git config --global user.name 'Your Name'"
        )
        if not effective_name:
            self._git("config", "user.name", fallback_name)
        if not effective_email:
            self._git("config", "user.email", fallback_email)

    def _fetch_github_identity(self) -> tuple[str, str]:
        """Return (name, email) from the GitHub /user API using the stored token."""
        try:
            resp = requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {self.github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                name = (data.get("name") or "").strip()
                login = (data.get("login") or "").strip()
                email = (data.get("email") or "").strip()
                if not email and login:
                    email = f"{login}@users.noreply.github.com"
                return name or login, email
        except Exception:
            pass
        return "", ""

    def _init_git(self) -> None:
        is_new_repo = not (self.out_dir / ".git").exists()
        self._git("init")
        self._ensure_git_identity()
        self._git("add", ".")
        # Skip commit if nothing is staged (re-run with no changes)
        has_staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=self.out_dir,
            capture_output=True,
        ).returncode == 1  # exit 1 = staged changes exist
        if has_staged:
            msg = (
                "Initial commit: Generated by Developable\n\n"
                "API, tests, Dockerfile, docker-compose, and GitHub Actions CI."
                if is_new_repo
                else "Regenerated by Developable"
            )
            self._git("commit", "-m", msg)
        if is_new_repo:
            self._git("branch", "-M", "main")

    def _repo_description(self, spec: dict[str, Any]) -> str:
        """Build a concise, informative GitHub repo description from the spec."""
        entities = spec.get("entities", [])
        auth = spec.get("auth_entity_name")
        names = [e["name"] for e in entities if e["name"] != auth]
        resource_part = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        auth_part = f" · auth via {auth}" if auth else ""
        return f"REST API for {resource_part}{auth_part} — generated by Developable (developablecode.app)"

    def _create_github_repo(self) -> tuple[str, str]:
        """Create the repo via GitHub REST API. Returns (html_url, clone_url)."""
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {
            "name": self.repo_name,
            "private": self.private,
            "description": self._repo_description(spec),
            "auto_init": False,
        }
        response = requests.post(
            "https://api.github.com/user/repos",
            headers=headers,
            json=payload,
            timeout=30,
        )

        if response.status_code == 422:
            body = response.json()
            errors = body.get("errors", [])
            if any(e.get("message", "").startswith("name already exists") for e in errors):
                print(
                    f"\nError: Repository '{self.repo_name}' already exists under "
                    f"'{self.github_user}'.\n"
                    "Use --github-repo to choose a different name.",
                    file=sys.stderr,
                )
            else:
                print(f"\nGitHub API error (422): {body}", file=sys.stderr)
            sys.exit(1)

        if not response.ok:
            print(
                f"\nGitHub API error ({response.status_code}): {response.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        data = response.json()
        self._verify_repo_accessible(headers)
        return data["html_url"], data["clone_url"]

    def _verify_repo_accessible(self, headers: dict) -> None:
        """
        Poll GET /repos/{owner}/{repo} until GitHub confirms the repo is visible.

        GitHub's API returns 201 before the repository fully propagates across
        its edge nodes. A git push issued within the same second can hit a node
        that does not yet know the repo exists, returning "Repository not found".
        Polling with a short backoff (up to ~30s) avoids this race condition.
        """
        url = f"https://api.github.com/repos/{self.github_user}/{self.repo_name}"
        delay = 1
        elapsed = 0
        max_wait = 30
        while elapsed < max_wait:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.ok:
                return
            time.sleep(delay)
            elapsed += delay
            delay = min(delay * 2, 16)
        print(
            f"\nWarning: repo https://github.com/{self.github_user}/{self.repo_name} "
            "was created but is not yet accessible — the push may fail.\n"
            "If it does, wait 30s and retry:\n"
            f"  cd {self.out_dir}\n  git push -u origin main",
            file=sys.stderr,
        )

    def _push_to_github(self, clone_url: str) -> None:
        """Push main branch with retry. Embeds token in URL to avoid interactive auth."""
        # Turn https://github.com/user/repo.git
        # into  https://<token>@github.com/user/repo.git
        authed_url = clone_url.replace("https://", f"https://{self.github_token}@")
        self._git("remote", "add", "origin", authed_url)
        delay = _PUSH_BACKOFF_BASE_S
        for attempt in range(1, _PUSH_MAX_RETRIES + 1):
            try:
                self._git("push", "-u", "origin", "main")
                return
            except subprocess.CalledProcessError as exc:
                if attempt == _PUSH_MAX_RETRIES:
                    print(
                        "\nPush to GitHub failed after retries.\n"
                        "Common causes:\n"
                        "  1. Token lacks 'repo' scope (classic PAT) or "
                        "Contents:write (fine-grained PAT)\n"
                        f"  2. Verify repo exists: "
                        f"https://github.com/{self.github_user}/{self.repo_name}\n"
                        "To retry manually:\n"
                        f"  cd {self.out_dir}\n  git push -u origin main",
                        file=sys.stderr,
                    )
                    print(exc.stderr, file=sys.stderr)
                    sys.exit(1)
                print(
                    f"  Push attempt {attempt}/{_PUSH_MAX_RETRIES} failed, "
                    f"retrying in {delay}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                delay *= 2

    def _git(self, *args: str) -> str:
        """Run a git command in the output directory."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.out_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, ["git", *args], result.stdout, result.stderr
            )
        return result.stdout
