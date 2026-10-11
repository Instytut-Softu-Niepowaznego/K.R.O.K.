import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
  base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
  api_key=os.getenv("LLM_API_KEY", "local"),
)

MODEL = os.getenv("LLM_MODEL", "qwen3:8b")