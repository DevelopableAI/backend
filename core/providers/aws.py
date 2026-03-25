"""
AWS ECS Fargate deployment provider.

Deployment flow
───────────────
1. Detect / collect credentials (access key + secret key + region).
2. Create ECR repository (idempotent).
3. Authenticate Docker to ECR, push the image.
4. Create ECS cluster (idempotent).
5. Ensure IAM task-execution role exists with the required policy.
6. Register a new task definition revision.
7. Create or update the ECS service (Fargate, default VPC, public IP).
8. Poll until a task reaches RUNNING state, resolve its public IP.
9. Tag all created resources with standard Developable tags.
10. Return a deployment record for DeploymentState.

Database note
─────────────
The generated API needs DATABASE_URL. This provider does NOT provision RDS —
DATABASE_URL must already be set in <out_dir>/.env (collected by main.py).
It is injected as a container environment variable at deploy time.

Endpoint note
─────────────
The service is exposed via the task's public ENI IP on port 3000.
This is ephemeral (changes on task restart) and is fine for development /
initial validation. For production-grade stability add an ALB in front.
"""

import base64
import getpass
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .base import BaseProvider


# ECS task sizing (512 CPU units = 0.5 vCPU, 1024 MiB RAM — cheapest Fargate tier)
_TASK_CPU = "512"
_TASK_MEMORY = "1024"
_CONTAINER_PORT = 3000
_EXECUTION_ROLE_NAME = "developable-ecs-execution-role"
_EXECUTION_POLICY_ARN = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
_WAIT_TIMEOUT_S = 300   # 5 minutes
_POLL_INTERVAL_S = 10


