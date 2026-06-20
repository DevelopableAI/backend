import re
import subprocess
import sys
from pathlib import Path
from typing import Any


class TerraformRunner:
    """
    Runs Terraform commands inside a single terraform/ directory.

    Consolidates _tf_run, _terraform_import_aws, and _terraform_import_gcp from
    Deployment — the import+reconcile pattern was duplicated verbatim for AWS and GCP.
    Error recovery (stale locks, checksum conflicts, missing buckets, auth failures,
    disabled GCP APIs) lives here so deployer classes stay focused on provisioning.
    """

    def __init__(self, tf_dir: Path, env: dict[str, str]) -> None:
        self.tf_dir = tf_dir
        self.env = env

    def init_if_needed(self) -> None:
        if (self.tf_dir / ".terraform" / "providers").exists():
            print("\n  [Terraform] Backend already initialized — skipping init.")
            return
        print("\n  [Terraform] Initializing backend...")
        self._run(["terraform", "init", "-input=false"])

    def import_resources(self, imports: list[tuple[str, str]]) -> int:
        """
        Import a list of (tf_address, resource_id) pairs into state.
        Returns the count of successfully imported resources.
        """
        print(f"\n  [Terraform] Importing {len(imports)} resources into state...")
        ok = 0
        for addr, res_id in imports:
            r = subprocess.run(
                ["terraform", "import", "-input=false", addr, res_id],
                cwd=self.tf_dir,
                env=self.env,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0 or "already managed" in (r.stderr + r.stdout).lower():
                ok += 1
            else:
                print(f"  [Terraform] Warning: could not import {addr}: {r.stderr.strip()[:120]}")
        print(f"  [Terraform] {ok}/{len(imports)} resources in state.")
        return ok

    def apply(self) -> None:
        print(f"\n  [Terraform] Running apply...")
        self._run(["terraform", "apply", "-auto-approve", "-input=false"])
        print(f"  [Terraform] ✓ State is fully up-to-date.")

    def output_json(self) -> dict[str, Any]:
        """Return terraform output as a dict of {name: value}. Exits on failure."""
        r = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=self.tf_dir,
            capture_output=True,
            text=True,
            env=self.env,
        )
        if r.returncode != 0:
            print(f"Error reading Terraform outputs:\n{r.stderr}", file=sys.stderr)
            sys.exit(1)
        import json
        return {k: v["value"] for k, v in json.loads(r.stdout).items()}

    # ── Internal ───────────────────────────────────────────────────────────────

    def _run(self, cmd: list[str]) -> None:
        """Run a Terraform command; auto-recover from known error conditions."""
        result = subprocess.run(
            cmd, cwd=self.tf_dir, env=self.env, capture_output=True, text=True
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.returncode == 0:
            return

        combined = result.stdout + result.stderr

        # Auto-recover stale DynamoDB/GCS lock
        lock_match = re.search(r'ID:\s+([0-9a-f-]{36})', combined)
        if lock_match and "state lock" in combined.lower():
            if self._unlock_and_retry(cmd, lock_match.group(1)):
                return

        project = self.tf_dir.parent.name

        recovery_cases = [
            (
                lambda c: any(p in c for p in ("checksum", "digest mismatch",
                    "state data in S3 does not have", "Error refreshing state")),
                "Terraform found a stale state file — the S3/GCS state does not match the DynamoDB lock table.",
                [
                    f"Open DynamoDB console → table '{project}-tf-lock'",
                    f"  Delete the item whose LockID ends with '/terraform.tfstate'",
                    f"Open S3 console → bucket '{project}-tf-state'",
                    f"  Delete 'terraform.tfstate' (contains stale data from a prior run)",
                    "Press Enter — Terraform will initialize with a fresh empty state",
                ],
            ),
            (
                lambda c: any(p in c.lower() for p in ("nosuchbucket", "no such bucket", "bucketnotfound")),
                "Terraform state backend bucket not found — it may have been deleted.",
                [
                    "Re-run the deployment from the beginning",
                    "  The bootstrap step will recreate the S3 bucket and DynamoDB table",
                ],
            ),
            (
                lambda c: any(p in c for p in (
                    "AccessDenied", "Error: error configuring S3 Backend", "googleapi: Error 403"
                )),
                "Terraform backend access denied — credentials may be expired or lack S3/GCS permissions.",
                [
                    "For AWS: export fresh credentials:",
                    "  export AWS_ACCESS_KEY_ID=<key>",
                    "  export AWS_SECRET_ACCESS_KEY=<secret>",
                    "  export AWS_SESSION_TOKEN=<token>  # if using STS",
                    "For GCP: re-authenticate:",
                    "  gcloud auth application-default login",
                ],
            ),
            (
                lambda c: any(p in c for p in (
                    "has not been used in project", "API has not been enabled", "SERVICE_DISABLED"
                )),
                "A required GCP API is not enabled for this project.",
                [
                    "Open GCP console → APIs & Services → Library",
                    "Enable the API named in the error above",
                    "Common APIs needed: Cloud Run API, Cloud SQL Admin API, Artifact Registry API",
                ],
            ),
        ]

        for matches, message, steps in recovery_cases:
            if matches(combined):
                self._wait_for_user_action(message, steps)
                retry = subprocess.run(
                    cmd, cwd=self.tf_dir, env=self.env, capture_output=True, text=True
                )
                if retry.stdout:
                    print(retry.stdout, end="")
                if retry.returncode == 0:
                    return
                result = retry
                break

        print(f"\nTerraform command failed: {' '.join(cmd)}\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    def _unlock_and_retry(self, cmd: list[str], lock_id: str) -> bool:
        print(f"\n  [Terraform] Stale lock detected (ID: {lock_id}) — auto-unlocking...")
        unlock = subprocess.run(
            ["terraform", "force-unlock", "-force", lock_id],
            cwd=self.tf_dir, env=self.env, capture_output=True, text=True,
        )
        if unlock.returncode != 0:
            return False
        print("  Lock cleared — retrying command...")
        retry = subprocess.run(cmd, cwd=self.tf_dir, env=self.env, capture_output=True, text=True)
        if retry.stdout:
            print(retry.stdout, end="")
        return retry.returncode == 0

    @staticmethod
    def _wait_for_user_action(message: str, steps: list[str]) -> None:
        import time
        print(f"\n  ⚠  {message}", flush=True)
        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")
        print("\n  Press Enter once resolved to retry...", flush=True)
        try:
            input()
        except EOFError:
            time.sleep(10)
