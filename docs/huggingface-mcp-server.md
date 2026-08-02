# Hugging Face MCP server

`huggingface_mcp_server.py` is a dependency-free MCP server that gives an MCP
client bounded access to Hugging Face Hub metadata and model inference.

## Tools

- `hf_search_models` and `hf_get_model`
- `hf_search_datasets` and `hf_get_dataset`
- `hf_search_spaces` and `hf_get_space`
- `hf_inference`

Hub discovery works without authentication for public repositories. Set an
`HF_TOKEN` to access authorized repositories and to run inference. The token is
read only from the process environment and is never accepted as a tool argument.

## Token setup

Create a fine-grained Hugging Face token with only the access needed for the
models and repositories you intend to use.

```bash
printenv HF_TOKEN >/dev/null
```

Optional configuration:

```bash
export HF_MCP_TIMEOUT_SECONDS=30
export HF_HUB_API='https://huggingface.co/api'
export HF_INFERENCE_API='https://router.huggingface.co/hf-inference/models'
```

## Run locally

```bash
python3 huggingface_mcp_server.py
```

The process uses MCP's stdio transport. Do not print logs to stdout; stdout is
reserved for protocol messages.

## MCP client configuration

Use an absolute path to the repository checkout:

```json
{
  "mcpServers": {
    "hugging-face": {
      "command": "python3",
      "args": ["/absolute/path/to/PJ/huggingface_mcp_server.py"],
      "env": {
        "HF_TOKEN": "${HF_TOKEN}"
      }
    }
  }
}
```

If your client does not interpolate environment variables, launch it from a
shell where `HF_TOKEN` is already set. Do not save a real token in repository
configuration.

## Validation

```bash
python3 -m unittest tests.test_huggingface_mcp_server -v
python3 -m unittest discover tests -v
```

## Security boundaries

- Repository mutation, uploads, deletion, and token management are not exposed.
- Search results are capped at 50 items.
- Serialized inference input is capped at 100,000 characters.
- Network timeouts are capped at 120 seconds.
- Hugging Face HTTP errors are bounded before being returned to the client.
- Inference requires `HF_TOKEN`; public discovery does not.

## Deployment note

This version intentionally uses stdio for local, auditable operation inside PJ.
A remote Streamable HTTP wrapper should add server authentication, HTTPS,
request-size limits, rate limiting, and deployment receipts before production
activation.
