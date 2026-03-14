FROM python:3.11-slim

RUN apt-get update && apt-get install -y gcc g++ sqlite3 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy package into a backend sub-directory so relative imports work
COPY . ./backend

EXPOSE 8080
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
