"""
Heroku deployment provider with Heroku Postgres addon provisioning.

Deployment flow
───────────────
1. Detect API key from HEROKU_API_KEY env var or ~/.netrc.
2. Create the Heroku app (idempotent — catches 422 name-already-taken).
3. Provision Heroku Postgres Essential-0 addon.
4. Apply Prisma schema to the remote database (npx prisma db push).
5. Set container environment variables via Heroku Config Vars API.
6. Authenticate Docker to registry.heroku.com.
7. Tag and push the image to the Heroku container registry.
8. Release the web dyno via the Heroku Formation API.
9. Return a deployment record.

Database (Heroku Postgres)
──────────────────────────
- Essential-0 plan (~$5/month), provisioned as a Heroku addon.
- Heroku sets DATABASE_URL in the app's config vars automatically.
- We retrieve it and return it as the remote_db_url for schema migration.

Tagging note
────────────
Heroku has no native resource-tagging system. Traceability is maintained by:
  - Setting DEVELOPABLE_* config vars on the app.
  - Recording resources in the local state file.
"""

import getpass
import json
import netrc
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from .base import BaseProvider


_HEROKU_API = "https://api.heroku.com"
_HEROKU_REGISTRY = "registry.heroku.com"
_ADDON_WAIT_TIMEOUT_S = 300   # 5 minutes
_ADDON_POLL_INTERVAL_S = 10


