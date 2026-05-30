"""
Non-secret GCP defaults for TradeTalk production (Cloud SQL, region, host).

Secrets (POSTGRES_PASSWORD, API keys) belong in ``.env.gcp`` or the deployment
environment — never commit those values.
"""
from __future__ import annotations

GCP_PROJECT_ID = "tradetalkapp-492904"
CLOUD_SQL_INSTANCE_ID = "tradetalk-postgres"
CLOUD_SQL_REGION = "us-central1"
CLOUD_SQL_CONNECTION_NAME = f"{GCP_PROJECT_ID}:{CLOUD_SQL_REGION}:{CLOUD_SQL_INSTANCE_ID}"

# Cloud SQL public IP (us-central1). Override with POSTGRES_HOST if the instance is recreated.
POSTGRES_HOST = "34.31.98.184"
POSTGRES_PORT = 5432
POSTGRES_DB_NAME = "tradetalk"
POSTGRES_USER = "tradetalk"

# VM allowed to connect (openclaw-gateway). Update if the app host IP changes.
CLOUD_SQL_AUTHORIZED_NETWORK = "34.57.42.63/32"
