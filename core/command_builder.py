"""
Translates a skill config dict into Developable CLI invocation strings.

This is the single source of truth for how skill config maps to CLI flags.

CLI usage (reads JSON from stdin):
  - Default: prints the main.py command
  - With "command": "deploy": prints the deploy.py command

Examples:
    echo '{"schema_path": "./schema.prisma", "rules": "./rules.yaml"}' | python core/command_builder.py
    echo '{"command": "deploy", "out_dir": "./output", "deploy_to": "aws"}' | python core/command_builder.py
"""
import json
import sys


def build_cli_command(config: dict) -> str:
    """
    Build a main.py CLI invocation from a skill config dict.

    Required keys:
        schema_path  (str)
        rules        (str)   path to .rules.yaml — always required

    Optional keys:
        cli           (str)   CLI binary — default "python main.py"
        out_dir       (str)   default "./output"
        tests_out     (str)   if set, appends --tests-out <value>
        no_llm        (bool)  default True (skill fills LLM sections in Phase 2)
        force         (bool)  default False
        github        (bool)  if True, appends --github + credentials flags
        github_token  (str)   appended as --github-token if github=True
        github_user   (str)   appended as --github-user if github=True
        github_repo   (str)   appended as --github-repo if github=True
        github_private(bool)  appends --private if github=True and private=True
    """
    schema_path = config.get("schema_path")
    if not schema_path:
        raise ValueError("schema_path is required")

    rules = config.get("rules")
    if not rules:
        raise ValueError("rules is required — pass the path to the .rules.yaml file")

    cli = config.get("cli", "python main.py")
    parts = [cli, str(schema_path)]

    out_dir = config.get("out_dir", "./output")
    parts += ["--out", str(out_dir)]

    if config.get("no_llm", True):
        parts.append("--no-llm")

    tests_out = config.get("tests_out")
    if tests_out:
        parts += ["--tests-out", str(tests_out)]

    parts += ["--rules", str(rules)]

    if config.get("force", False):
        parts.append("--force")

    if config.get("github"):
        parts.append("--github")
        if config.get("github_token"):
            parts += ["--github-token", config["github_token"]]
        if config.get("github_user"):
            parts += ["--github-user", config["github_user"]]
        if config.get("github_repo"):
            parts += ["--github-repo", config["github_repo"]]
        if config.get("project_name"):
            parts += ["--project-name", config["project_name"]]
        if config.get("github_private"):
            parts.append("--private")

    return " ".join(parts)


def build_deploy_command(config: dict) -> str:
    """
    Build a deploy.py CLI invocation from a skill config dict.

    Required keys:
        deploy_to    (str)   "aws" | "gcp" | "heroku"

    Optional keys:
        cli           (str)   CLI binary — default "python deploy.py"
        out_dir       (str)   default "./output"
        aws_region    (str)   appended as --aws-region when deploy_to="aws"
        gcp_project   (str)   appended as --gcp-project when deploy_to="gcp"
        gcp_region    (str)   appended as --gcp-region when deploy_to="gcp"
        gcp_sa_path   (str)   appended as --gcp-sa-path when deploy_to="gcp" (omit for ADC)
        heroku_app    (str)   appended as --heroku-app when deploy_to="heroku"
        github_token  (str)   appended as --github-token (for Secrets API)
    """
    deploy_to = config.get("deploy_to")
    if not deploy_to or deploy_to == "none":
        raise ValueError("deploy_to is required and must not be 'none'")

    cli = config.get("cli", "python deploy.py")
    # Normalise: if caller passed the main.py cli binary, swap to deploy.py
    cli = cli.replace("main.py", "deploy.py")

    out_dir = config.get("out_dir", "./output")
    parts = [cli, "--out", str(out_dir), "--deploy-to", deploy_to]

    if deploy_to == "aws" and config.get("aws_region"):
        parts += ["--aws-region", config["aws_region"]]
    if deploy_to == "gcp":
        if config.get("gcp_project"):
            parts += ["--gcp-project", config["gcp_project"]]
        if config.get("gcp_region"):
            parts += ["--gcp-region", config["gcp_region"]]
        if config.get("gcp_sa_path"):
            parts += ["--gcp-sa-path", config["gcp_sa_path"]]
    if deploy_to == "heroku" and config.get("heroku_app"):
        parts += ["--heroku-app", config["heroku_app"]]
    if config.get("github_token"):
        parts += ["--github-token", config["github_token"]]

    return " ".join(parts)


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        sys.exit(1)

    if config.get("command") == "deploy":
        print(build_deploy_command(config))
    else:
        print(build_cli_command(config))
