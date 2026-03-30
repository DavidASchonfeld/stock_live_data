# Use official slim Python 3.12 image as base — slim reduces image size vs full python image
FROM python:3.12-slim

# Set working directory inside container; all subsequent paths are relative to /app
WORKDIR /app

# Copy requirements first (before app code) so Docker can cache this layer and skip reinstall when only app code changes
COPY requirements.txt .

# Install Python dependencies; --no-cache-dir avoids storing pip cache in the image, keeping image size smaller
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app source code into /app
COPY . .

# Declare that the container listens on port 5000 (documents intent; does not actually publish the port — that's done at runtime)
EXPOSE 5000

# app:app matches renamed flask entrypoint app.py
CMD ["gunicorn", "--workers=2", "--bind", "0.0.0.0:5000", "--timeout=120","app:app"]
# --workers=2. 2 workers so Gunicorn distributes the load better handles requests
# --timeout=120. If a worker gets a timeout=120, it restarts the worker instead of crashes
