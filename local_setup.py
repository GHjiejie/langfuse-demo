"""Local-only configuration for the work-order demo. Requires only the standard library."""

import getpass
import hashlib
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
STACK = LOCAL / "stack.env"
MODEL_KEY = LOCAL / "wylon.key"
BASE_URL = "http://127.0.0.1:3100"
SOURCE_COMMIT = "cb335bf30a599d88364c5a9910b1e0bd26de3482"
COMPOSE_HASH = "a1abd7f9985171529fc61e473bbf447dfb49f3cc1c49a8573fca0427d86357ee"


def write_private(path, data):
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=LOCAL, delete=False) as tmp:
        tmp.write(data)
        temporary = tmp.name
    os.replace(temporary, path)


def settings():
    if not STACK.exists():
        raise SystemExit("请先运行 ./demo up 初始化本地 Langfuse。")
    return dict(line.split("=", 1) for line in STACK.read_text().splitlines())


def initialize():
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    config = LOCAL / "upstream-compose.yaml"
    if not config.exists() or hashlib.sha256(config.read_bytes()).hexdigest() != COMPOSE_HASH:
        url = f"https://raw.githubusercontent.com/langfuse/langfuse/{SOURCE_COMMIT}/docker-compose.yml"
        with urlopen(url, timeout=40) as response:
            body = response.read()
        if hashlib.sha256(body).hexdigest() != COMPOSE_HASH:
            raise SystemExit("上游 Compose 文件校验失败。")
        write_private(config, body)
    if STACK.exists():
        return
    password = secrets.token_hex(20)
    storage_password = secrets.token_hex(20)
    config = {
        "LANGFUSE_IMAGE_TAG": "4.86.0",
        "POSTGRES_VERSION": "17-alpine",
        "NEXTAUTH_URL": BASE_URL,
        "NEXTAUTH_SECRET": secrets.token_hex(32),
        "SALT": secrets.token_hex(24),
        "ENCRYPTION_KEY": secrets.token_hex(32),
        "POSTGRES_PASSWORD": password,
        "DATABASE_URL": f"postgresql://postgres:{password}@postgres:5432/postgres",
        "CLICKHOUSE_PASSWORD": secrets.token_hex(20),
        "REDIS_AUTH": secrets.token_hex(20),
        "MINIO_ROOT_PASSWORD": storage_password,
        "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY": storage_password,
        "LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY": storage_password,
        "LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY": storage_password,
        "LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT": "http://127.0.0.1:3190",
        "LANGFUSE_INIT_ORG_ID": "workorder-lab",
        "LANGFUSE_INIT_ORG_NAME": "Workorder Lab",
        "LANGFUSE_INIT_PROJECT_ID": "workorder-demo",
        "LANGFUSE_INIT_PROJECT_NAME": "工单知识库",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "pk-lf-" + secrets.token_hex(16),
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "sk-lf-" + secrets.token_hex(24),
        "LANGFUSE_INIT_USER_EMAIL": "demo@example.test",
        "LANGFUSE_INIT_USER_NAME": "Demo",
        "LANGFUSE_INIT_USER_PASSWORD": secrets.token_urlsafe(18),
        "LANGFUSE_DISABLE_SIGNUP_ONBOARDING": "true",
        "LANGFUSE_INGESTION_QUEUE_DELAY_MS": "100",
        "LANGFUSE_INGESTION_CLICKHOUSE_WRITE_INTERVAL_MS": "1000",
        "TELEMETRY_ENABLED": "false",
    }
    # Publish a complete file only once, including when two terminals start the demo.
    with tempfile.NamedTemporaryFile(dir=LOCAL) as file:
        file.write("".join(f"{key}={value}\n" for key, value in config.items()).encode())
        file.flush()
        try:
            os.link(file.name, STACK)
        except FileExistsError:
            pass


if __name__ == "__main__":
    if sys.argv[1] == "init":
        initialize()
    elif sys.argv[1] == "login":
        config = settings()
        print(BASE_URL)
        print("邮箱", config["LANGFUSE_INIT_USER_EMAIL"])
        print("密码", config["LANGFUSE_INIT_USER_PASSWORD"])
    elif sys.argv[1] == "set-key":
        key = getpass.getpass("Wylon API key（输入隐藏） ").strip()
        if not key:
            raise SystemExit("密钥为空。")
        write_private(MODEL_KEY, (key + "\n").encode())
