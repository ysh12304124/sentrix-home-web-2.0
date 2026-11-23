"""Read the exact three-QA continuation without changing any run or answer."""

import json

from retry_three import snapshot

if __name__ == "__main__":
    print(json.dumps(snapshot(), ensure_ascii=False), flush=True)
