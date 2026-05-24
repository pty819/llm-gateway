import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main():
    import uvicorn

    uvicorn.run("llm_gateway.main:app", host="127.0.0.1", port=18080, reload=True)


if __name__ == "__main__":
    main()
