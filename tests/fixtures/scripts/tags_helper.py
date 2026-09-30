#!/usr/bin/env python3
"""Tag-loading fixture; never accesses the real keyring or Pinboard."""

import json
import sys


operation = sys.argv[1]
payload = json.loads(sys.stdin.readline())
if operation == "status":
    response = {"ok": True, "tokenConfigured": True, "username": "alice"}
elif operation == "tags" and not payload.get("fail"):
    response = {"ok": True, "tags": ["ai-memory", "oss", "osint"]}
elif operation in ("tags", "suggest"):
    response = {
        "ok": False,
        "code": "network_error",
        "error": "Unable to reach Pinboard.",
        "retryable": True,
    }
elif operation == "submit":
    response = {"ok": True, "queued": False}
else:
    response = {"ok": False, "error": "Unexpected fixture operation."}
print(json.dumps(response))
