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
       GitHub Actions workflow) into the output directory.
    2. Initialising a git repository and creating an initial commit.
    3. Creating a new GitHub repository via the REST API.
    4. Pushing the main branch to GitHub.

    Follows the same Planner → Assembler pattern as Developer and Tester.
    """

    def __init__(
        self,
        out_dir: Path,
        github_token: str,
        github_user: str,
        repo_name: str,
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
        no GitHub API calls. Used by the skill's --infra flag so infra files
        are always present in the output directory regardless of --github.
        """
        print("  Generating infrastructure files (Dockerfile, docker-compose, CI)...")
        self._generate_infra_files(spec)
        print("  Writing .gitignore...")
        self._write_gitignore()

    def publish(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> str:
        """
        Generate infra files, init git, create GitHub repo, push.

        Args:
            spec:     Parsed Prisma spec from PrismaParser.
            api_plan: File plan returned by the Developer agent.

        Returns:
            The HTML URL of the created GitHub repository.
        """
        self._spec = spec  # stored for entity-specific repo description
        print("  Generating infrastructure files (Dockerfile, docker-compose, CI)...")
        self._generate_infra_files(spec)

        print("  Writing .gitignore...")
        self._write_gitignore()

        print("  Initialising git repository...")
        self._init_git()

        print(f"  Creating GitHub repository '{self.github_user}/{self.repo_name}'...")
        repo_html_url, clone_url = self._create_github_repo()

        print(f"  Pushing to {repo_html_url} ...")
        self._push_to_github(clone_url)

        return repo_html_url
