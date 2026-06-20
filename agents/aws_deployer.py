import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

from core.aws_provisioner import AWSProvisioner
from core.deployment_state import DeploymentState
from core.docker_client import DockerClient
from core.provider_deployer import ProviderDeployer


class AWSDeployer(ProviderDeployer):
    """
    Deploys to AWS using a hybrid boto3 + Terraform import strategy.

    Delegates all boto3 resource provisioning to AWSProvisioner (core/aws_provisioner.py),
    keeping this class focused on the deployment orchestration flow:
      provision → build → push to ECR → terraform import → ECS migration.
    """

    def deploy(
        self,
        spec: dict[str, Any],
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> dict[str, Any]:
        region = creds.get("region", "us-east-1")
        print(
            f"\n  ┌─ Step 3/7 — Provision AWS infrastructure via boto3 ──────────────────\n"
            f"  │  ETA: ~10–15 min  (RDS takes 8–10 min; other resources ~2 min)\n"
            f"  │  Monitor RDS:  https://console.aws.amazon.com/rds/home?region={region}#databases:\n"
            f"  │  Monitor ECS:  https://console.aws.amazon.com/ecs/home?region={region}#/clusters\n"
            f"  └─────────────────────────────────────────────────────────────────────────"
        )
        pr = AWSProvisioner(creds, project_name, self._read_env_file).provision()

        local_tag = f"developable/{project_name}:latest"
        image_uri = f"{pr['ecr_url']}:latest"
        print(f"\n  ┌─ Step 4/7 — Build Docker image  (ETA: ~3 min) ─────────────────────")
        print(f"  └─ Image tag: {local_tag}")
        self.docker.build(local_tag)

        print(f"\n  ┌─ Step 5/7 — Push image to ECR  (ETA: ~1 min) ──────────────────────")
        print(f"  └─ Target:    {image_uri}")
        self._push_to_ecr(local_tag, image_uri, region, creds)

        print(
            f"\n  ┌─ Step 6/7 — Terraform import + reconciliation apply  (ETA: ~3 min) ──\n"
            f"  │  Imports 15 resources into state, then apply makes state fully current.\n"
            f"  └─────────────────────────────────────────────────────────────────────────"
        )
        self._terraform_import(creds, project_name, pr)

        print(
            f"\n  ┌─ Step 7/7 — Prisma migration via ECS task  (ETA: ~2 min) ───────────\n"
            f"  │  Monitor: https://console.aws.amazon.com/ecs/home?region={region}"
            f"#/clusters/{project_name}/tasks\n"
            f"  └─────────────────────────────────────────────────────────────────────────"
        )
        self._run_migration(project_name, pr["db_url"], region, creds)

        endpoint = f"http://{pr['alb_dns']}"
        print(f"\n  ✓ Deployed — endpoint: {endpoint}")

        return DeploymentState.make_record(
            provider="aws", region=region, endpoint=endpoint, image_uri=image_uri,
            resources=[{"type": "terraform_managed", "id": project_name, "arn": None}],
            tags=provider.build_tags(project_name, deployment_id, spec),
        )

    # ── ECR push ───────────────────────────────────────────────────────────────

    def _push_to_ecr(
        self, local_tag: str, image_uri: str, region: str, creds: dict[str, Any]
    ) -> None:
        import boto3
        session = boto3.Session(
            aws_access_key_id=creds.get("access_key"),
            aws_secret_access_key=creds.get("secret_key"),
            aws_session_token=creds.get("session_token"),
            region_name=region,
        )
        ecr = session.client("ecr")
        auth = ecr.get_authorization_token()["authorizationData"][0]
        username, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
        self.docker.login(auth["proxyEndpoint"], username, password)
        self.docker.tag(local_tag, image_uri)
        self.docker.push(image_uri)

    # ── Terraform import ───────────────────────────────────────────────────────

    def _terraform_import(
        self, creds: dict[str, Any], project_name: str, pr: dict[str, Any]
    ) -> None:
        region = creds["region"]
        tf_dir = self.out_dir / "terraform"
        tf_env = {
            **os.environ,
            "AWS_ACCESS_KEY_ID": creds.get("access_key", ""),
            "AWS_SECRET_ACCESS_KEY": creds.get("secret_key", ""),
            "AWS_DEFAULT_REGION": region,
        }
        if creds.get("session_token"):
            tf_env["AWS_SESSION_TOKEN"] = creds["session_token"]

        (tf_dir / "terraform.auto.tfvars.json").write_text(json.dumps({
            "project_name": project_name, "aws_region": region,
            "db_password": pr["db_password"], "jwt_secret": pr["jwt_secret"],
            "ecr_image_tag": "latest",
        }, indent=2))

        runner = self.tf_runner_factory(tf_dir, tf_env)
        runner.init_if_needed()
        runner.import_resources([
            ("aws_security_group.alb",                   pr["alb_sg_id"]),
            ("aws_security_group.ecs",                   pr["ecs_sg_id"]),
            ("aws_security_group.rds",                   pr["rds_sg_id"]),
            ("aws_ecr_repository.main",                  project_name),
            ("aws_db_subnet_group.main",                 pr["subnet_group"]),
            ("aws_db_instance.main",                     project_name),
            ("aws_iam_role.ecs_execution",               pr["role_name"]),
            ("aws_iam_role_policy_attachment.ecs_execution",
             f"{pr['role_name']}/{pr['policy_arn']}"),
            ("aws_lb.main",                              pr["alb_arn"]),
            ("aws_lb_target_group.main",                 pr["tg_arn"]),
            ("aws_lb_listener.http",                     pr["listener_arn"]),
            ("aws_cloudwatch_log_group.main",            pr["log_group"]),
            ("aws_ecs_cluster.main",                     project_name),
            ("aws_ecs_task_definition.main",             f"{project_name}:{pr['td_revision']}"),
            ("aws_ecs_service.main",                     f"{project_name}/{project_name}"),
        ])
        runner.apply()

    # ── ECS migration ──────────────────────────────────────────────────────────

    def _run_migration(
        self, project_name: str, db_url: str, region: str, creds: dict[str, Any]
    ) -> None:
        import boto3
        session = boto3.Session(
            aws_access_key_id=creds.get("access_key"),
            aws_secret_access_key=creds.get("secret_key"),
            aws_session_token=creds.get("session_token"),
            region_name=region,
        )
        ecs = session.client("ecs")
        network_config = ecs.describe_services(
            cluster=project_name, services=[project_name]
        )["services"][0]["networkConfiguration"]

        print("\n  Running Prisma migration as ECS task inside VPC...")
        run_resp = ecs.run_task(
            cluster=project_name, taskDefinition=project_name, launchType="FARGATE",
            networkConfiguration=network_config,
            overrides={"containerOverrides": [{
                "name": project_name,
                "command": ["sh", "-c", "npx prisma db push --accept-data-loss"],
                "environment": [{"name": "DATABASE_URL", "value": db_url}],
            }]},
        )
        if not run_resp.get("tasks"):
            print(
                f"\n  Warning: ECS migration task did not start: {run_resp.get('failures', [])}\n"
                f"  Apply manually: DATABASE_URL='{db_url}' npx prisma db push --accept-data-loss",
                file=sys.stderr,
            )
            return

        task_arn = run_resp["tasks"][0]["taskArn"]
        print(f"  Migration task started: {task_arn.split('/')[-1]} — waiting...")
        waiter = ecs.get_waiter("tasks_stopped")
        waiter.wait(cluster=project_name, tasks=[task_arn],
                    WaiterConfig={"Delay": 10, "MaxAttempts": 60})
        container = ecs.describe_tasks(cluster=project_name, tasks=[task_arn]
                                       )["tasks"][0]["containers"][0]
        if container.get("exitCode") == 0:
            print("  ✓ Prisma migration completed successfully.")
        else:
            print(
                f"\n  Warning: migration exited with code {container.get('exitCode')}: "
                f"{container.get('reason', '')}\n"
                f"  Apply manually: DATABASE_URL='{db_url}' npx prisma db push --accept-data-loss",
                file=sys.stderr,
            )