class HerokuProvider(BaseProvider):
    """Deploy to Heroku via the Platform API and container registry."""

    display_name = "Heroku"

    def __init__(self, out_dir: Path, app_name: str | None = None) -> None:
        super().__init__(out_dir)
        self._app_name = app_name          # initial preference from CLI
        self._app_name_resolved: str = ""  # set after _ensure_app() succeeds
        self._api_headers: dict[str, str] = {}

    # ── Credential handling ────────────────────────────────────────────────────

    def detect_credentials(self) -> dict[str, Any] | None:
        """Check HEROKU_API_KEY env var, then ~/.netrc."""
        api_key = os.environ.get("HEROKU_API_KEY", "").strip()
        if api_key:
            return {"api_key": api_key, "app_name": self._app_name}

        try:
            nrc = netrc.netrc()
            auth = nrc.authenticators("api.heroku.com")
            if auth:
                token = auth[2]  # password field holds the API token
                if token:
                    return {"api_key": token, "app_name": self._app_name}
        except (FileNotFoundError, netrc.NetrcParseError):
            pass

        return None

    def collect_credentials(self) -> dict[str, Any]:
        """Prompt for Heroku API key and optional app name."""
        print("\nHeroku credentials not found in environment or ~/.netrc.")
        print("Create an API token at: https://dashboard.heroku.com/account\n")

        api_key = getpass.getpass("  Heroku API Key: ").strip()
        if not api_key:
            print("Error: Heroku API Key is required.", file=sys.stderr)
            sys.exit(1)

        return {"api_key": api_key, "app_name": self._app_name}

    # ── Database provisioning ──────────────────────────────────────────────────

    def provision_database(
        self,
        spec: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Add the Heroku Postgres Essential-0 addon to the app.

        The app must already exist (call _ensure_app first — done in deploy()).
        Heroku automatically sets DATABASE_URL in the app's config vars.
        """
        if not self._app_name_resolved:
            raise RuntimeError(
                "provision_database() must be called after _ensure_app() "
                "sets self._app_name_resolved."
            )

        app_name = self._app_name_resolved
        headers = self._api_headers

        print(f"  [Heroku] Adding Heroku Postgres Essential-0 addon to app '{app_name}'...")
        print(f"           This typically takes 1–2 minutes. Please wait...")

        addon_id, addon_name = self._create_postgres_addon(headers, app_name)

        print(f"  [Heroku] Waiting for Postgres addon to provision...")
        self._wait_for_addon(headers, app_name, addon_id)

        # Heroku sets DATABASE_URL automatically; retrieve it
        db_url = self._get_database_url(headers, app_name)

        print(f"  [Heroku] Postgres addon provisioned: {addon_name}")

        resources = [
            {"type": "heroku_postgres_addon", "id": addon_id, "name": addon_name, "plan": "essential-0"}
        ]
        return db_url, resources

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

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/vnd.heroku+json; version=3",
            "Content-Type": "application/json",
        }
        self._api_headers = headers

        # App name: prefer CLI arg / previously collected value; default to slug
        app_name: str = creds.get("app_name") or project_name

        # 1. Create app (sets self._app_name_resolved)
        print(f"  [Heroku] Creating app '{app_name}'...")
        app_name = self._ensure_app(headers, app_name)
        self._app_name_resolved = app_name

        # 2a. Explicitly set the app stack to "container".
        #     Passing stack in the create payload is not reliable; Heroku
        #     requires a dedicated PATCH to switch to container mode. Without
        #     this, the Formation API returns 404 "record not found" because
        #     it looks for a buildpack slug rather than a container image.
        self._set_container_stack(headers, app_name)

        # 2. Set config vars (env vars + Developable tracking vars)
        print(f"  [Heroku] Setting config vars...")
        config_vars = dict(env_vars)
        config_vars["DEVELOPABLE_PROJECT_NAME"] = project_name
        config_vars["DEVELOPABLE_DEPLOYMENT_ID"] = deployment_id
        self._set_config_vars(headers, app_name, config_vars)

        # 3. Docker login to Heroku registry
        print(f"  [Heroku] Authenticating Docker to registry.heroku.com...")
        self._docker_login(api_key)

        # 4. Tag + push image (stream output so push failures are visible)
        heroku_image = f"{_HEROKU_REGISTRY}/{app_name}/web"
        print(f"  [Heroku] Pushing image to {heroku_image}...")
        self._run(["docker", "tag", image_tag, heroku_image])
        push = subprocess.run(["docker", "push", heroku_image])
        if push.returncode != 0:
            print("\nDocker push failed.", file=sys.stderr)
            sys.exit(1)

        # 5. Get the image config digest from the manifest.
        #    With `docker buildx`, `docker inspect --format={{.Id}}` returns the
        #    manifest digest (sha256 of the manifest JSON), not the config digest
        #    (sha256 of the image config JSON). Heroku's Formation API indexes
        #    images by config digest, so we must read it from the manifest itself.
        manifest_result = subprocess.run(
            ["docker", "manifest", "inspect", heroku_image],
            capture_output=True, text=True,
        )
        if manifest_result.returncode != 0:
            print(
                f"\nFailed to inspect manifest: {manifest_result.stderr}",
                file=sys.stderr,
            )
            sys.exit(1)
        manifest = json.loads(manifest_result.stdout)
        image_id = manifest.get("config", {}).get("digest", "")
        if not image_id:
            print("\nCould not read config digest from manifest.", file=sys.stderr)
            sys.exit(1)
        print(f"  [Heroku] Config digest: {image_id}")

        # 6. Release
        print(f"  [Heroku] Releasing web dyno...")
        self._release(headers, app_name, image_id)

        # 7. Check that Heroku accepted the release and show dyno state
        self._print_release_status(headers, app_name)

        # Heroku now appends a random hash to the default domain for security
        # (e.g. developable-a8389cc7a1ea.herokuapp.com instead of developable.herokuapp.com).
        # Fetch the actual domain from the API instead of constructing it ourselves.
        endpoint = self._get_app_domain(headers, app_name)
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

    # ── CI/CD workflow generation ──────────────────────────────────────────────

    def generate_deploy_workflow(
        self,
        project_name: str,
        record: dict[str, Any],
    ) -> str:
        """Return a GitHub Actions deploy.yml for Heroku container deployments."""
        app_name = self._app_name_resolved or project_name

        return f"""\
name: Deploy to Heroku

on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]

jobs:
  deploy:
    if: ${{{{ github.event.workflow_run.conclusion == 'success' }}}}
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Log in to Heroku Container Registry
        uses: docker/login-action@v3
        with:
          registry: registry.heroku.com
          username: _
          password: ${{{{ secrets.HEROKU_API_KEY }}}}

      - name: Build and push image
        run: |
          docker build -t registry.heroku.com/{app_name}/web .
          docker push registry.heroku.com/{app_name}/web

      - name: Release web dyno
        run: |
          IMAGE_ID=$(docker manifest inspect registry.heroku.com/{app_name}/web | python3 -c "import sys,json; m=json.load(sys.stdin); print(m['config']['digest'])")
          curl -f -s -X PATCH https://api.heroku.com/apps/{app_name}/formation \\
            -H "Content-Type: application/json" \\
            -H "Accept: application/vnd.heroku+json; version=3.docker-releases" \\
            -H "Authorization: Bearer ${{{{ secrets.HEROKU_API_KEY }}}}" \\
            -d "{{\\"updates\\":[{{\\"type\\":\\"web\\",\\"docker_image\\":\\"$IMAGE_ID\\"}}]}}"
