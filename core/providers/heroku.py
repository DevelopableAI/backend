"""
Heroku deployment provider.

Deployment flow
───────────────
1. Detect API key from HEROKU_API_KEY env var or ~/.netrc.
2. Create the Heroku app (idempotent — catches 422 name-already-taken).
3. Set container environment variables via Heroku Config Vars API.
4. Authenticate Docker to registry.heroku.com.
5. Tag and push the image to the Heroku container registry.
6. Release the web dyno via the Heroku Formation API.
7. Return a deployment record.

Tagging note
────────────
Heroku has no native resource-tagging system. Traceability is maintained by:
  - Setting DEVELOPABLE_* config vars on the app.
  - Recording resources in the local state file.

Database note
─────────────
DATABASE_URL must be set in <out_dir>/.env before deploying. Heroku Postgres
add-on can be added manually afterwards with:
  heroku addons:create heroku-postgresql:essential-0 --app <app-name>
"""

import getpass
import netrc
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

from .base import BaseProvider


_HEROKU_API = "https://api.heroku.com"
_HEROKU_REGISTRY = "registry.heroku.com"


class HerokuProvider(BaseProvider):
    """Deploy to Heroku via the Platform API and container registry."""

    display_name = "Heroku"

    def __init__(self, out_dir: Path, app_name: str | None = None) -> None:
        super().__init__(out_dir)
        self._app_name = app_name  # may be set by CLI flag or collected interactively

    # ── Credential handling ────────────────────────────────────────────────────

    def detect_credentials(self) -> dict[str, Any] | None:
        """
        Check HEROKU_API_KEY env var first, then ~/.netrc for a stored token.
        Returns None if no token is found.
        """
        api_key = os.environ.get("HEROKU_API_KEY", "").strip()
        if api_key:
            return {"api_key": api_key, "app_name": self._app_name}

        try:
            nrc = netrc.netrc()
            auth = nrc.authenticators("api.heroku.com")
            if auth:
                # netrc format: machine api.heroku.com login <email> password <token>
                token = auth[2]  # password field holds the API token
                if token:
                    return {"api_key": token, "app_name": self._app_name}
        except (FileNotFoundError, netrc.NetrcParseError):
            pass

        return None

    def collect_credentials(self) -> dict[str, Any]:
        """Prompt for Heroku API key and optional app name."""
        print("\nHeroku credentials not found in environment or ~/.netrc.")
        print("You can create an API token at: https://dashboard.heroku.com/account\n")

        api_key = getpass.getpass("  Heroku API Key: ").strip()
        if not api_key:
            print("Error: Heroku API Key is required.", file=sys.stderr)
            sys.exit(1)

        return {"api_key": api_key, "app_name": self._app_name}

    # ── Main deploy ────────────────────────────────────────────────────────────

    def deploy(
        self,
        spec: dict[str, Any],
        image_tag: str,
        env_vars: dict[str, str],
        deployment_id: str,
    ) -> dict[str, Any]:
        creds = self._credentials
        api_key = creds["api_key"]
        project_name = self.slug(spec)
        tags = self.build_tags(project_name, deployment_id, spec)

        # App name: prefer CLI arg / previously collected value; default to slug
        app_name: str = creds.get("app_name") or project_name

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/vnd.heroku+json; version=3",
            "Content-Type": "application/json",
        }

        # 1. Create app
        print(f"  [Heroku] Creating app '{app_name}'...")
        app_name = self._ensure_app(headers, app_name)

        # 2. Set config vars (env vars + Developable tracking vars)
        print(f"  [Heroku] Setting config vars...")
        config_vars = dict(env_vars)
        config_vars["DEVELOPABLE_PROJECT_NAME"] = project_name
        config_vars["DEVELOPABLE_DEPLOYMENT_ID"] = deployment_id
        self._set_config_vars(headers, app_name, config_vars)

        # 3. Docker login to Heroku registry
        print(f"  [Heroku] Authenticating Docker to registry.heroku.com...")
        self._docker_login(api_key)

        # 4. Tag + push image
        heroku_image = f"{_HEROKU_REGISTRY}/{app_name}/web"
        print(f"  [Heroku] Pushing image to {heroku_image}...")
        self._run(["docker", "tag", image_tag, heroku_image])
        self._run(["docker", "push", heroku_image])

        # 5. Get image ID for release
        result = subprocess.run(
            ["docker", "inspect", heroku_image, "--format={{.Id}}"],
            capture_output=True, text=True
        )
        image_id = result.stdout.strip()

        # 6. Release
        print(f"  [Heroku] Releasing web dyno...")
        self._release(headers, app_name, image_id)

        endpoint = f"https://{app_name}.herokuapp.com"
        resources = [
            {"type": "heroku_app", "id": app_name, "url": endpoint},
        ]

        from core.deployment_state import DeploymentState
        return DeploymentState.make_record(
            provider="heroku",
            region=None,
            endpoint=endpoint,
            image_uri=heroku_image,
            resources=resources,
            tags=tags,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _ensure_app(self, headers: dict, app_name: str) -> str:
        """
        Create the Heroku app. If the name is taken and belongs to this account
        reuse it. If taken by someone else, append a suffix and retry once.
        Returns the final app name used.
        """
        resp = requests.post(
            f"{_HEROKU_API}/apps",
            headers=headers,
            json={"name": app_name, "stack": "container"},
            timeout=30,
        )
        if resp.status_code == 201:
            return resp.json()["name"]

        if resp.status_code == 422:
            # Name taken — check if it belongs to us (fetch app details)
            check = requests.get(
                f"{_HEROKU_API}/apps/{app_name}",
                headers=headers,
                timeout=15,
            )
            if check.ok:
                # App already exists under our account — reuse it
                return app_name

            # Taken by someone else — use a different name
            new_name = f"{app_name}-{self._credentials.get('deployment_id', 'dev')[:6]}"
            print(
                f"  [Heroku] App name '{app_name}' is taken. Trying '{new_name}'...",
            )
            retry = requests.post(
                f"{_HEROKU_API}/apps",
                headers=headers,
                json={"name": new_name, "stack": "container"},
                timeout=30,
            )
            if retry.ok:
                return retry.json()["name"]
            print(
                f"\nError: Could not create Heroku app. "
                f"Use --heroku-app to specify a unique name.\n{retry.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(
            f"\nHeroku API error ({resp.status_code}): {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    def _set_config_vars(
        self, headers: dict, app_name: str, config_vars: dict[str, str]
    ) -> None:
        resp = requests.patch(
            f"{_HEROKU_API}/apps/{app_name}/config-vars",
            headers=headers,
            json=config_vars,
            timeout=30,
        )
        if not resp.ok:
            print(
                f"\nWarning: Could not set config vars ({resp.status_code}): {resp.text}",
                file=sys.stderr,
            )

    def _docker_login(self, api_key: str) -> None:
        result = subprocess.run(
            ["docker", "login", "--username=_", f"--password={api_key}", _HEROKU_REGISTRY],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"\nDocker login to Heroku registry failed:\n{result.stderr}",
                file=sys.stderr,
            )
            sys.exit(1)

    def _release(self, headers: dict, app_name: str, image_id: str) -> None:
        """Release the web process type with the new image."""
        resp = requests.patch(
            f"{_HEROKU_API}/apps/{app_name}/formation",
            headers=headers,
            json={"updates": [{"type": "web", "docker_image": image_id}]},
            timeout=30,
        )
        if not resp.ok:
            print(
                f"\nHeroku release failed ({resp.status_code}): {resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)

    def _run(self, cmd: list[str]) -> None:
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            print(
                f"\nCommand failed: {' '.join(cmd)}\n{result.stderr.decode()}",
                file=sys.stderr,
            )
            sys.exit(1)