class AWSProvider(BaseProvider):
    """Deploy to AWS ECS Fargate using boto3."""

    display_name = "AWS ECS Fargate"

    def __init__(self, out_dir: Path, region: str | None = None) -> None:
        super().__init__(out_dir)
        self._region = region  # may be overridden by detect/collect

    # ── Credential handling ────────────────────────────────────────────────────

    def detect_credentials(self) -> dict[str, Any] | None:
        """
        Try boto3's default credential chain (env vars → ~/.aws/credentials →
        instance profile). Also reads AWS_DEFAULT_REGION / ~/.aws/config for region.
        Returns None if credentials or region are unavailable.
        """
        try:
            import boto3
            from botocore.exceptions import NoCredentialsError, NoRegionError
        except ImportError:
            return None

        try:
            session = boto3.Session()
            creds = session.get_credentials()
            if creds is None:
                return None
            resolved = creds.resolve()
            region = self._region or session.region_name
            if not region:
                return None
            return {
                "access_key": resolved.access_key,
                "secret_key": resolved.secret_key,
                "session_token": resolved.token,
                "region": region,
            }
        except (NoCredentialsError, NoRegionError):
            return None

    def collect_credentials(self) -> dict[str, Any]:
        """Prompt the user for AWS access key, secret key, and region."""
        print("\nAWS credentials not found in environment.")
        print("Enter your AWS credentials (IAM user with ECS, ECR, IAM permissions):\n")

        access_key = input("  AWS Access Key ID: ").strip()
        if not access_key:
            print("Error: AWS Access Key ID is required.", file=sys.stderr)
            sys.exit(1)

        secret_key = getpass.getpass("  AWS Secret Access Key: ").strip()
        if not secret_key:
            print("Error: AWS Secret Access Key is required.", file=sys.stderr)
            sys.exit(1)

        region = (
            self._region
            or input("  AWS Region [us-east-1]: ").strip()
            or "us-east-1"
        )

        return {
            "access_key": access_key,
            "secret_key": secret_key,
            "session_token": None,
            "region": region,
        }

    # ── Main deploy ────────────────────────────────────────────────────────────

    def deploy(
        self,
        spec: dict[str, Any],
        image_tag: str,
        env_vars: dict[str, str],
        deployment_id: str,
    ) -> dict[str, Any]:
        import boto3

        creds = self._credentials
        region = creds["region"]
        project_name = self.slug(spec)
        tags = self.build_tags(project_name, deployment_id, spec)

        # Build boto3 session from resolved credentials
        session = boto3.Session(
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            aws_session_token=creds.get("session_token"),
            region_name=region,
        )
        ecr = session.client("ecr")
        ecs = session.client("ecs")
        iam = session.client("iam")
        ec2 = session.client("ec2")
        sts = session.client("sts")

        account_id = sts.get_caller_identity()["Account"]
        ecr_repo = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{project_name}"
        image_uri = f"{ecr_repo}:latest"

        print(f"  [AWS] Account: {account_id}  Region: {region}")

        # 1. ECR repository
        print(f"  [AWS] Ensuring ECR repository '{project_name}'...")
        repo_arn = self._ensure_ecr_repo(ecr, project_name, tags)

        # 2. Push image to ECR
        print(f"  [AWS] Pushing image to ECR...")
        self._push_to_ecr(ecr, region, image_tag, image_uri)

        # 3. ECS cluster
        cluster_name = f"{project_name}-cluster"
        print(f"  [AWS] Ensuring ECS cluster '{cluster_name}'...")
        cluster_arn = self._ensure_cluster(ecs, cluster_name, tags)

        # 4. IAM execution role
        print(f"  [AWS] Ensuring IAM execution role '{_EXECUTION_ROLE_NAME}'...")
        role_arn = self._ensure_execution_role(iam)

        # 5. Security group
        print(f"  [AWS] Ensuring security group...")
        vpc_id, subnet_ids, sg_id = self._ensure_network(ec2, project_name)

        # 6. Task definition
        td_family = f"{project_name}-task"
        print(f"  [AWS] Registering task definition '{td_family}'...")
        td_arn = self._register_task_definition(
            ecs, td_family, image_uri, role_arn, env_vars, tags
        )

        # 7. ECS service
        service_name = f"{project_name}-service"
        print(f"  [AWS] Creating/updating ECS service '{service_name}'...")
        service_arn = self._ensure_service(
            ecs, cluster_name, service_name, td_arn, subnet_ids, sg_id, tags
        )

        # 8. Wait for task to run, get public IP
        print(f"  [AWS] Waiting for service to reach RUNNING state (up to {_WAIT_TIMEOUT_S}s)...")
        public_ip = self._wait_for_public_ip(ecs, ec2, cluster_name, service_name)

        endpoint = f"http://{public_ip}:{_CONTAINER_PORT}" if public_ip else "pending"
        if not public_ip:
            print(
                "  [AWS] Warning: could not resolve public IP yet. "
                "Check ECS console for task status."
            )

        resources = [
            {"type": "ecr_repository", "id": project_name, "arn": repo_arn},
            {"type": "ecs_cluster", "id": cluster_name, "arn": cluster_arn},
            {"type": "ecs_service", "id": service_name, "arn": service_arn},
            {"type": "iam_role", "id": _EXECUTION_ROLE_NAME, "arn": role_arn},
            {"type": "security_group", "id": sg_id, "arn": None},
        ]

        from core.deployment_state import DeploymentState
        return DeploymentState.make_record(
            provider="aws",
            region=region,
            endpoint=endpoint,
            image_uri=image_uri,
            resources=resources,
            tags=tags,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _ensure_ecr_repo(
        self, ecr: Any, repo_name: str, tags: dict[str, str]
    ) -> str:
        """Create ECR repo if it doesn't exist. Returns repo ARN."""
        from botocore.exceptions import ClientError
        try:
            resp = ecr.create_repository(
                repositoryName=repo_name,
                tags=[{"Key": k, "Value": v} for k, v in tags.items()],
            )
            return resp["repository"]["repositoryArn"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "RepositoryAlreadyExistsException":
                resp = ecr.describe_repositories(repositoryNames=[repo_name])
                return resp["repositories"][0]["repositoryArn"]
            raise

    def _push_to_ecr(
        self, ecr: Any, region: str, local_tag: str, image_uri: str
    ) -> None:
        """Authenticate Docker to ECR, re-tag image, and push."""
        token_resp = ecr.get_authorization_token()
        auth_data = token_resp["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode()
        username, password = token.split(":", 1)
        registry = auth_data["proxyEndpoint"]  # e.g. https://<account>.dkr.ecr...

        self._run(["docker", "login", "--username", username, "--password-stdin", registry],
                  input=password.encode())
        self._run(["docker", "tag", local_tag, image_uri])
        self._run(["docker", "push", image_uri])

    def _ensure_cluster(
        self, ecs: Any, cluster_name: str, tags: dict[str, str]
    ) -> str:
        """Create ECS cluster if absent. Returns cluster ARN."""
        resp = ecs.create_cluster(
            clusterName=cluster_name,
            tags=[{"key": k, "value": v} for k, v in tags.items()],
        )
        return resp["cluster"]["clusterArn"]

    def _ensure_execution_role(self, iam: Any) -> str:
        """Create the ECS task execution IAM role if it doesn't exist. Returns role ARN."""
        from botocore.exceptions import ClientError

        assume_policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        })

        try:
            resp = iam.create_role(
                RoleName=_EXECUTION_ROLE_NAME,
                AssumeRolePolicyDocument=assume_policy,
                Description="Task execution role created by Developable",
            )
            role_arn = resp["Role"]["Arn"]
            iam.attach_role_policy(
                RoleName=_EXECUTION_ROLE_NAME,
                PolicyArn=_EXECUTION_POLICY_ARN,
            )
            # IAM is eventually consistent — brief pause before using the role
            time.sleep(10)
            return role_arn
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "EntityAlreadyExists":
                resp = iam.get_role(RoleName=_EXECUTION_ROLE_NAME)
                return resp["Role"]["Arn"]
            raise

    def _ensure_network(
        self, ec2: Any, project_name: str
    ) -> tuple[str, list[str], str]:
        """
        Find the default VPC + public subnets and ensure a security group
        allowing inbound TCP on port 3000 exists.

        Returns (vpc_id, subnet_ids, security_group_id).
        """
        from botocore.exceptions import ClientError

        # Default VPC
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
        if not vpcs["Vpcs"]:
            print(
                "\nError: No default VPC found in this region.\n"
                "Create a default VPC with: aws ec2 create-default-vpc",
                file=sys.stderr,
            )
            sys.exit(1)
        vpc_id = vpcs["Vpcs"][0]["VpcId"]

        # Subnets (prefer public / all subnets in default VPC)
        subnets = ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )["Subnets"]
        subnet_ids = [s["SubnetId"] for s in subnets]
        if not subnet_ids:
            print(
                "\nError: No subnets found in default VPC.", file=sys.stderr
            )
            sys.exit(1)

        # Security group
        sg_name = f"{project_name}-sg"
        try:
            sg = ec2.create_security_group(
                GroupName=sg_name,
                Description=f"Developable SG for {project_name}",
                VpcId=vpc_id,
            )
            sg_id = sg["GroupId"]
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{
                    "IpProtocol": "tcp",
                    "FromPort": _CONTAINER_PORT,
                    "ToPort": _CONTAINER_PORT,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                }],
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "InvalidGroup.Duplicate":
                sgs = ec2.describe_security_groups(
                    Filters=[
                        {"Name": "group-name", "Values": [sg_name]},
                        {"Name": "vpc-id", "Values": [vpc_id]},
                    ]
                )["SecurityGroups"]
                sg_id = sgs[0]["GroupId"]
            else:
                raise

        return vpc_id, subnet_ids, sg_id

    def _register_task_definition(
        self,
        ecs: Any,
        family: str,
        image_uri: str,
        role_arn: str,
        env_vars: dict[str, str],
        tags: dict[str, str],
    ) -> str:
        """Register a new Fargate task definition revision. Returns task def ARN."""
        environment = [{"name": k, "value": v} for k, v in env_vars.items()]
        resp = ecs.register_task_definition(
            family=family,
            networkMode="awsvpc",
            requiresCompatibilities=["FARGATE"],
            cpu=_TASK_CPU,
            memory=_TASK_MEMORY,
            executionRoleArn=role_arn,
            containerDefinitions=[{
                "name": family,
                "image": image_uri,
                "portMappings": [{"containerPort": _CONTAINER_PORT, "protocol": "tcp"}],
                "environment": environment,
                "essential": True,
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": f"/ecs/{family}",
                        "awslogs-region": self._credentials["region"],
                        "awslogs-stream-prefix": "ecs",
                        "awslogs-create-group": "true",
                    },
                },
            }],
            tags=[{"key": k, "value": v} for k, v in tags.items()],
        )
        return resp["taskDefinition"]["taskDefinitionArn"]

    def _ensure_service(
        self,
        ecs: Any,
        cluster_name: str,
        service_name: str,
        td_arn: str,
        subnet_ids: list[str],
        sg_id: str,
        tags: dict[str, str],
    ) -> str:
        """Create or update the ECS Fargate service. Returns service ARN."""
        from botocore.exceptions import ClientError

        network_config = {
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED",
            }
        }

        try:
            resp = ecs.create_service(
                cluster=cluster_name,
                serviceName=service_name,
                taskDefinition=td_arn,
                desiredCount=1,
                launchType="FARGATE",
                networkConfiguration=network_config,
                tags=[{"key": k, "value": v} for k, v in tags.items()],
            )
            return resp["service"]["serviceArn"]
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("ServiceAlreadyExistsException", "ServiceNotActiveException"):
                ecs.update_service(
                    cluster=cluster_name,
                    service=service_name,
                    taskDefinition=td_arn,
                    desiredCount=1,
                    networkConfiguration=network_config,
                    forceNewDeployment=True,
                )
                svcs = ecs.describe_services(cluster=cluster_name, services=[service_name])
                return svcs["services"][0]["serviceArn"]
            raise

    def _wait_for_public_ip(
        self, ecs: Any, ec2: Any, cluster_name: str, service_name: str
    ) -> str | None:
        """
        Poll ECS until a task is RUNNING, then resolve its ENI public IP.
        Returns the IP string or None on timeout.
        """
        deadline = time.time() + _WAIT_TIMEOUT_S
        while time.time() < deadline:
            tasks_resp = ecs.list_tasks(
                cluster=cluster_name, serviceName=service_name, desiredStatus="RUNNING"
            )
            task_arns = tasks_resp.get("taskArns", [])
            if task_arns:
                tasks = ecs.describe_tasks(cluster=cluster_name, tasks=task_arns)["tasks"]
                for task in tasks:
                    if task.get("lastStatus") != "RUNNING":
                        continue
                    for attachment in task.get("attachments", []):
                        if attachment.get("type") != "ElasticNetworkInterface":
                            continue
                        for detail in attachment.get("details", []):
                            if detail["name"] == "networkInterfaceId":
                                eni_id = detail["value"]
                                eni = ec2.describe_network_interfaces(
                                    NetworkInterfaceIds=[eni_id]
                                )["NetworkInterfaces"][0]
                                return eni.get("Association", {}).get("PublicIp")
            time.sleep(_POLL_INTERVAL_S)
            print("  [AWS] Waiting...", end="\r", flush=True)

        return None

    def _run(self, cmd: list[str], input: bytes | None = None) -> None:
        """Run a subprocess command, printing stderr on failure."""
        result = subprocess.run(
            cmd,
            input=input,
            capture_output=True,
        )
        if result.returncode != 0:
            print(
                f"\nCommand failed: {' '.join(cmd)}\n{result.stderr.decode()}",
                file=sys.stderr,
            )
            sys.exit(1)
