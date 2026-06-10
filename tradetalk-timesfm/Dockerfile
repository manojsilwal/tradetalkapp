FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn[standard]

# Real-weights build: docker build --build-arg INSTALL_TIMESFM=1 .
# CPU torch keeps the image GPU-free — 200M params is fast enough on CPU for
# nightly batch forecasting, which keeps Cloud Run cost near free tier.
ARG INSTALL_TIMESFM=0
RUN if [ "$INSTALL_TIMESFM" = "1" ]; then \
        pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install --no-cache-dir timesfm ; \
    fi

COPY app.py model_loader.py ./

ENV PORT=8090
EXPOSE 8090

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8090}"]
