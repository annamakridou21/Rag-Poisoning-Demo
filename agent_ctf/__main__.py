"""Run the local browser demo with ``python -m agent_ctf``."""

import argparse

from .ui import run_ui


def main() -> int:
    parser = argparse.ArgumentParser(description="Local RAG poisoning demo")
    parser.add_argument("--model", default="qwen2.5:14b", help="Installed Ollama model")
    parser.add_argument("--port", type=int, default=8765, help="Local browser port")
    args = parser.parse_args()
    return run_ui(args.model, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
