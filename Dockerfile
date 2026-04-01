FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir ".[remote]"

EXPOSE 8080

CMD ["sml-mcp-remote"]
