#!/usr/bin/env python3
"""Isolated helper fixture; never accesses the real keyring."""

import json
import sys
import time


operation = sys.argv[1]
payload = json.loads(sys.stdin.readline())
if operation == "status":
    response = {"ok": True, "tokenConfigured": False, "queue": []}
elif operation == "save-token":
    if payload.get("token") == "alice:hang":
        time.sleep(60)
    response = {
        "ok": False,
        "code": "invalid_token",
        "error": "The Pinboard token must use the username:TOKEN format.",
    }
else:
    response = {"ok": False, "error": "Unexpected fixture operation."}
print(json.dumps(response))
