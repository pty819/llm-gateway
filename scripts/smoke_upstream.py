import json
import os
import time
from pathlib import Path

import httpx2 as httpx


def load_dotenv(path: str = ".env.local") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    load_dotenv()

    api_base = require_env("LLM_GATEWAY_UPSTREAM_BASE_URL")
    api_key = require_env("LLM_GATEWAY_UPSTREAM_API_KEY")
    model = require_env("LLM_GATEWAY_UPSTREAM_MODEL")

    url = api_base.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with one short sentence."}],
        "max_tokens": 64,
        "temperature": 0,
    }

    started = time.time()
    response = httpx.post(url, json=body, headers=headers, timeout=30.0)
    response.raise_for_status()
    data = response.json()

    choice = data["choices"][0]
    message = choice["message"]
    print(
        json.dumps(
            {
                "ok": True,
                "elapsed_ms": round((time.time() - started) * 1000),
                "model": data.get("model"),
                "finish_reason": choice.get("finish_reason"),
                "content_preview": (message.get("content") or "")[:240],
                "usage": data.get("usage"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
