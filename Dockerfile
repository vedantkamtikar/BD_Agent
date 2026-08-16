FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose web server port
EXPOSE 8000

# Launch server
CMD ["python", "run_web.py"]
