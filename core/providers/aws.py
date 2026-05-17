"""
AWS ECS Fargate deployment provider with RDS PostgreSQL database provisioning.

Deployment flow
───────────────
1. Detect / collect credentials (access key + secret key + region).
2. Provision RDS PostgreSQL 15 (db.t3.micro) in the default VPC.
3. Apply Prisma schema to the remote database (npx prisma db push).
4. Create ECR repository (idempotent).
5. Authenticate Docker to ECR, push the image.
6. Create ECS cluster (idempotent).
7. Ensure IAM task-execution role exists with the required policy.
8. Register a new task definition revision (DATABASE_URL = remote RDS URL).
9. Create or update the ECS service (Fargate, default VPC, public IP).
10. Poll until a task reaches RUNNING state, resolve its public IP.
11. Tag all created resources with standard Developable tags.
12. Return a deployment record for DeploymentState.

Database (RDS PostgreSQL)
─────────────────────────
- db.t3.micro (~$13/month), PostgreSQL 15, single-AZ, gp2 storage (20 GiB).
- PubliclyAccessible=True so the local Prisma migration can reach it.
- Security group allows TCP 5432 from 0.0.0.0/0.
- WARNING: restrict this to your IP range for production workloads.

Endpoint note
─────────────
The ECS service is exposed via the task's public ENI IP on port 3000.
This IP is ephemeral (changes on task restart). For stable production
traffic add an ALB in front of the ECS service.
"""

import base64
import getpass
import json
import secrets
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

_ECS_WAIT_TIMEOUT_S = 300   # 5 minutes — ECS service stabilisation
_RDS_WAIT_TIMEOUT_S = 900   # 15 minutes — RDS provisioning
_POLL_INTERVAL_S = 15


