"""
AWS resource provisioner using boto3.

Creates all 15 Terraform-managed AWS resources (VPC, security groups, ECR, RDS,
IAM, ALB, target group, listener, CloudWatch, ECS cluster + service + task def)
with terraform-matching names so `terraform import` can reconcile state.

All creates are idempotent — safe to re-run after a partial failure.
"""

import json
import secrets as _secrets
import sys
import time
from typing import Any


class AWSProvisioner:
    """
    Provisions all 15 AWS resources required for an ECS Fargate deployment.

    RDS is kicked off first (~8-10 min); IAM, ALB, and ECS are created while
    RDS provisions in the background. Returns a dict of IDs/ARNs consumed by
    AWSDeployer for terraform import and the deployment record.
    """

    def __init__(self, creds: dict[str, Any], project_name: str, read_env_file: Any) -> None:
        self.creds = creds
        self.project_name = project_name
        self.read_env_file = read_env_file  # callable → dict[str, str]
        self.region = creds["region"]

    def provision(self) -> dict[str, Any]:
        import boto3
        session = self._session(self.creds)
        session, account_id = self._validated_session(session)
        ecr_url = f"{account_id}.dkr.ecr.{self.region}.amazonaws.com/{self.project_name}"

        ec2 = session.client("ec2")
        ecr = session.client("ecr")
        ecs = session.client("ecs")
        iam = session.client("iam")
        rds = session.client("rds")
        elb = session.client("elbv2")
        logs = session.client("logs")

        vpc_id, subnet_ids = self._default_vpc_and_subnets(ec2)

        print(f"  [boto3] Ensuring security groups...")
        alb_sg_id = self._ensure_sg(ec2, vpc_id, f"{self.project_name}-alb-sg", "Managed by Terraform",
            [{"proto": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}])
        ecs_sg_id = self._ensure_sg(ec2, vpc_id, f"{self.project_name}-ecs-sg", "Managed by Terraform",
            [{"proto": "tcp", "from_port": 3000, "to_port": 3000, "src_sg": alb_sg_id}])
        rds_sg_id = self._ensure_sg(ec2, vpc_id, f"{self.project_name}-rds-sg", "Managed by Terraform",
            [{"proto": "tcp", "from_port": 5432, "to_port": 5432, "src_sg": ecs_sg_id}])

        print(f"  [boto3] Ensuring ECR repository '{self.project_name}'...")
        self._ensure_ecr(ecr)

        env_vars = self.read_env_file()
        db_password = env_vars.get("DB_PASSWORD") or _secrets.token_urlsafe(24)
        jwt_secret = env_vars.get("JWT_SECRET") or _secrets.token_urlsafe(32)
        db_name = self.project_name.replace("-", "_")
        subnet_group = f"{self.project_name}-db-subnet-group"

        self._ensure_db_subnet_group(rds, subnet_group, subnet_ids)
        self._kick_off_rds(rds, db_password, db_name, rds_sg_id, subnet_group)

        role_name, role_arn, policy_arn = self._ensure_iam_role(iam)
        alb_arn, alb_dns = self._ensure_alb(elb, subnet_ids, alb_sg_id)
        tg_arn = self._ensure_target_group_with_retry(elb, vpc_id, alb_arn)
        listener_arn = self._ensure_listener(elb, alb_arn, tg_arn)

        log_group = f"/ecs/{self.project_name}"
        self._ensure_log_group(logs, log_group)

        print(f"  [boto3] Ensuring ECS cluster '{self.project_name}'...")
        ecs.create_cluster(clusterName=self.project_name)

        rds_endpoint = self._wait_for_rds(rds)
        db_url = f"postgresql://postgres:{db_password}@{rds_endpoint}:5432/{db_name}"

        print(f"  [boto3] Registering placeholder task definition '{self.project_name}'...")
        td_resp = ecs.register_task_definition(
            family=self.project_name, networkMode="awsvpc",
            requiresCompatibilities=["FARGATE"], cpu="256", memory="512",
            executionRoleArn=role_arn,
            containerDefinitions=[{
                "name": self.project_name, "image": f"{ecr_url}:placeholder",
                "portMappings": [{"containerPort": 3000, "protocol": "tcp"}],
                "environment": [
                    {"name": "NODE_ENV", "value": "production"},
                    {"name": "PORT", "value": "3000"},
                    {"name": "DATABASE_URL", "value": db_url},
                ],
                "essential": True,
                "logConfiguration": {"logDriver": "awslogs", "options": {
                    "awslogs-group": log_group, "awslogs-region": self.region,
                    "awslogs-stream-prefix": "ecs",
                }},
            }],
        )
        td_arn = td_resp["taskDefinition"]["taskDefinitionArn"]
        td_revision = td_resp["taskDefinition"]["revision"]

        print(f"  [boto3] Ensuring ECS service '{self.project_name}'...")
        self._ensure_ecs_service(ecs, td_arn, subnet_ids, ecs_sg_id, tg_arn)

        return {
            "alb_sg_id": alb_sg_id, "ecs_sg_id": ecs_sg_id, "rds_sg_id": rds_sg_id,
            "ecr_url": ecr_url, "rds_endpoint": rds_endpoint,
            "db_password": db_password, "jwt_secret": jwt_secret, "db_url": db_url,
            "role_name": role_name, "role_arn": role_arn, "policy_arn": policy_arn,
            "alb_arn": alb_arn, "alb_dns": alb_dns, "tg_arn": tg_arn,
            "listener_arn": listener_arn, "log_group": log_group,
            "td_arn": td_arn, "td_revision": td_revision,
            "subnet_group": subnet_group, "subnet_ids": subnet_ids, "vpc_id": vpc_id,
        }

    # ── Session / auth ─────────────────────────────────────────────────────────

    @staticmethod
    def _session(creds: dict[str, Any], region: str | None = None) -> Any:
        import boto3
        return boto3.Session(
            aws_access_key_id=creds.get("access_key"),
            aws_secret_access_key=creds.get("secret_key"),
            aws_session_token=creds.get("session_token"),
            region_name=region or creds.get("region"),
        )

    def _validated_session(self, session: Any) -> tuple[Any, str]:
        import os
        from botocore.exceptions import ClientError
        while True:
            try:
                account_id = session.client("sts").get_caller_identity()["Account"]
                return session, account_id
            except ClientError as e:
                if e.response["Error"]["Code"] not in (
                    "InvalidClientTokenId", "ExpiredTokenException", "AuthFailure", "UnauthorizedOperation"
                ):
                    raise
                _wait_for_user_action(
                    "AWS credentials are invalid or expired.",
                    ["Export fresh credentials:", "  export AWS_ACCESS_KEY_ID=<key>",
                     "  export AWS_SECRET_ACCESS_KEY=<secret>",
                     "  export AWS_SESSION_TOKEN=<token>  # if using STS",
                     "Verify with: aws sts get-caller-identity"],
                )
                session = self._session({
                    "access_key": os.environ.get("AWS_ACCESS_KEY_ID"),
                    "secret_key": os.environ.get("AWS_SECRET_ACCESS_KEY"),
                    "session_token": os.environ.get("AWS_SESSION_TOKEN"),
                    "region": self.region,
                })

    # ── VPC ────────────────────────────────────────────────────────────────────

    def _default_vpc_and_subnets(self, ec2: Any) -> tuple[str, list[str]]:
        while True:
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
            if vpcs["Vpcs"]:
                break
            _wait_for_user_action(
                "No default VPC found in this AWS region.",
                [f"Create one with: aws ec2 create-default-vpc --region {self.region}",
                 "Or create it in the VPC console: https://console.aws.amazon.com/vpc/",
                 "Then press Enter to retry"],
            )
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
            if not vpcs["Vpcs"]:
                print(f"\nError: still no default VPC in {self.region}.", file=sys.stderr)
                sys.exit(1)
        vpc_id = vpcs["Vpcs"][0]["VpcId"]
        subnet_ids = [s["SubnetId"] for s in ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]]
        return vpc_id, subnet_ids

    # ── Idempotent resource helpers ────────────────────────────────────────────

    @staticmethod
    def _ensure_sg(
        ec2: Any, vpc_id: str, name: str, description: str, rules: list[dict[str, Any]]
    ) -> str:
        from botocore.exceptions import ClientError
        try:
            sg_id = ec2.create_security_group(
                GroupName=name, Description=description, VpcId=vpc_id)["GroupId"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidGroup.Duplicate":
                raise
            sg_id = ec2.describe_security_groups(Filters=[
                {"Name": "group-name", "Values": [name]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ])["SecurityGroups"][0]["GroupId"]
        for rule in rules:
            perm: dict[str, Any] = {
                "IpProtocol": rule["proto"], "FromPort": rule["from_port"], "ToPort": rule["to_port"],
            }
            perm["IpRanges" if "cidr" in rule else "UserIdGroupPairs"] = (
                [{"CidrIp": rule["cidr"]}] if "cidr" in rule else [{"GroupId": rule["src_sg"]}]
            )
            try:
                ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[perm])
            except ClientError as e:
                if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                    raise
        return sg_id

    def _ensure_ecr(self, ecr: Any) -> None:
        from botocore.exceptions import ClientError
        try:
            ecr.create_repository(repositoryName=self.project_name,
                imageScanningConfiguration={"scanOnPush": True})
        except ClientError as e:
            if e.response["Error"]["Code"] != "RepositoryAlreadyExistsException":
                raise

    def _ensure_db_subnet_group(self, rds: Any, subnet_group: str, subnet_ids: list[str]) -> None:
        from botocore.exceptions import ClientError
        try:
            rds.create_db_subnet_group(
                DBSubnetGroupName=subnet_group,
                DBSubnetGroupDescription=f"Developable DB subnet group for {self.project_name}",
                SubnetIds=subnet_ids,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "DBSubnetGroupAlreadyExists":
                raise

    def _kick_off_rds(
        self, rds: Any, db_password: str, db_name: str, rds_sg_id: str, subnet_group: str
    ) -> None:
        from botocore.exceptions import ClientError
        print(f"  [boto3] Kicking off RDS provisioning (5-10 min) — continuing with other resources...")
        try:
            rds.create_db_instance(
                DBInstanceIdentifier=self.project_name, DBInstanceClass="db.t3.micro",
                Engine="postgres", EngineVersion="15", MasterUsername="postgres",
                MasterUserPassword=db_password, DBName=db_name,
                VpcSecurityGroupIds=[rds_sg_id], DBSubnetGroupName=subnet_group,
                PubliclyAccessible=False, MultiAZ=False, StorageType="gp2",
                AllocatedStorage=20, BackupRetentionPeriod=1,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "DBInstanceAlreadyExists":
                raise
            print(f"  [boto3] RDS '{self.project_name}' already exists — reusing.")

    def _ensure_iam_role(self, iam: Any) -> tuple[str, str, str]:
        from botocore.exceptions import ClientError
        role_name = f"{self.project_name}-ecs-execution"
        policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
        assume = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}],
        })
        print(f"  [boto3] Ensuring IAM role '{role_name}'...")
        try:
            role_arn = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=assume,
                Description=f"ECS execution role for {self.project_name}")["Role"]["Arn"]
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            time.sleep(10)
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
            else:
                raise
        return role_name, role_arn, policy_arn

    def _ensure_alb(self, elb: Any, subnet_ids: list[str], sg_id: str) -> tuple[str, str]:
        from botocore.exceptions import ClientError
        print(f"  [boto3] Ensuring ALB '{self.project_name}'...")
        try:
            lbs = elb.describe_load_balancers(Names=[self.project_name])["LoadBalancers"]
            return lbs[0]["LoadBalancerArn"], lbs[0]["DNSName"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "LoadBalancerNotFound":
                raise
        lb = elb.create_load_balancer(
            Name=self.project_name, Subnets=subnet_ids, SecurityGroups=[sg_id],
            Scheme="internet-facing", Type="application", IpAddressType="ipv4",
        )["LoadBalancers"][0]
        return lb["LoadBalancerArn"], lb["DNSName"]

    def _ensure_target_group(self, elb: Any, vpc_id: str) -> str:
        from botocore.exceptions import ClientError
        try:
            resp = elb.describe_target_groups(Names=[self.project_name])
            if resp["TargetGroups"]:
                return resp["TargetGroups"][0]["TargetGroupArn"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "TargetGroupNotFound":
                raise
        return elb.create_target_group(
            Name=self.project_name, Protocol="HTTP", Port=3000, VpcId=vpc_id,
            TargetType="ip", HealthCheckProtocol="HTTP", HealthCheckPath="/health",
            HealthyThresholdCount=2, UnhealthyThresholdCount=3, HealthCheckIntervalSeconds=30,
        )["TargetGroups"][0]["TargetGroupArn"]

    def _ensure_target_group_with_retry(self, elb: Any, vpc_id: str, alb_arn: str) -> str:
        from botocore.exceptions import ClientError
        tg_arn = self._ensure_target_group(elb, vpc_id)
        try:
            self._ensure_listener(elb, alb_arn, tg_arn)
        except ClientError as err:
            if err.response["Error"]["Code"] != "TargetGroupAssociationLimitException":
                raise
            print(f"  [boto3] Stale TG association — recycling target group (may take ~60s)...", flush=True)
            deadline = time.time() + 300
            while time.time() < deadline:
                try:
                    elb.delete_target_group(TargetGroupArn=tg_arn)
                    break
                except ClientError as de:
                    if de.response["Error"]["Code"] != "ResourceInUse":
                        raise
                    time.sleep(10)
            else:
                raise RuntimeError(f"Stale target group {tg_arn} could not be deleted within 5 minutes")
            gone_deadline = time.time() + 60
            while time.time() < gone_deadline:
                try:
                    elb.describe_target_groups(TargetGroupArns=[tg_arn])
                    time.sleep(5)
                except ClientError as ge:
                    if ge.response["Error"]["Code"] == "TargetGroupNotFound":
                        break
                    raise
            tg_arn = self._ensure_target_group(elb, vpc_id)
        return tg_arn

    @staticmethod
    def _ensure_listener(elb: Any, alb_arn: str, tg_arn: str) -> str:
        existing = elb.describe_listeners(LoadBalancerArn=alb_arn)["Listeners"]
        for l in existing:
            if l["Port"] == 80:
                return l["ListenerArn"]
        return elb.create_listener(
            LoadBalancerArn=alb_arn, Protocol="HTTP", Port=80,
            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
        )["Listeners"][0]["ListenerArn"]

    def _ensure_log_group(self, logs: Any, log_group: str) -> None:
        from botocore.exceptions import ClientError
        try:
            logs.create_log_group(logGroupName=log_group)
            logs.put_retention_policy(logGroupName=log_group, retentionInDays=7)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise

    def _ensure_ecs_service(
        self, ecs: Any, td_arn: str, subnet_ids: list[str], ecs_sg_id: str, tg_arn: str
    ) -> None:
        from botocore.exceptions import ClientError
        try:
            ecs.create_service(
                cluster=self.project_name, serviceName=self.project_name,
                taskDefinition=td_arn, desiredCount=0, launchType="FARGATE",
                networkConfiguration={"awsvpcConfiguration": {
                    "subnets": subnet_ids, "securityGroups": [ecs_sg_id], "assignPublicIp": "ENABLED",
                }},
                loadBalancers=[{
                    "targetGroupArn": tg_arn,
                    "containerName": self.project_name,
                    "containerPort": 3000,
                }],
            )
        except ClientError as e:
            if e.response["Error"]["Code"] not in (
                "ServiceAlreadyExistsException", "ServiceNotActiveException"
            ):
                raise
            print(f"  [boto3] ECS service '{self.project_name}' already exists.")

    def _wait_for_rds(self, rds: Any) -> str:
        from botocore.exceptions import ClientError
        print(f"  [boto3] Waiting for RDS to become available...", flush=True)
        while True:
            deadline = time.time() + 900
            rds_endpoint = None
            while time.time() < deadline:
                try:
                    resp = rds.describe_db_instances(DBInstanceIdentifier=self.project_name)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "DBInstanceNotFound":
                        time.sleep(15)
                        continue
                    raise
                inst = resp["DBInstances"][0]
                if inst["DBInstanceStatus"] == "available":
                    rds_endpoint = inst["Endpoint"]["Address"]
                    print(f"  [boto3] ✓ RDS available — {rds_endpoint}")
                    break
                print(f"  [boto3] RDS status: {inst['DBInstanceStatus']}...", end="\r", flush=True)
                time.sleep(15)
            if rds_endpoint:
                return rds_endpoint
            _wait_for_user_action(
                f"RDS instance '{self.project_name}' did not become available within 15 minutes.",
                [f"Check the RDS console: https://console.aws.amazon.com/rds/",
                 f"Look for instance: {self.project_name}  (region: {self.region})",
                 "If status is 'failed': delete the instance and press Enter to retry",
                 "If status is still 'creating': press Enter to wait another 15 minutes"],
            )


def _wait_for_user_action(message: str, steps: list[str]) -> None:
    print(f"\n  ⚠  {message}", flush=True)
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")
    print("\n  Press Enter once resolved to retry...", flush=True)
    try:
        input()
    except EOFError:
        time.sleep(10)
