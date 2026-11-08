# Live check: `switch_acp_effort` proxy

`.pr/acp_effort_proxy_check.py` calls `switch_conversation_acp_effort` with a
real `httpx.AsyncClient`, with `_get_agent_server_context` pointed at a local
agent-server built from software-agent-sdk#6556. The target was a live Claude
Code ACP conversation (`claude-agent-acp@0.63.0`, real subscription login).
Each Anthropic `/v1/messages` body was captured by a local proxy on
`ANTHROPIC_BASE_URL`.

```
$ PYTHONPATH=. .venv/bin/python .pr/acp_effort_proxy_check.py http://127.0.0.1:18320 <key> <conversation> medium
after proxy switch: {'model': 'opus[1m]', 'effort': 'medium'}
unknown level: HTTPException 400        # the agent-server's 400 is passed through

next turn  ->  /v1/messages model=claude-opus-5 output_config.effort=medium (same session, 10 msgs)
```
