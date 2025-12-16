FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for WeasyPrint and Playwright
RUN apt-get update && apt-get install -y \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project files (uv needs both pyproject.toml and uv.lock)
COPY pyproject.toml uv.lock ./

# Install Python dependencies with uv (much faster than pip!)
RUN uv sync --frozen --no-dev

# Install Playwright browsers
RUN uv run playwright install chromium
RUN uv run playwright install-deps chromium

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p data/cv data/jobs data/generated_cvs

# Expose API port
EXPOSE 8000

# Run the application
CMD ["uv", "run", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
