FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY pyproject.toml README.md ./
COPY runway ./runway
RUN pip install --no-cache-dir .

# Runtime data dirs
RUN mkdir -p /app/data /app/uploads

EXPOSE 8080
EXPOSE 5050

# Dashboard + API. Override CMD to run `runway listen ...` instead if desired.
CMD ["runway", "serve", "--host", "0.0.0.0", "--port", "8080"]