class AWSProvider(BaseProvider):
    """Deploy to AWS ECS Fargate using boto3, with RDS PostgreSQL provisioning."""

    display_name = "AWS ECS Fargate"

    def __init__(self, out_dir: Path, region: str | None = None) -> None:
        super().__init__(out_dir)
        self._region = region  # may be overridden by detect/collect
        # Set by _ensure_network(); reused by provision_database()
        self._vpc_id: str = ""
        self._subnet_ids: list[str] = []
        self._ecs_sg_id: str = ""
        # Set by provision_database(); used by deploy() to lock down after ECS is running
        self._rds_sg_id: str = ""

    # ── Credential handling ────────────────────────────────────────────────────

    def detect_credentials(self) -> dict[str, Any] | None:
        """
        Try boto3's default credential chain (env vars → ~/.aws/credentials →
        instance profile). Also reads AWS_DEFAULT_REGION / ~/.aws/config for region.
        Returns None if credentials or region are unavailable.
        """
        try:
            import boto3
        except ImportError:
            return None

        try:
            session = boto3.Session()
            creds = session.get_credentials()
            if creds is None:
                return None
            resolved = creds.get_frozen_credentials()
            region = self._region or session.region_name
            if not region:
                return None
            return {
                "access_key": resolved.access_key,
                "secret_key": resolved.secret_key,
                "session_token": resolved.token,
                "region": region,
            }
        except Exception:
            return None

    def collect_credentials(self) -> dict[str, Any]:
        """Prompt the user for AWS access key, secret key, and region."""
        print("\nAWS credentials not found in environment.")
        print("Enter your AWS credentials (IAM user with ECS, ECR, RDS, IAM permissions):\n")

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

    # ── Database provisioning ──────────────────────────────────────────────────

    def provision_database(
        self,
        spec: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Provision an RDS PostgreSQL 15 instance in the default VPC.

        Network resources (VPC, subnets) are shared with the ECS service and
        are set up during deploy() via _ensure_network(). If _ensure_network()
        hasn't run yet (e.g. provision_database is called first), we call it now.
        """
        import boto3
        from botocore.exceptions import ClientError

        creds = self._credentials
        region = creds["region"]
        tags = self.build_tags(project_name, deployment_id, spec)

        session = boto3.Session(
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            aws_session_token=creds.get("session_token"),
            region_name=region,
        )
        ec2 = session.client("ec2")
        rds = session.client("rds")

        # Ensure we have VPC / subnet info (deploy() sets these; handle early calls)
        if not self._subnet_ids:
            self._vpc_id, self._subnet_ids, self._ecs_sg_id = self._ensure_network(ec2, project_name)

        db_password = secrets.token_urlsafe(16)
        db_name = project_name.replace("-", "_")
        instance_id = f"{project_name}-db"
        subnet_group_name = f"{project_name}-db-subnet"

        print(f"  [AWS] Ensuring DB subnet group '{subnet_group_name}'...")
        self._ensure_db_subnet_group(rds, subnet_group_name, self._subnet_ids, tags)

        print(f"  [AWS] Ensuring RDS security group for port 5432...")
        rds_sg_id = self._ensure_rds_sg(ec2, project_name, self._vpc_id)
        self._rds_sg_id = rds_sg_id  # saved so deploy() can lock it to ECS-only after migration

        print(f"  [AWS] Creating RDS PostgreSQL instance '{instance_id}' (db.t3.micro)...")
        print(f"        This typically takes 5–10 minutes. Please wait...")
        instance_arn, endpoint = self._ensure_rds_instance(
            rds, instance_id, db_name, db_password, subnet_group_name, rds_sg_id, tags, region
        )

        db_url = f"postgresql://postgres:{db_password}@{endpoint}:5432/{db_name}"
        print(f"  [AWS] RDS endpoint: {endpoint}")
        print(
            "  [AWS] Warning: RDS instance is publicly accessible (port 5432 open to 0.0.0.0/0).\n"
            "         Restrict the security group for production workloads."
        )

        resources = [
            {
                "type": "rds_instance",
                "id": instance_id,
                "arn": instance_arn,
                "endpoint": endpoint,
                "db_name": db_name,
            }
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
        import boto3

        creds = self._credentials
        region = creds["region"]
        project_name = self.slug(spec)
        tags = self.build_tags(project_name, deployment_id, spec)

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

        # Network (sets self._vpc_id, self._subnet_ids, self._ecs_sg_id)
        if not self._subnet_ids:
            self._vpc_id, self._subnet_ids, self._ecs_sg_id = self._ensure_network(ec2, project_name)

        # ECR repository
        print(f"  [AWS] Ensuring ECR repository '{project_name}'...")
        repo_arn = self._ensure_ecr_repo(ecr, project_name, tags)

        # Push image
        print(f"  [AWS] Pushing image to ECR...")
        self._push_to_ecr(ecr, region, image_tag, image_uri)

        # ECS cluster
        cluster_name = f"{project_name}-cluster"
        print(f"  [AWS] Ensuring ECS cluster '{cluster_name}'...")
        cluster_arn = self._ensure_cluster(ecs, cluster_name, tags)

        # IAM role
        print(f"  [AWS] Ensuring IAM execution role '{_EXECUTION_ROLE_NAME}'...")
        role_arn = self._ensure_execution_role(iam)

        # CloudWatch log group (pre-create so execution role doesn't need logs:CreateLogGroup,
        # which is not included in AmazonECSTaskExecutionRolePolicy)
        td_family = f"{project_name}-task"
        log_group = f"/ecs/{td_family}"
        print(f"  [AWS] Ensuring CloudWatch log group '{log_group}'...")
        self._ensure_log_group(session, log_group, tags, region)

        # Task definition
        print(f"  [AWS] Registering task definition '{td_family}'...")
        td_arn = self._register_task_definition(
            ecs, td_family, image_uri, role_arn, env_vars, tags, region
        )

        # ECS service
        service_name = f"{project_name}-service"
        print(f"  [AWS] Creating/updating ECS service '{service_name}'...")
        service_arn = self._ensure_service(
            ecs, cluster_name, service_name, td_arn, self._subnet_ids, self._ecs_sg_id, tags
        )

        # Wait for running task + public IP
        print(f"  [AWS] Waiting for service to reach RUNNING state (up to {_ECS_WAIT_TIMEOUT_S}s)...")
        public_ip = self._wait_for_public_ip(ecs, ec2, cluster_name, service_name)

        endpoint = f"http://{public_ip}:{_CONTAINER_PORT}" if public_ip else "pending"
        if not public_ip:
            print(
                "  [AWS] Warning: could not resolve public IP yet. "
                "Check ECS console for task status."
            )

        # Lock RDS SG to ECS-only now that Prisma migration is done and ECS is running
        if self._rds_sg_id:
            print(f"  [AWS] Locking RDS security group to ECS-only access...")
            self._lock_rds_to_ecs(ec2, self._rds_sg_id, self._ecs_sg_id)

        resources = [
            {"type": "ecr_repository", "id": project_name, "arn": repo_arn},
            {"type": "ecs_cluster", "id": cluster_name, "arn": cluster_arn},
            {"type": "ecs_service", "id": service_name, "arn": service_arn},
            {"type": "iam_role", "id": _EXECUTION_ROLE_NAME, "arn": role_arn},
            {"type": "security_group", "id": self._ecs_sg_id, "arn": None},
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

    # ── CI/CD workflow generation ──────────────────────────────────────────────

    def generate_deploy_workflow(
        self,
        project_name: str,
        record: dict[str, Any],
    ) -> str:
        """Return a GitHub Actions deploy.yml for AWS ECS Fargate."""
        region = record.get("region", "us-east-1")
        cluster_name = f"{project_name}-cluster"
        service_name = f"{project_name}-service"

        # Build ECR registry URL from the image_uri in the record
        image_uri = record.get("image_uri", "")
        # image_uri format: <account>.dkr.ecr.<region>.amazonaws.com/<project>:latest
        ecr_registry = image_uri.rsplit("/", 1)[0] if "/" in image_uri else ""

        return f"""\
name: Deploy to AWS ECS

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

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{{{ secrets.AWS_ACCESS_KEY_ID }}}}
          aws-secret-access-key: ${{{{ secrets.AWS_SECRET_ACCESS_KEY }}}}
          aws-region: {region}

      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build, tag, and push image to ECR
        env:
          ECR_REGISTRY: ${{{{ steps.login-ecr.outputs.registry }}}}
        run: |
          docker build -t $ECR_REGISTRY/{project_name}:latest .
          docker push $ECR_REGISTRY/{project_name}:latest

      - name: Force new ECS deployment
        run: |
          aws ecs update-service \\
            --cluster {cluster_name} \\
            --service {service_name} \\
            --force-new-deployment \\
            --region {region}
