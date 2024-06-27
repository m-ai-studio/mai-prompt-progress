# Setup

- Set `PROGRESS_HOOK_URL` in `.env` to the controller backend progress hook URL.

# Development

Launch ComfyUI in dev mode (restart on file change):
`watchmedo auto-restart -d . -p '*.py' -R  -- python main.py`
