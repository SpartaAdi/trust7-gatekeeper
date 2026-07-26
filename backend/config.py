"""Runtime configuration, read from the environment set by the SAM stack."""

import functools
import os

UPLOADS_BUCKET = os.environ.get("UPLOADS_BUCKET", "")
REVIEWS_TABLE = os.environ.get("REVIEWS_TABLE", "")
REVIEW_STATUS_TABLE = os.environ.get("REVIEW_STATUS_TABLE", "")
WORKER_FUNCTION_NAME = os.environ.get("WORKER_FUNCTION_NAME", "")

# Claude API direct — not Bedrock. Pay-per-token, no provisioned throughput.
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")

# Progress records are transient; expire them so the status table stays near-empty.
STATUS_TTL_SECONDS = int(os.environ.get("STATUS_TTL_SECONDS", str(7 * 24 * 3600)))

# Presigned upload URLs are short-lived by design.
UPLOAD_URL_TTL_SECONDS = int(os.environ.get("UPLOAD_URL_TTL_SECONDS", "900"))


@functools.lru_cache(maxsize=1)
def anthropic_api_key() -> str:
    """Resolve the Anthropic API key.

    Prefers a Secrets Manager secret (the deployed path — the key never lands in
    the CloudFormation template or environment) and falls back to
    ANTHROPIC_API_KEY for local development.
    """
    secret_arn = os.environ.get("ANTHROPIC_API_KEY_SECRET_ARN", "")
    if secret_arn:
        import boto3

        secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
        return secret["SecretString"].strip()

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "No Anthropic API key: set ANTHROPIC_API_KEY_SECRET_ARN (deployed) "
            "or ANTHROPIC_API_KEY (local)."
        )
    return key
