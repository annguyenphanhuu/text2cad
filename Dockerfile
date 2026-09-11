FROM python:3.10-slim

# Add GEMINI_API_KEY
ARG GEMINI_API_KEY
ENV GEMINI_API_KEY=${GEMINI_API_KEY}

ARG OPENAI_API_KEY
ENV OPENAI_API_KEY=${OPENAI_API_KEY}

# Install system dependencies with fixes
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       default-libmysqlclient-dev \
       libmagic-dev \
       python3-dev \
       build-essential \
       curl \
       wget \
       gnupg \
       dirmngr \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy and install Python packages
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

RUN pip cache purge

# Copy app code
COPY . .


CMD ["python", "run.py"]
