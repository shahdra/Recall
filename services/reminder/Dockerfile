FROM python:3.11-slim

WORKDIR /app

# Copy requirements first so pip layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY reminder.py ./

# Run unprivileged. Same UID as the other Python services so the Kubernetes
# securityContext (runAsUser: 10001) is identical across all of them.
RUN useradd --create-home --uid 10001 recall
USER recall

# No EXPOSE and no PORT: this is a batch job, not a server. It runs once, publishes
# at most one SNS message, and exits — the CronJob schedules it.
CMD ["python", "reminder.py"]

