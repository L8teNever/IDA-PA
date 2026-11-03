"""Watchtower pre-update hook (docker-compose label): exit 75 while Stella is in a conversation, so the update waits for
the next round instead of cutting the call. Any other result (also: container not answering) lets the update run."""
import json
import sys
import urllib.request


def main() -> int:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as r:
            busy = bool(json.load(r).get("busy"))
    except Exception:  # noqa: BLE001
        return 0
    if busy:
        print("Stella telefoniert gerade – Update später")
        return 75  # EX_TEMPFAIL: watchtower skips this container for now
    return 0


if __name__ == "__main__":
    sys.exit(main())
