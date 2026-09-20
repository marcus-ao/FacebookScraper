"""Decode JSON documents from an already received Meta response body."""
import json


def response_documents(raw):
    text = raw.decode('utf-8').removeprefix('for (;;);').strip()
    try:
        return [json.loads(text)]
    except ValueError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