"""

    # ── Private helpers: RDS ───────────────────────────────────────────────────

    def _ensure_db_subnet_group(
        self,
        rds: Any,
        subnet_group_name: str,
        subnet_ids: list[str],
        tags: dict[str, str],
    ) -> None:
        from botocore.exceptions import ClientError
        try:
            rds.create_db_subnet_group(
                DBSubnetGroupName=subnet_group_name,
                DBSubnetGroupDescription="Developable managed DB subnet group",
                SubnetIds=subnet_ids,
                Tags=[{"Key": k, "Value": v} for k, v in tags.items()],
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "DBSubnetGroupAlreadyExists":
                raise

    def _ensure_rds_sg(self, ec2: Any, project_name: str, vpc_id: str) -> str:
        """Create (or reuse) a security group that allows inbound TCP 5432 from all IPs.

        The open rule is required so apply_schema() can reach the DB from the local
        machine. _lock_rds_to_ecs() removes it after ECS is deployed; on re-runs this
        method restores it so subsequent apply_schema() calls still work.
        """
        from botocore.exceptions import ClientError
        sg_name = f"{project_name}-rds-sg"

        # Create or reuse the security group
        try:
            sg = ec2.create_security_group(
                GroupName=sg_name,
                Description=f"Developable RDS SG for {project_name} (port 5432)",
                VpcId=vpc_id,
            )
            sg_id = sg["GroupId"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "InvalidGroup.Duplicate":
                raise
            sgs = ec2.describe_security_groups(
                Filters=[
                    {"Name": "group-name", "Values": [sg_name]},
                    {"Name": "vpc-id", "Values": [vpc_id]},
                ]
            )["SecurityGroups"]
            sg_id = sgs[0]["GroupId"]

        # Ensure 0.0.0.0/0 rule is present so the local machine can run apply_schema().
        # On re-runs _lock_rds_to_ecs() will have removed it — restore it here.
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                }],
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise
            # Rule already present — no action needed

        return sg_id

    def _lock_rds_to_ecs(self, ec2: Any, rds_sg_id: str, ecs_sg_id: str) -> None:
        """
        Replace the 0.0.0.0/0 inbound rule on the RDS security group with a
        rule that allows TCP 5432 only from the ECS security group.
        Called after apply_schema() and ECS deployment are both complete.
        Both operations are idempotent — safe to call on re-deploys.
        """
        from botocore.exceptions import ClientError

        # Remove the open-world rule (may already be gone on a re-deploy)
        try:
            ec2.revoke_security_group_ingress(
                GroupId=rds_sg_id,
                IpPermissions=[{
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                }],
            )
            print(f"  [AWS] Revoked 0.0.0.0/0 ingress from RDS security group.")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "InvalidPermission.NotFound":
                raise
            # Rule already removed (e.g. previous deploy run) — that's fine

        # Allow traffic from the ECS security group only
        try:
            ec2.authorize_security_group_ingress(
                GroupId=rds_sg_id,
                IpPermissions=[{
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "UserIdGroupPairs": [{"GroupId": ecs_sg_id}],
                }],
            )
            print(f"  [AWS] RDS security group now only allows traffic from ECS SG ({ecs_sg_id}).")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise
            # Rule already exists (re-deploy) — no action needed

    def _ensure_rds_instance(
        self,
        rds: Any,
        instance_id: str,
        db_name: str,
        password: str,
        subnet_group_name: str,
        rds_sg_id: str,
        tags: dict[str, str],
        region: str,
    ) -> tuple[str, str]:
        """
        Create the RDS instance if absent, or reuse an existing one.
        Returns (instance_arn, endpoint_address).
        Blocks until the instance is available.
        """
        from botocore.exceptions import ClientError

        try:
            rds.create_db_instance(
                DBInstanceIdentifier=instance_id,
                DBInstanceClass="db.t3.micro",
                Engine="postgres",
                EngineVersion="15",
                MasterUsername="postgres",
                MasterUserPassword=password,
                DBName=db_name,
                VpcSecurityGroupIds=[rds_sg_id],
                DBSubnetGroupName=subnet_group_name,
                PubliclyAccessible=True,
                MultiAZ=False,
                StorageType="gp2",
                AllocatedStorage=20,
                BackupRetentionPeriod=1,
                Tags=[{"Key": k, "Value": v} for k, v in tags.items()],
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "DBInstanceAlreadyExists":
                raise
            print(f"  [AWS] RDS instance '{instance_id}' already exists — reusing.")

        # Poll until available
        deadline = time.time() + _RDS_WAIT_TIMEOUT_S
        while time.time() < deadline:
            resp = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
            inst = resp["DBInstances"][0]
            status = inst["DBInstanceStatus"]
            if status == "available":
                arn = inst["DBInstanceArn"]
                endpoint = inst["Endpoint"]["Address"]
                return arn, endpoint
            print(f"  [AWS] RDS status: {status} — waiting...", end="\r", flush=True)
            time.sleep(_POLL_INTERVAL_S)

        print(
            f"\n  [AWS] Warning: RDS instance '{instance_id}' did not become "
            f"available within {_RDS_WAIT_TIMEOUT_S}s. Check RDS console.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Private helpers: ECR / ECS ─────────────────────────────────────────────

    def _ensure_ecr_repo(
        self, ecr: Any, repo_name: str, tags: dict[str, str]
    ) -> str:
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
        token_resp = ecr.get_authorization_token()
        auth_data = token_resp["authorizationData"][0]
        token = base64.b64decode(auth_data["authorizationToken"]).decode()
        username, password = token.split(":", 1)
        registry = auth_data["proxyEndpoint"]

        self._run(["docker", "login", "--username", username, "--password-stdin", registry],
                  input=password.encode())
        self._run(["docker", "tag", local_tag, image_uri])
        self._run(["docker", "push", image_uri])

    def _ensure_log_group(
        self, session: Any, log_group: str, tags: dict[str, str], region: str
    ) -> None:
        """
        Pre-create the CloudWatch log group so the ECS execution role doesn't
        need logs:CreateLogGroup (not included in AmazonECSTaskExecutionRolePolicy).
        Idempotent — silently skips if the group already exists.
        """
        from botocore.exceptions import ClientError

        logs = session.client("logs", region_name=region)
        try:
            logs.create_log_group(
                logGroupName=log_group,
                tags=tags,
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise
            # Group already exists (re-deploy) — no action needed

    def _ensure_cluster(
        self, ecs: Any, cluster_name: str, tags: dict[str, str]
    ) -> str:
        resp = ecs.create_cluster(
            clusterName=cluster_name,
            tags=[{"key": k, "value": v} for k, v in tags.items()],
        )
        return resp["cluster"]["clusterArn"]

    def _ensure_execution_role(self, iam: Any) -> str:
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
            time.sleep(10)  # IAM is eventually consistent
            return role_arn
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "EntityAlreadyExists":
                resp = iam.get_role(RoleName=_EXECUTION_ROLE_NAME)
                return resp["Role"]["Arn"]
            raise

    def _ensure_network(
        self, ec2: Any, project_name: str
    ) -> tuple[str, list[str], str]:
        """Find default VPC + subnets, create ECS security group."""
        from botocore.exceptions import ClientError

        vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
        if not vpcs["Vpcs"]:
            print(
                "\nError: No default VPC found in this region.\n"
                "Create one with: aws ec2 create-default-vpc",
                file=sys.stderr,
            )
            sys.exit(1)
        vpc_id = vpcs["Vpcs"][0]["VpcId"]

        subnets = ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )["Subnets"]
        subnet_ids = [s["SubnetId"] for s in subnets]
        if not subnet_ids:
            print("\nError: No subnets found in default VPC.", file=sys.stderr)
            sys.exit(1)

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
            if exc.response["Error"]["Code"] == "InvalidGroup.Duplicate":
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
        region: str,
    ) -> str:
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
                        "awslogs-region": region,
                        "awslogs-stream-prefix": "ecs",
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
        deadline = time.time() + _ECS_WAIT_TIMEOUT_S
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
            print("  [AWS] Waiting for task...", end="\r", flush=True)
        return None

    def _run(self, cmd: list[str], input: bytes | None = None) -> None:
        result = subprocess.run(cmd, input=input, capture_output=True)
        if result.returncode != 0:
            print(
                f"\nCommand failed: {' '.join(cmd)}\n{result.stderr.decode()}",
                file=sys.stderr,
            )
            sys.exit(1)
