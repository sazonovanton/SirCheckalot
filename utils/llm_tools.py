from openai import OpenAI
from openai import APIError, APIConnectionError, RateLimitError, AuthenticationError, BadRequestError

import os
import httpx
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

class LLMTools:
    def __init__(self) -> None:        
        proxy_url = os.getenv('HTTP_PROXY')
        self.client = OpenAI(
            base_url=os.getenv('OPENAI_BASE_URL', None),
            api_key=os.getenv('OPENAI_API_KEY', "ollama"),
            http_client=httpx.Client(proxy=proxy_url if proxy_url else None)
        )
        
        # Load default system message
        system_message_path = os.path.join(os.path.dirname(__file__), 'system_message.md')
        try:
            with open(system_message_path, 'r', encoding='utf-8') as file:
                self.default_system_message = file.read()
        except FileNotFoundError:
            print(f"⚠️  System message file not found at {system_message_path}")
            self.default_system_message = "You are a helpful assistant."

    def rewrite(self, text: str, system_message: Optional[str] = None) -> str:
        """Rewrite text with LLM"""
        try:
            if system_message is None:
                system_message = self.default_system_message

            response = self.client.chat.completions.create(
                model=os.getenv('OPENAI_MODEL', "gpt-4.1-nano"),
                temperature=float(os.getenv('OPENAI_TEMPERATURE', 0.5)),
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": text}
                ]
            )
            reply = response.choices[0].message.content
            return reply
        except KeyboardInterrupt:
            print("⚠️  Operation was interrupted by user")
            raise KeyboardInterrupt
        except AuthenticationError as e:
            error_msg = f"❌ API authentication error: {e}"
            print(error_msg)
            print("🔑 Please check your API key in OPENAI_API_KEY environment variable")
            raise Exception(error_msg) from e
        except RateLimitError as e:
            error_msg = f"⏱️  Rate limit exceeded: {e}"
            print(error_msg)
            print("⏳ Please try again after some time")
            raise Exception(error_msg) from e
        except BadRequestError as e:
            error_msg = f"❌ Bad request to API: {e}"
            print(error_msg)
            print("📝 Please check model parameters and message format")
            raise Exception(error_msg) from e
        except APIConnectionError as e:
            error_msg = f"🌐 API connection error: {e}"
            print(error_msg)
            print("🔗 Please check your internet connection and proxy settings")
            raise Exception(error_msg) from e
        except APIError as e:
            error_msg = f"🚨 OpenAI API error (status code {getattr(e, 'status_code', 'unknown')}): {e}"
            print(error_msg)
            if hasattr(e, 'status_code'):
                if e.status_code == 500:
                    print("🔧 Internal server error")
                elif e.status_code == 502:
                    print("🚪 Bad Gateway - server unavailable")
                elif e.status_code == 503:
                    print("⚙️  Service temporarily unavailable")
                elif e.status_code == 504:
                    print("⏰ Request timeout")
                else:
                    print(f"📊 HTTP status: {e.status_code}")
            raise Exception(error_msg) from e
        except Exception as e:
            error_msg = f"💥 Unexpected error: {type(e).__name__}: {e}"
            print(error_msg)
            raise Exception(error_msg) from e
        

if __name__ == "__main__":
    # TEST
    # 0. Create virtual environment with `python3 -m venv venv`
    # 1. Activate virtual environment with `source venv/bin/activate`
    # 2. Install dependencies with `pip install openai dotenv`
    # 3. Set environment variables with `export OPENAI_API_KEY=your_api_key`
    # 4. Run the script with `python3 -m utils.llm_tools.py`
    llm_tools = LLMTools()
    example_output = (
        "user@server:~$ uptime\n"
        "22:24:36 up 5 days,  7:28,  5 users,  load average: 1,11, 0,55, 0,46\n"
    )
    print(llm_tools.rewrite(example_output))