# Use lightweight Python image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install gunicorn

# Copy project files
COPY . .

# Ensure entrypoints are executable
RUN chmod +x docker/celery-entrypoint.sh docker/web-entrypoint.sh

# Collect static files at build time
RUN python manage.py collectstatic --noinput

# Expose port for Gunicorn
EXPOSE 8000

# Run migrations, then Gunicorn
CMD ["./docker/web-entrypoint.sh"]
