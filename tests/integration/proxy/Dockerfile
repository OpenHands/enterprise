FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0
RUN pip install --no-cache-dir fastapi==0.136.1 uvicorn==0.53.0 websockets==16.0
WORKDIR /fixture
COPY backend.py .
USER 65534:65534
# Trust forwarding only in this isolated test network; not production guidance.
CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
