from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CONTENT_DIR = ROOT / "content"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "progress.db"

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8765"))
