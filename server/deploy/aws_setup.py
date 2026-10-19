"""Create or update the AWS resources of the Lambda deployment (docs/lambda-deployment.md).

    python server/deploy/aws_setup.py bucket      S3 bucket (private, encrypted)
    python server/deploy/aws_setup.py data        upload the fingerprint DB + rhythm index (stage.py first)
    python server/deploy/aws_setup.py image       ECR repository + build/push the image (stage.py first)
    python server/deploy/aws_setup.py function    IAM role, Lambda function, Function URL
    python server/deploy/aws_setup.py warm        EventBridge schedule that keeps one instance warm
    python server/deploy/aws_setup.py budget      monthly cost alert by e-mail (KF_BUDGET_EMAIL, KF_BUDGET_USD=10)
    python server/deploy/aws_setup.py all

Cost cap: reserved concurrency 3 and a 60 s timeout bound what runaway traffic can cost. The
budget step adds an e-mail alert on this project's costs only: it activates the Project cost
allocation tag (a billing setting; it changes no resource) and filters the budget on
Project=krump-finder, so the account's other resources are not counted. Not part of `all`.

data, image and function run only from main as pushed to origin, on a build staged from that commit
(release.py, CLAUDE.md); the image tag ends with the commit. warm also runs only from pushed main.

Every step only creates what is missing (or updates the function's code/config), never touches
resources it did not create, and tags everything Project=krump-finder. The API key is generated
once into build/api_key.txt (not in git) and set as the function's KF_API_KEY.
"""
from __future__ import annotations

import base64
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from release import pushed_main, require_staged

ROOT = Path(__file__).resolve().parents[2]
REGION = "ap-northeast-2"
ACCOUNT = boto3.client("sts").get_caller_identity()["Account"]
NAME = "krump-finder"
BUCKET = f"{NAME}-previews-{ACCOUNT}"
TAGS = {"Project": NAME}
ROLE = f"{NAME}-lambda"
# Rhythm index ~1.5 GB + JVM + the fingerprint DB in the page cache (1.2 GB, prefetched) + the pair
# index (0.2 GB). More memory also means more vCPU: 10 GB = 6 vCPU, searched as 6 speeds at once
# (RHYTHM_THREADS).
# Measured 2026-10-15: the 21-speed rhythm stage took 4.7 s at 6 GB / 3 threads, 2.9 s at 10 GB / 6.
MEMORY_MB = 10240
RHYTHM_THREADS = 6
TIMEOUT_S = 60
MAX_CONCURRENCY = 3  # cost cap: at most 3 instances at once
# The fingerprint DB, rhythm index and pair index are downloaded from here into /tmp at every cold start.
DATA_PREFIX = "runtime/"
TMP_MB = 3072  # /tmp: 1.6 GB of data + recordings
KEY_FILE = ROOT / "build" / "api_key.txt"

s3 = boto3.client("s3", region_name=REGION)
ecr = boto3.client("ecr", region_name=REGION)
iam = boto3.client("iam")
lam = boto3.client("lambda", region_name=REGION)
scheduler = boto3.client("scheduler", region_name=REGION)


def exists(call, *codes) -> bool:
    try:
        call()
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in codes:
            return False
        raise


def bucket() -> None:
    if not exists(lambda: s3.head_bucket(Bucket=BUCKET), "404", "NoSuchBucket"):
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        print("created bucket", BUCKET)
    s3.put_public_access_block(Bucket=BUCKET, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_encryption(Bucket=BUCKET, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    s3.put_bucket_tagging(Bucket=BUCKET, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in TAGS.items()]})
    print("bucket ok:", BUCKET)


