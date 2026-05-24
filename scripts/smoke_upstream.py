import json
import os
import time
from pathlib import Path


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


def usage_to_dict(usage):
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    try:
        return dict(usage)
    except TypeError:
        return str(usage)


def main() -> None:
    load_dotenv()

    from litellm import completion

    try:
        import importlib.metadata

        litellm_version = importlib.metadata.version("litellm")
    except Exception:
        litellm_version = "unknown"

    api_base = require_env("LLM_GATEWAY_UPSTREAM_BASE_URL")
    api_key = require_env("LLM_GATEWAY_UPSTREAM_API_KEY")
    litellm_model = os.environ.get("LLM_GATEWAY_LITELLM_MODEL")
    if not litellm_model:
        litellm_model = f"openai/{require_env('LLM_GATEWAY_UPSTREAM_MODEL')}"

    started = time.time()
    response = completion(
        model=litellm_model,
        api_base=api_base,
        api_key=api_key,
        messages=[{"role": "user", "content": "Reply with one short sentence."}],
        max_tokens=64,
        temperature=0,
    )

    choice = response.choices[0]
    message = choice.message
    print(
        json.dumps(
            {
                "ok": True,
                "litellm_version": litellm_version,
                "elapsed_ms": round((time.time() - started) * 1000),
                "model": getattr(response, "model", None),
                "finish_reason": getattr(choice, "finish_reason", None),
                "content_preview": (getattr(message, "content", None) or "")[:240],
                "usage": usage_to_dict(getattr(response, "usage", None)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
