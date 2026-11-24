"""Read-only probe of the newly authorized three-transport retry run."""

import json

from retry_transport import snapshot

if __name__ == "__main__":
    print(json.dumps(snapshot(), ensure_ascii=False), flush=True)
