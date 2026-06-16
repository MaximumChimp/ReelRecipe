FROM python:3.10-slim

# Install system dependencies required by OpenCV and audio processors
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY . .

# Run the API on port 7860 (Hugging Face's default incoming port assignment)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]