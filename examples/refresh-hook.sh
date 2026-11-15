#!/bin/sh

curl -fsS \
    -X POST \
    -H "Authorization: Bearer $GIT_HOOK_SECRET" \
    -H "X-Client-Type: system" \
    http://127.0.0.1:8080/internal/kaboom