def data() -> None:
    """Upload build/runtime/ (stage.py) to s3://BUCKET/runtime/, skipping unchanged files
    (same MD5 for single-part objects, same size for multipart ones)."""
    require_staged()
    import hashlib
    from boto3.s3.transfer import TransferConfig
    config = TransferConfig(max_concurrency=4, multipart_threshold=64 << 20)
    root = ROOT / "build" / "runtime"
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        key = DATA_PREFIX + f.relative_to(root).as_posix()
        try:
            head = s3.head_object(Bucket=BUCKET, Key=key)
            etag = head["ETag"].strip('"')
            same = (etag == hashlib.md5(f.read_bytes()).hexdigest() if "-" not in etag
                    else head["ContentLength"] == f.stat().st_size)
            if same:
                print("unchanged", key)
                continue
        except ClientError:
            pass
        t = time.time()
        s3.upload_file(str(f), BUCKET, key, Config=config)
        print(f"uploaded {key} ({f.stat().st_size / 1e6:.0f} MB, {time.time() - t:.0f} s)", flush=True)


def repo_uri() -> str:
    return f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{NAME}"


def image() -> str:
    if not exists(lambda: ecr.describe_repositories(repositoryNames=[NAME]), "RepositoryNotFoundException"):
        ecr.create_repository(repositoryName=NAME, tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
                              imageTagMutability="MUTABLE")
        print("created ECR repository", NAME)
    ecr.put_lifecycle_policy(repositoryName=NAME, lifecyclePolicyText=json.dumps({"rules": [{
        "rulePriority": 1, "description": "keep the last 3 images",
        "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 3},
        "action": {"type": "expire"}}]}))
    tag = time.strftime("%Y%m%d-%H%M%S") + "-" + require_staged()[:7]
    uri = f"{repo_uri()}:{tag}"
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "--provenance=false", "-t", uri,
                    str(ROOT / "build" / "lambda")], check=True)
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    subprocess.run(["docker", "login", "-u", user, "--password-stdin", auth["proxyEndpoint"]],
                   input=password.encode(), check=True)
    subprocess.run(["docker", "push", uri], check=True)
    (ROOT / "build" / "image_uri.txt").write_text(uri)
    print("pushed", uri)
    return uri


def api_key() -> str:
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(secrets.token_urlsafe(24))
    return KEY_FILE.read_text().strip()


def role_arn() -> str:
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                              Description="krump-finder Lambda: own S3 bucket + CloudWatch Logs",
                              Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])["Role"]["Arn"]
        print("created role", ROLE)
        time.sleep(10)  # IAM propagation before Lambda can assume it
    iam.attach_role_policy(RoleName=ROLE,
                           PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
    iam.put_role_policy(RoleName=ROLE, PolicyName=f"{NAME}-bucket", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [
                f"arn:aws:s3:::{BUCKET}/previews/*", f"arn:aws:s3:::{BUCKET}/{DATA_PREFIX}*"]},
            # recordings/: kept queries; videos/: per-video records (server/videos.py)
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
             "Resource": [f"arn:aws:s3:::{BUCKET}/recordings/*", f"arn:aws:s3:::{BUCKET}/videos/*"]},
            # without ListBucket a missing key is reported as AccessDenied instead of NoSuchKey
            {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": f"arn:aws:s3:::{BUCKET}"},
        ]}))
    return arn


