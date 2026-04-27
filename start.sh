#!/bin/bash
set -e

# Ensure data directories exist (uses Render Disk at /data if mounted)
mkdir -p "${DATA_ROOT:-data}"
mkdir -p "${OUTPUT_ROOT:-output}"

# Start Streamlit on the port Render provides
exec streamlit run app.py \
  --server.port "${PORT:-8501}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false