"""

    # ── Private helpers ────────────────────────────────────────────────────────

    def _set_container_stack(self, headers: dict, app_name: str) -> None:
        """Set the app's build stack to 'container' (required for registry releases)."""
        resp = requests.patch(
            f"{_HEROKU_API}/apps/{app_name}",
            headers=headers,
            json={"build_stack": "container"},
            timeout=30,
        )
        if not resp.ok:
            print(
                f"\n[Heroku] Warning: could not set container stack: {resp.text}",
                file=sys.stderr,
            )

    def _ensure_app(self, headers: dict, app_name: str) -> str:
        """Create the Heroku app or reuse an existing one. Returns final app name."""
        resp = requests.post(
            f"{_HEROKU_API}/apps",
            headers=headers,
            json={"name": app_name},
            timeout=30,
        )
        if resp.status_code == 201:
            return resp.json()["name"]

        if resp.status_code == 422:
            # Check if the app belongs to us
            check = requests.get(
                f"{_HEROKU_API}/apps/{app_name}",
                headers=headers,
                timeout=15,
            )
            if check.ok:
                return app_name  # Reuse our own app

            # Name taken by someone else — append suffix
            new_name = f"{app_name}-{self._credentials.get('deployment_id', 'dev')[:6]}"
            print(f"  [Heroku] App name '{app_name}' is taken. Trying '{new_name}'...")
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

        print(f"\nHeroku API error ({resp.status_code}): {resp.text}", file=sys.stderr)
        sys.exit(1)

    def _create_postgres_addon(
        self, headers: dict, app_name: str
    ) -> tuple[str, str]:
        """Create the Heroku Postgres Essential-0 addon. Returns (addon_id, addon_name)."""
        resp = requests.post(
            f"{_HEROKU_API}/apps/{app_name}/addons",
            headers=headers,
            json={"plan": "heroku-postgresql:essential-0"},
            timeout=60,
        )
        if resp.status_code == 422:
            # Addon may already exist — fetch it
            addons = requests.get(
                f"{_HEROKU_API}/apps/{app_name}/addons",
                headers=headers,
                timeout=15,
            ).json()
            for addon in addons:
                if "heroku-postgresql" in addon.get("plan", {}).get("name", ""):
                    return addon["id"], addon["name"]
        if not resp.ok:
            print(
                f"\nHeroku Postgres addon creation failed ({resp.status_code}): {resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)
        data = resp.json()
        return data["id"], data["name"]

    def _wait_for_addon(self, headers: dict, app_name: str, addon_id: str) -> None:
        """Poll addon state until 'provisioned'."""
        deadline = time.time() + _ADDON_WAIT_TIMEOUT_S
        while time.time() < deadline:
            resp = requests.get(
                f"{_HEROKU_API}/apps/{app_name}/addons/{addon_id}",
                headers=headers,
                timeout=15,
            )
            if resp.ok:
                state = resp.json().get("state", "")
                if state == "provisioned":
                    return
                print(f"  [Heroku] Addon state: {state} — waiting...", end="\r", flush=True)
            time.sleep(_ADDON_POLL_INTERVAL_S)
        print(
            f"\n  [Heroku] Warning: Postgres addon did not provision within "
            f"{_ADDON_WAIT_TIMEOUT_S}s.",
            file=sys.stderr,
        )
        sys.exit(1)

    def _get_database_url(self, headers: dict, app_name: str) -> str:
        """Retrieve DATABASE_URL from Heroku config vars."""
        resp = requests.get(
            f"{_HEROKU_API}/apps/{app_name}/config-vars",
            headers=headers,
            timeout=15,
        )
        if not resp.ok:
            print(
                f"\nCould not retrieve config vars ({resp.status_code}): {resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)
        db_url = resp.json().get("DATABASE_URL", "")
        if not db_url:
            print(
                "\nHeroku Postgres addon provisioned but DATABASE_URL not found in config vars.",
                file=sys.stderr,
            )
            sys.exit(1)
        return db_url

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
            ["docker", "login", "--username=_", "--password-stdin", _HEROKU_REGISTRY],
            input=api_key,
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
        # Container registry releases require the docker-releases Accept header.
        # The standard version=3 header treats this as a slug-based formation
        # update, which fails with 404 on brand-new apps that have no web dyno.
        docker_headers = {
            **headers,
            "Accept": "application/vnd.heroku+json; version=3.docker-releases",
        }
        resp = requests.patch(
            f"{_HEROKU_API}/apps/{app_name}/formation",
            headers=docker_headers,
            json={"updates": [{"type": "web", "docker_image": image_id}]},
            timeout=30,
        )
        if not resp.ok:
            print(
                f"\nHeroku release failed ({resp.status_code}): {resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)

    def _get_app_domain(self, headers: dict, app_name: str) -> str:
        """
        Return the app's actual herokuapp.com domain.

        Heroku now appends a random hash suffix to default domains
        (e.g. myapp-a1b2c3d4e5f6.herokuapp.com) so we can't construct the URL
        from the app name alone. Fetch the real hostname from the domains API.
        Falls back to the legacy pattern if the API call fails.
        """
        try:
            resp = requests.get(
                f"{_HEROKU_API}/apps/{app_name}/domains",
                headers=headers,
                timeout=15,
            )
            if resp.ok:
                for domain in resp.json():
                    if domain.get("kind") == "heroku" and domain.get("hostname", "").endswith(".herokuapp.com"):
                        hostname = domain["hostname"]
                        print(f"  [Heroku] App domain: https://{hostname}")
                        return f"https://{hostname}"
        except Exception as e:
            print(f"  [Heroku] Warning: could not fetch domain: {e}")
        # Fallback to legacy pattern
        return f"https://{app_name}.herokuapp.com"

    def _print_release_status(self, headers: dict, app_name: str) -> None:
        """Fetch and print the latest release + dyno state for diagnostics."""
        try:
            rel = requests.get(
                f"{_HEROKU_API}/apps/{app_name}/releases",
                headers={**headers, "Range": "version ..; order=desc, max=1"},
                timeout=15,
            )
            dynos = requests.get(
                f"{_HEROKU_API}/apps/{app_name}/dynos",
                headers=headers,
                timeout=15,
            )
            if rel.ok:
                releases = rel.json()
                if releases:
                    r = releases[0]
                    print(f"  [Heroku] Latest release v{r.get('version')}: status={r.get('status')} description={r.get('description')}")
            if dynos.ok:
                dyno_list = dynos.json()
                if dyno_list:
                    for d in dyno_list:
                        print(f"  [Heroku] Dyno {d.get('name')}: state={d.get('state')}")
                else:
                    print("  [Heroku] No dynos running yet (release pending).")
        except Exception as e:
            print(f"  [Heroku] Could not fetch release status: {e}")

    def wait_for_ready(self, endpoint: str, timeout_s: int = 120, poll_s: int = 5) -> None:
        """
        Poll GET <endpoint>/health until HTTP 200 or timeout.

        Called by the deployment agent AFTER apply_schema() so DATABASE_URL is
        already set in Heroku's config vars and the dyno has restarted with it.
        """
        print(f"  [Heroku] Waiting for dyno to become ready (up to {timeout_s}s)...", end="", flush=True)
        deadline = time.time() + timeout_s
        print(f"  [Heroku] Endpoint to call: {endpoint}/health")
        while time.time() < deadline:
            try:
                resp = requests.get(f"{endpoint}/health", timeout=5)
                if resp.status_code == 200:
                    print(" ready.")
                    return
            except requests.exceptions.RequestException as e:
                print(f"  [Heroku] Health check error: {e}")
                pass
            print(".", end="", flush=True)
            time.sleep(poll_s)
        print(f"\n  [Heroku] Warning: dyno did not become healthy within {timeout_s}s. Tests may fail.", file=sys.stderr)

    def _run(self, cmd: list[str]) -> None:
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            print(
                f"\nCommand failed: {' '.join(cmd)}\n{result.stderr.decode()}",
                file=sys.stderr,
            )
            sys.exit(1)
