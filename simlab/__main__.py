"""Run the sim lab:  python -m simlab"""
import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("ANANKOS_HOST", "0.0.0.0")
    port = int(os.environ.get("ANANKOS_PORT", "47811"))
    uvicorn.run("simlab.app:app", host=host, port=port, log_level="info")
