import os
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()

llm = init_chat_model(
    os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    model_provider="google_genai",
    api_key=os.getenv("GEMINI_API_KEY"),
    temperature=0,
)
