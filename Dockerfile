FROM python:3.12-slim

RUN useradd --create-home --shell /bin/bash --uid 1000 appuser

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir ".[remote]"

USER appuser

EXPOSE 8080

CMD ["sml-mcp-remote"]