def function() -> None:
    uri_file = ROOT / "build" / "image_uri.txt"
    uri = uri_file.read_text().strip()
    head = require_staged()
    if not uri.endswith("-" + head[:7]):
        sys.exit(f"Refusing to deploy: {uri.rsplit(':', 1)[-1]} was not built from origin/main {head[:7]}. Run the image step first.")
    env = {"Variables": {"KF_BUCKET": BUCKET, "KF_API_KEY": api_key(), "KF_DATA_PREFIX": DATA_PREFIX,
                         "RHYTHM_THREADS": str(RHYTHM_THREADS)}}
    arn = role_arn()
    if exists(lambda: lam.get_function(FunctionName=NAME), "ResourceNotFoundException"):
        lam.update_function_code(FunctionName=NAME, ImageUri=uri)
        lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
        lam.update_function_configuration(FunctionName=NAME, Role=arn, MemorySize=MEMORY_MB,
                                          Timeout=TIMEOUT_S, Environment=env,
                                          EphemeralStorage={"Size": TMP_MB})
        print("updated function", NAME, uri)
    else:
        for attempt in range(6):  # a fresh role can take a few seconds to become assumable
            try:
                lam.create_function(FunctionName=NAME, PackageType="Image", Code={"ImageUri": uri}, Role=arn,
                                    MemorySize=MEMORY_MB, Timeout=TIMEOUT_S, Environment=env,
                                    EphemeralStorage={"Size": TMP_MB},
                                    Architectures=["x86_64"], Tags=TAGS,
                                    Description="Krump Track Finder backend (server/app.py)")
                break
            except lam.exceptions.InvalidParameterValueException as e:
                if "role" not in str(e).lower() or attempt == 5:
                    raise
                time.sleep(10)
        print("created function", NAME)
    lam.get_waiter("function_active_v2").wait(FunctionName=NAME)
    lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
    lam.put_function_concurrency(FunctionName=NAME, ReservedConcurrentExecutions=MAX_CONCURRENCY)

    try:
        url = lam.get_function_url_config(FunctionName=NAME)["FunctionUrl"]
    except lam.exceptions.ResourceNotFoundException:
        url = lam.create_function_url_config(FunctionName=NAME, AuthType="NONE")["FunctionUrl"]
        # Public URL; access is limited by the app's X-Api-Key check. Both statements are
        # required for Function URLs, the second only allows invocations through the URL.
        lam.add_permission(FunctionName=NAME, StatementId="public-url", Action="lambda:InvokeFunctionUrl",
                           Principal="*", FunctionUrlAuthType="NONE")
        lam.add_permission(FunctionName=NAME, StatementId="public-url-invoke", Action="lambda:InvokeFunction",
                           Principal="*", InvokedViaFunctionUrl=True)
        print("created function URL")
    (ROOT / "build" / "function_url.txt").write_text(url)
    print("function URL:", url)
    print("API key in", KEY_FILE)
    extension_config(url)


def extension_config(url: str) -> None:
    """Bake the endpoint and API key into extension/config.js (not in git); the extension has
    no settings UI for them."""
    server = {"url": url.rstrip("/"), "apiKey": api_key()}
    (ROOT / "extension" / "config.js").write_text(
        "// Generated by server/deploy/aws_setup.py (not in git).\n"
        f"const SERVER = Object.freeze({json.dumps(server)});\n")
    print("wrote extension/config.js")


def budget() -> None:
    """Monthly budget on Project=krump-finder: e-mail when actual cost passes 80 % or the month's
    forecast passes 100 %. The tag counts only once activated, and AWS needs up to 24 h after
    activation (and after a tagged resource first appears in billing) to apply it."""
    import os

    email = os.environ.get("KF_BUDGET_EMAIL")
    if not email:
        raise SystemExit("set KF_BUDGET_EMAIL")
    limit = os.environ.get("KF_BUDGET_USD", "10")
    ce = boto3.client("ce", region_name="us-east-1")
    try:
        ce.update_cost_allocation_tags_status(CostAllocationTagsStatus=[{"TagKey": "Project", "Status": "Active"}])
        print("cost allocation tag Project: active")
    except ClientError as e:
        print(f"cost allocation tag not activated yet ({e.response['Error']['Message']}); re-run later")
    budgets = boto3.client("budgets", region_name="us-east-1")
    name = f"{NAME}-monthly"
    spec = {"BudgetName": name, "BudgetType": "COST", "TimeUnit": "MONTHLY",
            "BudgetLimit": {"Amount": limit, "Unit": "USD"},
            "CostFilters": {"TagKeyValue": [f"user:Project${NAME}"]}}
    try:
        budgets.describe_budget(AccountId=ACCOUNT, BudgetName=name)
        budgets.update_budget(AccountId=ACCOUNT, NewBudget=spec)
        print(f"updated budget {name} (${limit}/month)")
        return
    except budgets.exceptions.NotFoundException:
        pass
    subscribers = [{"SubscriptionType": "EMAIL", "Address": email}]
    budgets.create_budget(AccountId=ACCOUNT, Budget=spec, NotificationsWithSubscribers=[
        {"Notification": {"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN",
                          "Threshold": 80, "ThresholdType": "PERCENTAGE"}, "Subscribers": subscribers},
        {"Notification": {"NotificationType": "FORECASTED", "ComparisonOperator": "GREATER_THAN",
                          "Threshold": 100, "ThresholdType": "PERCENTAGE"}, "Subscribers": subscribers},
    ])
    print(f"created budget {name} (${limit}/month, alerts to {email})")


