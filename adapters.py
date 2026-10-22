import os
from typing import AsyncGenerator, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

try:
    from google import genai
except ImportError:
    genai = None

CANDIDATE_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]

class BaseLLMAdapter:
    async def stream_text(self, prompt: str, history: List[Dict[str, Any]] = None) -> AsyncGenerator[str, None]:
        raise NotImplementedError

    def generate_text(self, prompt: str) -> str:
        raise NotImplementedError

class GeminiAdapter(BaseLLMAdapter):
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key or not self.api_key.strip():
            raise ValueError("GEMINI_API_KEY is missing. Please set GEMINI_API_KEY in your .env file.")
        current_genai = genai
        if current_genai is None:
            try:
                from google import genai as imported_genai
                current_genai = imported_genai
            except ImportError:
                raise ImportError("google-genai is not installed in the active Python environment.")
        self.client = current_genai.Client(api_key=self.api_key)

    async def stream_text(self, prompt: str, history: List[Dict[str, Any]] = None) -> AsyncGenerator[str, None]:
        last_error = None
        for model in CANDIDATE_MODELS:
            try:
                response = self.client.models.generate_content_stream(
                    model=model,
                    contents=prompt,
                )
                for chunk in response:
                    if chunk.text:
                        yield chunk.text
                return
            except Exception as e:
                last_error = e
                continue
        raise last_error or RuntimeError("Gemini content generation failed across all available models.")

    def generate_text(self, prompt: str) -> str:
        last_error = None
        for model in CANDIDATE_MODELS:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                if response.text:
                    return response.text
            except Exception as e:
                last_error = e
                continue
        raise last_error or RuntimeError("Gemini generation failed across all candidate models.")

class AnthropicAdapter(BaseLLMAdapter):
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key or not self.api_key.strip():
            raise ValueError("ANTHROPIC_API_KEY is missing. Please set ANTHROPIC_API_KEY in your .env file.")
        import anthropic
        self.client = anthropic.Anthropic(api_key=self.api_key)

    async def stream_text(self, prompt: str, history: List[Dict[str, Any]] = None) -> AsyncGenerator[str, None]:
        with self.client.messages.stream(
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            model="claude-3-5-sonnet-latest",
        ) as stream:
            for text in stream.text_stream:
                yield text

    def generate_text(self, prompt: str) -> str:
        response = self.client.messages.create(
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            model="claude-3-5-sonnet-latest",
        )
        return "".join([b.text for b in response.content if hasattr(b, "text")])

class MockAdapter(BaseLLMAdapter):
    async def stream_text(self, prompt: str, history: List[Dict[str, Any]] = None) -> AsyncGenerator[str, None]:
        import asyncio
        sentence = "I have prepared your itinerary with the selected highlights and updated your shared plan. Please review the details below or let me know if you would like any changes."
        for word in sentence.split():
            yield word + " "
            await asyncio.sleep(0.02)

    def generate_text(self, prompt: str) -> str:
        return ""

def get_llm_adapter() -> BaseLLMAdapter:
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider == "anthropic":
        return AnthropicAdapter()
    if provider == "mock":
        return MockAdapter()
    return GeminiAdapter()
