# Lambda container image of server/app.py. Build context: build/lambda (server/deploy/stage.py).
# The FastAPI app runs unchanged behind the Lambda Web Adapter, which turns invocations into
# HTTP requests on port 8080.
FROM python:3.12-slim-bookworm

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jre-headless ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Data first, code last: a code change then re-pushes only the small last layer. The large
# data files (fingerprint DB, rhythm index) are not in the image; app.py downloads them from S3.
COPY app/db /app/db
COPY app/tools /app/tools
COPY app/poc /app/poc
COPY app/server /app/server
WORKDIR /app

# Lambda's file system is read-only except /tmp.
ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    KF_TMP=/tmp/kf \
    KF_READONLY_DB=1 \
    PANAKO_MAP_GB=2 \
    PANAKO_THREADS=2 \
    RHYTHM_WORKERS=1 \
    AWS_LWA_PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_ASYNC_INIT=true

CMD ["python", "server/app.py"]
