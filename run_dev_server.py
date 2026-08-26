"""Server pengembangan. Jangan dipakai di produksi tanpa reverse proxy TLS."""
import os
from server import serve

if __name__ == "__main__":
    serve(int(os.environ.get("PORT", 8000)), os.environ.get("STOCKS_DB"))
