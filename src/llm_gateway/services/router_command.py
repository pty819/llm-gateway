from llm_gateway.db.models import RouterCommandConfig


def render_router_command(config: RouterCommandConfig) -> str:
    if not config.worker_urls:
        raise ValueError("worker_urls must not be empty")
    args = [
        "vllm-router",
        "--worker-urls",
        *config.worker_urls,
        "--policy",
        config.policy.value,
        "--host",
        config.host,
        "--port",
        str(config.port),
    ]
    for key in sorted(config.extra_args):
        value = config.extra_args[key]
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                args.append(flag)
            continue
        if isinstance(value, list):
            args.append(flag)
            args.extend(str(item) for item in value)
            continue
        args.extend([flag, str(value)])
    return " ".join(_shell_quote(arg) for arg in args)


def _shell_quote(value: str) -> str:
    if value and all(ch.isalnum() or ch in "-_./:=," for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"

