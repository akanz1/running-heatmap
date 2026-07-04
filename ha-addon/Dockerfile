ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

# Install uv for fast python dependency management
RUN apk add --no-cache curl build-base
ADD https://astral.sh/uv/install.sh /install.sh
RUN chmod +x /install.sh && /install.sh && rm /install.sh
ENV PATH="/root/.local/bin:${PATH}"

# We will put the code in /app
WORKDIR /app

# Copy application source
COPY . /app

# Install python dependencies system-wide or let uv sync handle it
RUN uv sync

# The port exposed
EXPOSE 8000

# Copy the run.sh script and make it executable
RUN chmod a+x /app/run.sh

CMD [ "/app/run.sh" ]
