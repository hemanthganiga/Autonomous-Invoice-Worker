"""Shared configuration loaded from the project-root .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()

_database_path = Path(os.getenv("DATABASE_PATH", "database/task_history.db"))
DATABASE_PATH = _database_path if _database_path.is_absolute() else PROJECT_ROOT / _database_path