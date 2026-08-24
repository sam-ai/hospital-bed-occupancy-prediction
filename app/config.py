import os
from dotenv import load_dotenv

load_dotenv()

# Temporal Connection
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "hospital-ai-queue")

# Google Gemini Settings
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_MODEL = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")

# OpenAI-Compatible API Settings (e.g. Baseten, DeepSeek, etc.)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-ai/DeepSeek-V4-Pro-0813")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://inference.baseten.co/v1")

USE_LLM = os.getenv("USE_LLM", "true").lower() == "true"
