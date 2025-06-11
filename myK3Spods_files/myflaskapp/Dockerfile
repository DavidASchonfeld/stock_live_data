FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "--workers=2", "--bind", "0.0.0.0:5000", "--timeout=120","flask_main:app"]
# --workers=2. 2 workers so Gunicorn distributes the load better handles requests
# --timeout=120. If a worker gets a timeout=120, it restarts the worker instead of crashes
