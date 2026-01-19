FROM python:3.14-bookworm

# Install OpenTofu
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    unzip \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install OpenTofu binary
RUN OPENTOFU_VERSION="1.9.0" && \
    ARCH="amd64" && \
    curl -LO "https://github.com/opentofu/opentofu/releases/download/v${OPENTOFU_VERSION}/tofu_${OPENTOFU_VERSION}_linux_${ARCH}.zip" && \
    unzip "tofu_${OPENTOFU_VERSION}_linux_${ARCH}.zip" && \
    mv tofu /usr/local/bin/tofu && \
    chmod +x /usr/local/bin/tofu && \
    rm "tofu_${OPENTOFU_VERSION}_linux_${ARCH}.zip"

# Set working directory
WORKDIR /action

# Copy source code
COPY src/ /action/src/
COPY pyproject.toml uv.lock* README.md /action/

# Install Python dependencies using uv
RUN pip install --upgrade pip && \
    pip install uv && \
    uv pip install --system . && \
    pip uninstall -y uv

CMD [ "python3", "/action/src/infra_visualiser_action/cli" ]
