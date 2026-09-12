FROM mcr.microsoft.com/playwright/python:v1.62.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config.py models.py cleaner.py fetcher.py extractor.py main.py ./
RUN mkdir -p /app/output
USER pwuser
CMD ["python", "main.py", "--domains", "postman.com,supabase.com,vapi.ai", "--out", "/app/output/output.json"]
