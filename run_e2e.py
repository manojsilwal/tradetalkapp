import subprocess
import time

backend = subprocess.Popen(["uvicorn", "backend.main:app", "--port", "8000"])
frontend = subprocess.Popen(["npm", "run", "dev", "--", "--port", "5173"], cwd="frontend")
time.sleep(10)
try:
    subprocess.run(["npm", "run", "e2e:smoke"], check=True)
except Exception as e:
    print(e)
finally:
    backend.terminate()
    frontend.terminate()
