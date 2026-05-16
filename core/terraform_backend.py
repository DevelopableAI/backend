import sys
from pathlib import Path
from typing import Any


class TerraformBackend:
    """
    Bootstraps Terraform remote state storage before `terraform init` is run.

    Each provider creates the minimum infrastructure needed to hold state:
      AWS    → S3 bucket (versioned, encrypted) + DynamoDB table for locking
      GCP    → GCS bucket (versioned)
      Heroku → Terraform Cloud workspace (state managed by TFC)

    All operations are idempotent — re-running with the same project name
    is safe if the resources already exist.
    """

    def bootstrap(
        self, provider: str, provider_config: dict[str, Any], project_name: str
    ) -> dict[str, Any]:
        if provider == "aws":
            return self._bootstrap_aws(provider_config, project_name)
        if provider == "gcp":
            return self._bootstrap_gcp(provider_config, project_name)
        if provider == "heroku":
            return self._bootstrap_heroku(provider_config, project_name)
        print(f"Error: unknown Terraform provider '{provider}'", file=sys.stderr)
        sys.exit(1)

    # ── AWS ───────────────────────────────────────────────────────────────────

    def _bootstrap_aws(
        self, config: dict[str, Any], project_name: str
    ) -> dict[str, Any]:
        try:
            import boto3
            from botocore.exceptions import ClientError
        except ImportError:
            print("Error: boto3 is required for AWS Terraform backend bootstrap.", file=sys.stderr)
            sys.exit(1)

        region = config["aws_region"]
        bucket = config["state_bucket"]
        table = config["dynamodb_table"]

        session = boto3.Session(
            aws_access_key_id=config.get("access_key"),
            aws_secret_access_key=config.get("secret_key"),
            aws_session_token=config.get("session_token"),
            region_name=region,
        )

        self._aws_ensure_s3_bucket(session, bucket, region)
        self._aws_ensure_dynamodb_table(session, table)

        return {"bucket": bucket, "region": region, "dynamodb_table": table}

    def _aws_ensure_s3_bucket(self, session: Any, bucket: str, region: str) -> None:
        from botocore.exceptions import ClientError

        s3 = session.client("s3")
        try:
            if region == "us-east-1":
                s3.create_bucket(Bucket=bucket)
            else:
                s3.create_bucket(
                    Bucket=bucket,
                    CreateBucketConfiguration={"LocationConstraint": region},
                )
            s3.put_bucket_versioning(
                Bucket=bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
            s3.put_bucket_encryption(
                Bucket=bucket,
                ServerSideEncryptionConfiguration={
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                },
            )
            s3.put_public_access_block(
                Bucket=bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            print(f"    Created S3 bucket: {bucket}")
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                print(f"    S3 bucket already exists: {bucket}")
            else:
                print(f"Error creating S3 bucket '{bucket}': {exc}", file=sys.stderr)
                sys.exit(1)

    def _aws_ensure_dynamodb_table(self, session: Any, table: str) -> None:
        from botocore.exceptions import ClientError

        ddb = session.client("dynamodb")
        try:
            ddb.create_table(
                TableName=table,
                KeySchema=[{"AttributeName": "LockID", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "LockID", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            print(f"    Created DynamoDB table: {table}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceInUseException":
                print(f"    DynamoDB table already exists: {table}")
            else:
                print(f"Error creating DynamoDB table '{table}': {exc}", file=sys.stderr)
                sys.exit(1)

    # ── GCP ───────────────────────────────────────────────────────────────────

    def _bootstrap_gcp(
        self, config: dict[str, Any], project_name: str
    ) -> dict[str, Any]:
        try:
            from google.cloud import storage
            import google.auth
            import google.api_core.exceptions
        except ImportError:
            print(
                "Error: google-cloud-storage is required for GCP Terraform backend bootstrap.",
                file=sys.stderr,
            )
            sys.exit(1)

        project = config["gcp_project"]
        region = config["gcp_region"]
        bucket = config["state_bucket"]
        credentials_file = config.get("credentials_file")

        if credentials_file:
            from google.oauth2 import service_account
            gcp_creds = service_account.Credentials.from_service_account_file(
                credentials_file,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            client = storage.Client(project=project, credentials=gcp_creds)
        else:
            client = storage.Client(project=project)

        try:
            gcs_bucket = client.bucket(bucket)
            gcs_bucket.storage_class = "STANDARD"
            client.create_bucket(gcs_bucket, location=region)
            gcs_bucket.versioning_enabled = True
            gcs_bucket.patch()
            print(f"    Created GCS bucket: {bucket}")
        except google.api_core.exceptions.Conflict:
            print(f"    GCS bucket already exists: {bucket}")
        except Exception as exc:
            print(f"Error creating GCS bucket '{bucket}': {exc}", file=sys.stderr)
            sys.exit(1)

        return {"bucket": bucket, "project": project, "region": region}

    # ── Heroku / Terraform Cloud ──────────────────────────────────────────────

    def _bootstrap_heroku(
        self, config: dict[str, Any], project_name: str
    ) -> dict[str, Any]:
        try:
            import requests
        except ImportError:
            print("Error: requests is required for Terraform Cloud workspace bootstrap.", file=sys.stderr)
            sys.exit(1)

        tfc_token = config["tfc_token"]
        organization = config["tfc_organization"]
        workspace = config["tfc_workspace"]

        headers = {
            "Authorization": f"Bearer {tfc_token}",
            "Content-Type": "application/vnd.api+json",
        }
        url = f"https://app.terraform.io/api/v2/organizations/{organization}/workspaces"
        payload = {
            "data": {
                "type": "workspaces",
                "attributes": {
                    "name": workspace,
                    "auto-apply": False,
                    "terraform-version": "~> 1.5",
                },
            }
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=30)

        if resp.status_code == 201:
            print(f"    Created Terraform Cloud workspace: {organization}/{workspace}")
        elif resp.status_code == 422:
            body = resp.json()
            errors = body.get("errors", [])
            if any("already been taken" in e.get("detail", "") for e in errors):
                print(f"    Terraform Cloud workspace already exists: {organization}/{workspace}")
            else:
                print(f"Error creating TFC workspace: {body}", file=sys.stderr)
                sys.exit(1)
        else:
            print(
                f"Error creating TFC workspace ({resp.status_code}): {resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        return {"organization": organization, "workspace": workspace, "token": tfc_token}
