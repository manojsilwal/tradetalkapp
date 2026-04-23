import subprocess
import time

backend = subprocess.Popen(["uvicorn", "backend.main:app", "--port", "8000"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
