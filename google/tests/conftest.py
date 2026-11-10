import os

# Der Server liest seine Einstellungen beim Import
os.environ.update({
    "GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "x", "GOOGLE_REDIRECT_URI": "http://localhost/cb",
    "MCP_AUTH_TOKEN": "m" * 20, "AUTH_TOKEN": "a" * 20, "GOOGLE_TOKEN_FILE_PATH": "/tmp/ida-google-test-token.json",
})
