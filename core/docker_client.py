import subprocess
import sys
import time
from pathlib import Path


class DockerClient:
    """
    Thin wrapper around the Docker CLI scoped to a build directory.

    Replaces _ensure_docker_running, _docker_build, and the ECR push
    subprocess calls that were inlined inside Deployment.
    """

    def __init__(self, build_dir: Path) -> None:
        self.build_dir = build_dir

    def ensure_running(self) -> None:
        """Block until the Docker daemon responds, prompting the user if needed."""
        while True:
            if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
                return
            print(
                "\n  Docker is not running.\n"
                "  Please start Docker Desktop (or your Docker daemon) and press Enter to retry...",
                flush=True,
            )
            try:
                input()
            except EOFError:
                time.sleep(10)

    def build(self, tag: str) -> None:
        """
        Build a linux/amd64 image from build_dir and load it into the local daemon.

        Uses buildx with --provenance=false to force manifest v2 format — OCI
        manifests are rejected by Heroku and some other registries.
        """
        self.ensure_running()
        result = subprocess.run([
            "docker", "buildx", "build",
            "--platform", "linux/amd64",
            "--provenance=false",
            "--load",
            "-t", tag,
            str(self.build_dir),
        ])
        if result.returncode != 0:
            print(
                "\nDocker build failed. Ensure Docker is running and the "
                "Dockerfile in the output directory is valid.",
                file=sys.stderr,
            )
            sys.exit(1)

    def tag(self, source: str, target: str) -> None:
        subprocess.run(["docker", "tag", source, target], check=True)

    def push(self, tag: str) -> None:
        result = subprocess.run(["docker", "push", tag])
        if result.returncode != 0:
            print(f"Error pushing image: {tag}", file=sys.stderr)
            sys.exit(1)

    def login(self, registry: str, username: str, password: str) -> None:
        subprocess.run(
            ["docker", "login", "--username", username, "--password-stdin", registry],
            input=password.encode(),
            capture_output=True,
            check=True,
        )
