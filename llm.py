import os
from dotenv import loadenv
from langchain.chat_models import init_chat_model

loadenv()

llm = init_chat_model(os.getenv("MODEL","openai:gpt-4o-mini"), temperature=0)