import subprocess

from core.git_client import GitClient
from core.github_client import GitHubClient


class GitIdentityResolver:
    """
    Ensures git user.name and user.email are set before the first commit.

    Extracted from VersionControl._ensure_git_identity (110 lines, 3 nested
    closures). Resolution order:
      1. Existing global config — done immediately.
      2. GitHub API (when a token is available).
      3. Interactive prompt.
      4. Fallback placeholder so git commit never fails.
    """

    FALLBACK_NAME = "Developable"
    FALLBACK_EMAIL = "generated@developable.ai"

    def __init__(self, git: GitClient, github: GitHubClient | None = None) -> None:
        self.git = git
        self.github = github

    def resolve(self) -> None:
        """Ensure a valid git identity is available; mutates git config as needed."""
        name = self.git.config_get("user.name")
        email = self.git.config_get("user.email")
        local_name = self.git.config_get("user.name", "--local")
        local_email = self.git.config_get("user.email", "--local")

        # Remove stale fallback repo-local identity so user's global takes precedence.
        if name and local_name == self.FALLBACK_NAME:
            self.git.config_unset("user.name")
            local_name = ""
        if email and local_email == self.FALLBACK_EMAIL:
            self.git.config_unset("user.email")
            local_email = ""

        effective_name = local_name or name
        effective_email = local_email or email

        if self._is_real_identity(effective_name, effective_email):
            return

        # Try GitHub API before prompting.
        if self.github and self.github.token and (not name or not email):
            gh_name, gh_email = self.github.fetch_identity()
            if not name and gh_name:
                name = gh_name
                self.git.config_set("user.name", name)
            if not email and gh_email:
                email = gh_email
                self.git.config_set("user.email", email)
            if name and email:
                print(f"  ✓ Git identity set from GitHub account: {name} <{email}>")
                return

        self._prompt_or_fallback(effective_name, effective_email)

    def _is_real_identity(self, name: str, email: str) -> bool:
        return (
            bool(name) and bool(email)
            and name != self.FALLBACK_NAME
            and email != self.FALLBACK_EMAIL
        )

    def _prompt_or_fallback(self, effective_name: str, effective_email: str) -> None:
        missing = []
        if not effective_name or effective_name == self.FALLBACK_NAME:
            missing.append("user.name")
        if not effective_email or effective_email == self.FALLBACK_EMAIL:
            missing.append("user.email")

        print(
            f"\n  ⚠  No global git identity found ({', '.join(missing)}).\n"
            "  Commits will be attributed to whoever you set here.\n"
        )
        choice = input("  Set your git identity now? [Y/n] ").strip().lower()

        if choice in ("", "y", "yes"):
            if not effective_name or effective_name == self.FALLBACK_NAME:
                name = input("  Your name  (e.g. Jane Smith): ").strip()
                if name:
                    self.git.config_set("user.name", name)
                    effective_name = name
            if not effective_email or effective_email == self.FALLBACK_EMAIL:
                email = input("  Your email (e.g. jane@example.com): ").strip()
                if email:
                    self.git.config_set("user.email", email)
                    effective_email = email
            if self._is_real_identity(effective_name, effective_email):
                print("  ✓ Git identity saved to global config.")
                return

        print(
            f"  Using fallback identity for this commit "
            f"(name: {self.FALLBACK_NAME}, email: {self.FALLBACK_EMAIL}).\n"
            "  Set your own anytime: git config --global user.name 'Your Name'"
        )
        if not effective_name:
            self.git.config_set("user.name", self.FALLBACK_NAME, "--local")
        if not effective_email:
            self.git.config_set("user.email", self.FALLBACK_EMAIL, "--local")
