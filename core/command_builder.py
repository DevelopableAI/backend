"""
Translates a skill config dict into a Developable CLI invocation string.

This is the single source of truth for how skill config maps to main.py flags.

CLI usage (reads JSON from stdin, prints the command):
    echo '{"schema_path": "./prisma/schema.prisma", "out_dir": "./output"}' | python core/command_builder.py
"""
import json
import sys


def build_cli_command(config: dict) -> str:
    """
    Build a main.py CLI invocation from a skill config dict.

    Required keys:
        schema_path  (str)

    Optional keys:
        cli           (str)   CLI binary — default "python main.py"
        out_dir       (str)   default "./output"
        tests_out     (str)   if set, appends --tests-out <value>
        no_llm        (bool)  default True (skill fills LLM sections in Phase 2)
        rules         (str)   path to .rules.yaml, appended if provided
        force         (bool)  default False
        github        (bool)  if True, appends --github + credentials flags
        github_token  (str)   appended as --github-token if github=True
        github_user   (str)   appended as --github-user if github=True
        github_repo   (str)   appended as --github-repo if github=True
        github_private(bool)  appends --private if github=True and private=True
        deploy_to     (str)   "aws" | "gcp" | "heroku" | None
        aws_region    (str)   appended as --aws-region when deploy_to="aws"
        gcp_project   (str)   appended as --gcp-project when deploy_to="gcp"
        gcp_region    (str)   appended as --gcp-region when deploy_to="gcp"
        heroku_app    (str)   appended as --heroku-app when deploy_to="heroku"
    """
    schema_path = config.get("schema_path")
    if not schema_path:
        raise ValueError("schema_path is required")

    cli = config.get("cli", "python main.py")
    parts = [cli, str(schema_path)]

    out_dir = config.get("out_dir", "./output")
    parts += ["--out", str(out_dir)]

    if config.get("no_llm", True):
        parts.append("--no-llm")

    tests_out = config.get("tests_out")
    if tests_out:
        parts += ["--tests-out", str(tests_out)]

    rules = config.get("rules")
    if rules:
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
        if config.get("github_private"):
            parts.append("--private")

    deploy_to = config.get("deploy_to")
    if deploy_to and deploy_to != "none":
        parts += ["--deploy-to", deploy_to]
        if deploy_to == "aws" and config.get("aws_region"):
            parts += ["--aws-region", config["aws_region"]]
        if deploy_to == "gcp":
            if config.get("gcp_project"):
                parts += ["--gcp-project", config["gcp_project"]]
            if config.get("gcp_region"):
                parts += ["--gcp-region", config["gcp_region"]]
        if deploy_to == "heroku" and config.get("heroku_app"):
            parts += ["--heroku-app", config["heroku_app"]]

    return " ".join(parts)


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON — {exc}", file=sys.stderr)
        sys.exit(1)
    print(build_cli_command(config))
