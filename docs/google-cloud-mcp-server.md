# Google Cloud MCP server

`google_cloud_mcp_server.py` is a dependency-free, read-only MCP server for
Google Cloud resource discovery. It uses stdio transport and the Google Cloud
Resource Manager and Cloud Run REST APIs.

## Tools

- `gcp_search_projects` searches projects visible to the service account.
- `gcp_get_project` retrieves one project by project ID or project number.
- `gcp_list_cloud_run_services` lists services in one project and location.
- `gcp_get_cloud_run_service` retrieves one Cloud Run service.

Every list request is capped at 100 results. The server intentionally does not
follow pagination tokens, mutate resources, run jobs, deploy revisions, or
accept credentials in tool arguments.

## Authentication

The recommended deployment is a Google Cloud compute environment, such as
Cloud Run, with a dedicated attached service account. The server obtains a
short-lived access token from the Google metadata server. Grant that service
account only the read roles needed for the enabled tools, typically Project
Viewer (`roles/browser`) and Cloud Run Viewer (`roles/run.viewer`), scoped as
narrowly as possible.

For local development, authenticate with the Google Cloud CLI and export its
short-lived token without writing it to a repository file:

```bash
export GOOGLE_CLOUD_ACCESS_TOKEN="$(gcloud auth application-default print-access-token)"
python google_cloud_mcp_server.py
```

Do not use a long-lived service-account key. The token is read only from the
process environment and is never accepted as MCP tool input or included in a
tool result.

## Runtime configuration

Non-secret settings use PJ's typed runtime configuration. The default project
is optional; callers can instead provide `project` to each project-scoped tool.

```bash
export PJ_CONFIG__GOOGLE_CLOUD__PROJECT='"my-project-id"'
export PJ_CONFIG__GOOGLE_CLOUD__LOCATION='"us-central1"'
export PJ_CONFIG__GOOGLE_CLOUD__TIMEOUT_SECONDS=30
```

API base URLs are configurable through the same section for testing, but they
must use HTTPS. The metadata token URL is constrained to
`metadata.google.internal` to prevent credentials from being sent to another
host.

## MCP client configuration

Use an absolute repository path. In a Google Cloud runtime, omit the `env`
entry so the attached service account is used.

```json
{
  "mcpServers": {
    "google-cloud": {
      "command": "python",
      "args": ["/absolute/path/to/PJ/google_cloud_mcp_server.py"],
      "env": {
        "GOOGLE_CLOUD_ACCESS_TOKEN": "${GOOGLE_CLOUD_ACCESS_TOKEN}",
        "PJ_CONFIG__GOOGLE_CLOUD__PROJECT": "\"my-project-id\""
      }
    }
  }
}
```

If the MCP client does not interpolate variables, start it from a shell where
the token is already exported. Never paste a token into checked-in JSON.

PJ's `mcp_servers.json` also retains a disabled remote HTTP template. Use that
template only after placing this stdio implementation behind an authenticated,
TLS-protected Streamable HTTP adapter, then set `GOOGLE_CLOUD_MCP_URL` to the
adapter URL and review approval policy before enabling it.

## Validation

Tests mock all Google provider calls and require neither credentials nor a
network connection:

```bash
python -m unittest tests.test_google_cloud_mcp_server -v
```

## Security boundaries

- Only read methods are exposed.
- Project, location, and service identifiers are validated before URL assembly.
- Access tokens come from the process environment or Google metadata server,
  never MCP arguments.
- API error bodies are truncated to 2,000 characters.
- Timeouts are bounded from 1 to 120 seconds.
- Stdout is reserved for MCP protocol messages; the server does not log prompts,
  arguments, results, request bodies, or authorization headers.
