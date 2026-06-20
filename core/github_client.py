import base64
import os
import re
import sys
from typing import Any

import requests

from core.git_client import GitClient

# Maps provider slug → {GitHub secret name: key in credentials dict}
PROVIDER_GITHUB_SECRETS: dict[str, dict[str, str]] = {
    "heroku": {"HEROKU_API_KEY": "api_key"},
    "aws": {
        "AWS_ACCESS_KEY_ID": "access_key",
        "AWS_SECRET_ACCESS_KEY": "secret_key",
        "AWS_SESSION_TOKEN": "session_token",
    },
    "gcp": {"GCP_CREDENTIALS": "credentials_b64"},
}

OPTIONAL_PROVIDER_GITHUB_SECRETS: dict[str, set[str]] = {
    "aws": {"AWS_SESSION_TOKEN"},
}


class GitHubClient:
    """
    GitHub REST API client scoped to a single token.

    Consolidates the GitHub-related private methods that were scattered across
    Deployment (_github_token, _github_repo_fullname, _provision_github_secrets,
    _push_workflow_to_github, _print_secrets_instructions) and VersionControl
    (_fetch_github_identity, _create_github_repo, _verify_repo_accessible).
    """

    _API = "https://api.github.com"

    def __init__(self, token: str = "") -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")

    @classmethod
    def from_git_remote(cls, git: GitClient) -> "GitHubClient":
        """Try to extract a token embedded in the git remote URL, else return empty client."""
        url = git.remote_url() or ""
        m = re.match(r"https://([^@]+)@github\.com/", url)
        return cls(token=m.group(1) if m else "")

    # ── Identity ───────────────────────────────────────────────────────────────

    def fetch_identity(self) -> tuple[str, str]:
        """Return (name, email) from /user, or ('', '') on failure."""
        if not self.token:
            return "", ""
        try:
            resp = requests.get(
                f"{self._API}/user",
                headers=self._headers(),
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

    # ── Repository ─────────────────────────────────────────────────────────────

    def repo_fullname(self, git: GitClient) -> str | None:
        """Extract 'owner/repo' from the git remote URL, or None."""
        url = git.remote_url() or ""
        m = re.search(r"github\.com[/:]([^/]+/[^/.]+?)(?:\.git)?$", url)
        return m.group(1) if m else None

    def create_repo(
        self,
        user: str,
        name: str,
        description: str,
        private: bool,
    ) -> tuple[str, str]:
        """
        Create a GitHub repository. Returns (html_url, clone_url).
        Calls sys.exit(1) on failure (matches existing behaviour).
        """
        resp = requests.post(
            f"{self._API}/user/repos",
            headers=self._headers(),
            json={"name": name, "private": private, "description": description, "auto_init": False},
            timeout=30,
        )
        if resp.status_code == 422:
            body = resp.json()
            errors = body.get("errors", [])
            if any(e.get("message", "").startswith("name already exists") for e in errors):
                print(
                    f"\nError: Repository '{name}' already exists under '{user}'.\n"
                    "Use --github-repo to choose a different name.",
                    file=sys.stderr,
                )
            else:
                print(f"\nGitHub API error (422): {body}", file=sys.stderr)
            sys.exit(1)
        if not resp.ok:
            print(f"\nGitHub API error ({resp.status_code}): {resp.text}", file=sys.stderr)
            sys.exit(1)
        data = resp.json()
        return data["html_url"], data["clone_url"]

    def wait_until_accessible(self, user: str, repo: str, max_wait: int = 30) -> None:
        """Poll GET /repos/{user}/{repo} until GitHub propagates the new repo."""
        import time
        url = f"{self._API}/repos/{user}/{repo}"
        delay, elapsed = 1, 0
        while elapsed < max_wait:
            if requests.get(url, headers=self._headers(), timeout=10).ok:
                return
            time.sleep(delay)
            elapsed += delay
            delay = min(delay * 2, 16)
        print(
            f"\nWarning: repo https://github.com/{user}/{repo} was created but is not yet "
            "accessible — the push may fail.\n"
            f"If it does, wait 30 s and retry:\n  git push -u origin main",
            file=sys.stderr,
        )

    # ── Secrets ────────────────────────────────────────────────────────────────

    def fetch_public_key(self, repo: str) -> tuple[str, bytes] | None:
        """Return (key_id, raw_key_bytes) for the repo's Actions public key, or None."""
        resp = requests.get(
            f"{self._API}/repos/{repo}/actions/secrets/public-key",
            headers=self._headers(),
            timeout=15,
        )
        if not resp.ok:
            return None
        data = resp.json()
        return data["key_id"], base64.b64decode(data["key"])

    def set_secret(
        self, repo: str, name: str, value: str, key_id: str, pub_key_bytes: bytes
    ) -> bool:
        """Encrypt value with the repo's public key and set it as an Actions secret."""
        try:
            from nacl import public as nacl_public
        except ImportError:
            print("  PyNaCl not installed — cannot auto-set GitHub secrets.\n  Run: pip install PyNaCl")
            return False
        box = nacl_public.SealedBox(nacl_public.PublicKey(pub_key_bytes))
        encrypted = base64.b64encode(box.encrypt(value.encode())).decode()
        resp = requests.put(
            f"{self._API}/repos/{repo}/actions/secrets/{name}",
            headers=self._headers(),
            json={"encrypted_value": encrypted, "key_id": key_id},
            timeout=15,
        )
        if resp.status_code not in (201, 204):
            print(f"  Warning: could not set {name} ({resp.status_code}): {resp.text}")
            return False
        return True

    # ── Files ──────────────────────────────────────────────────────────────────

    def push_file_via_api(
        self, repo: str, path: str, content: str, commit_msg: str
    ) -> bool:
        """Create or update a file directly via the Contents API. Returns True on success."""
        encoded = base64.b64encode(content.encode()).decode()
        get_resp = requests.get(
            f"{self._API}/repos/{repo}/contents/{path}",
            headers=self._headers(),
            timeout=10,
        )
        payload: dict[str, Any] = {"message": commit_msg, "content": encoded}
        if get_resp.ok:
            payload["sha"] = get_resp.json().get("sha", "")
        resp = requests.put(
            f"{self._API}/repos/{repo}/contents/{path}",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        return resp.status_code in (200, 201)

    # ── Internal ───────────────────────────────────────────────────────────────

    # ── Deploy integration ─────────────────────────────────────────────────────

    def provision_deploy_secrets(
        self, provider_name: str, creds: dict[str, Any], repo: str | None
    ) -> bool:
        """
        Set the required GitHub Actions secrets for the deploy workflow.
        Returns True if all required secrets were set (or none are needed).
        Falls back to printing manual instructions when auto-set is not possible.
        """
        secret_map = PROVIDER_GITHUB_SECRETS.get(provider_name, {})
        if not secret_map:
            return True

        if not self.token or not repo:
            self._print_secrets_instructions(provider_name, creds, repo)
            return False

        pk = self.fetch_public_key(repo)
        if pk is None:
            print(f"  Could not fetch repo public key — skipping auto-set.")
            self._print_secrets_instructions(provider_name, creds, repo)
            return False

        key_id, pub_key_bytes = pk
        optional = OPTIONAL_PROVIDER_GITHUB_SECRETS.get(provider_name, set())
        set_ok, failed = [], []

        for secret_name, cred_key in secret_map.items():
            value = creds.get(cred_key, "")
            if not value:
                if secret_name not in optional:
                    failed.append(secret_name)
                continue
            if self.set_secret(repo, secret_name, value, key_id, pub_key_bytes):
                set_ok.append(secret_name)
            else:
                failed.append(secret_name)

        if set_ok:
            print(f"\n  GitHub Actions secrets set automatically: {', '.join(set_ok)}")
        if failed:
            self._print_secrets_instructions(provider_name, creds, repo)
            return False
        return True

    def push_workflow(
        self, git: GitClient, workflow_yaml: str, workflow_rel_path: str, commit_msg: str
    ) -> bool:
        """Write a workflow file, commit it, and push to origin main. Returns True on success."""
        workflow_file = git.cwd / workflow_rel_path
        workflow_file.parent.mkdir(parents=True, exist_ok=True)
        workflow_file.write_text(workflow_yaml)
        if not git.commit(commit_msg, workflow_rel_path):
            return False
        return git.push()

    def _print_secrets_instructions(
        self, provider_name: str, creds: dict[str, Any], repo: str | None
    ) -> None:
        secret_map = PROVIDER_GITHUB_SECRETS.get(provider_name, {})
        optional = OPTIONAL_PROVIDER_GITHUB_SECRETS.get(provider_name, set())
        if not secret_map:
            return
        settings_url = f"https://github.com/{repo or '<your-repo>'}/settings/secrets/actions"
        print(
            "\n  ─────────────────────────────────────────────────────────────────\n"
            "  Could not auto-set GitHub Actions secrets.\n"
            f"  Add them manually at: {settings_url}\n"
            "  ─────────────────────────────────────────────────────────────────"
        )
        for name in secret_map:
            suffix = "  (optional)" if name in optional else ""
            print(f"    {name}{suffix}")
        print("  ─────────────────────────────────────────────────────────────────")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
