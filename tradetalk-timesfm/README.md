# tradetalk-timesfm

Separate deployable that serves TimesFM 2.5 forecasts over HTTP. The TradeTalk FastAPI app calls it via `TIMESFM_SERVICE_URL` (`POST /forecast`, `GET /healthz`, `GET /readyz`, `GET /version`).

Local smoke:

```bash
docker build -t tradetalk-timesfm .
docker run -p 8090:8090 -e TIMESFM_SERVICE_TOKEN=test tradetalk-timesfm
curl -s localhost:8090/healthz
```

Production pins weights via Hugging Face revision env vars on the service image.
