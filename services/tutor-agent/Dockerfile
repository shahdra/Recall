FROM python:3.11-slim

WORKDIR /app

# Copy requirements first so the pip layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent_loop.py card_generator.py grader.py ingest.py llm.py llm_json.py \
     metrics.py prompts.py voice.py app.py ./

# Run unprivileged.
RUN useradd --create-home --uid 10001 recall
USER recall

ENV PORT=8000
EXPOSE 8000

# app.py's __main__ block runs uvicorn with the FastAPI lifespan, which is what
# builds the model, discovers MCP tools, and sets up the voice client. Invoking
# uvicorn directly would work too, but this keeps one startup path.
CMD ["python", "app.py"]
