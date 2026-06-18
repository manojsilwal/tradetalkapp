from backend.main import app
for r in app.routes:
    print(getattr(r, "path", r))
