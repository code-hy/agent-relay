FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.141.1" "uvicorn[standard]>=0.52.4" "sqlalchemy>=2.0.52" "psycopg[binary]>=3.3.5" "pydantic-settings>=2.15.0" "httpx>=0.28.1"
COPY main.py database.py storage.py schemas.py errors.py dashboard.py dashboard.html worker.py ./
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
