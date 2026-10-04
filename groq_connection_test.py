"""Send a small test request to Groq using the project's local configuration."""

import sys

from groq import Groq

from config import GROQ_API_KEY, GROQ_MODEL


def main() -> int:
    if not GROQ_API_KEY or GROQ_API_KEY == "replace_with_your_groq_api_key":
        print("Missing GROQ_API_KEY. Copy .env.example to .env and add your key.")
        return 1

    try:
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": "Reply with: Groq connection successful."}],
            temperature=0,
        )
    except Exception as error:
        print(f"Groq request failed ({type(error).__name__}): {error}")
        return 1

    message = response.choices[0].message.content
    print(f"Model: {GROQ_MODEL}")
    print(f"Response: {message or '(empty response)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())