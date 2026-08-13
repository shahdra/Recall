FROM python:3.11-slim

WORKDIR /app

# Copy requirements first so pip layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sm2.py storage.py app.py ./

# Run unprivileged.
RUN useradd --create-home --uid 10001 recall
USER recall

ENV PORT=9000
EXPOSE 9000

CMD ["python", "app.py"]
