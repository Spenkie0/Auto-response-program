import argparse

from app.ollama.installer import install_model


def main() -> int:
    """Install the requested Ollama model and return a process status code."""
    parser = argparse.ArgumentParser(description="Install one Ollama model for the scraper pipeline.")
    parser.add_argument("model", help="Ollama model name, e.g. qwen3:4b")
    args = parser.parse_args()

    ok, message = install_model(args.model)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
