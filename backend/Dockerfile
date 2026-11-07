FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app/backend/
COPY config/ /app/config/
ENV TRIAGENT_CONFIG_DIR=/app/config
RUN useradd -m triagent && mkdir -p /app/data && chown -R triagent /app/data
USER triagent
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "triagent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