WARM_ROLE = f"{NAME}-scheduler"
WARM_RATE = "rate(5 minutes)"
# GET /health as a Function URL request, so the Lambda Web Adapter hands it to the app like any request
# (a plain event would go to its /events pass-through path instead). /health needs no API key.
WARM_EVENT = {
    "version": "2.0", "routeKey": "$default", "rawPath": "/health", "rawQueryString": "",
    "headers": {"host": "warm.local", "user-agent": f"{NAME}-warm"},
    "requestContext": {"accountId": "anonymous", "apiId": "warm", "domainName": "warm.local", "domainPrefix": "warm",
                       "http": {"method": "GET", "path": "/health", "protocol": "HTTP/1.1", "sourceIp": "127.0.0.1",
                                "userAgent": f"{NAME}-warm"},
                       "requestId": "warm", "routeKey": "$default", "stage": "$default",
                       "time": "01/Jan/2026:00:00:00 +0000", "timeEpoch": 0},
    "isBase64Encoded": False,
}


def warm() -> None:
    """Call /health every 5 minutes so an instance stays warm. A cold start takes 13-15 s since the pair
    index (2026-10-18), 3-4 s past a recording's first check at 10.5 s. A ping bills 3 ms (0.03 GB-s, ~$0.01/month
    for 8,640 pings), against ~$133/month for provisioned concurrency of one 10 GB instance. It keeps one
    instance warm only, and AWS still replaces instances every few hours."""
    pushed_main()
    fn_arn = lam.get_function(FunctionName=NAME)["Configuration"]["FunctionArn"]
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}}]}
    try:
        role = iam.get_role(RoleName=WARM_ROLE)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(RoleName=WARM_ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                               Description="krump-finder warm-up schedule: invoke the function only",
                               Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])["Role"]["Arn"]
        print("created role", WARM_ROLE)
        time.sleep(10)  # IAM propagation before the scheduler can assume it
    iam.put_role_policy(RoleName=WARM_ROLE, PolicyName=f"{NAME}-invoke", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "lambda:InvokeFunction",
                                                "Resource": [fn_arn, f"{fn_arn}:*"]}]}))
    if not exists(lambda: scheduler.get_schedule_group(Name=NAME), "ResourceNotFoundException"):
        scheduler.create_schedule_group(Name=NAME, Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])
        print("created schedule group", NAME)
    spec = dict(Name=f"{NAME}-warm", GroupName=NAME, ScheduleExpression=WARM_RATE, State="ENABLED",
                FlexibleTimeWindow={"Mode": "OFF"}, Description="GET /health every 5 minutes (aws_setup.py warm)",
                Target={"Arn": fn_arn, "RoleArn": role, "Input": json.dumps(WARM_EVENT),
                        "RetryPolicy": {"MaximumRetryAttempts": 0, "MaximumEventAgeInSeconds": 60}})
    if exists(lambda: scheduler.get_schedule(Name=spec["Name"], GroupName=NAME), "ResourceNotFoundException"):
        scheduler.update_schedule(**spec)
        print("updated schedule", spec["Name"])
    else:
        scheduler.create_schedule(**spec)
        print("created schedule", spec["Name"], WARM_RATE)


STEPS = {"bucket": bucket, "data": data, "image": image, "function": function, "warm": warm, "budget": budget}
ALL = ("bucket", "data", "image", "function", "warm")

if __name__ == "__main__":
    steps = ALL if sys.argv[1:] == ["all"] else sys.argv[1:]
    if {"data", "image", "function"} & set(steps):
        require_staged()  # deploys come only from main as pushed to origin (release.py)
    for step in steps:
        STEPS[step]()
