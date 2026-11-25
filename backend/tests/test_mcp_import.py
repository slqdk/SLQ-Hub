"""MCP servers from elsewhere (0.86.0): a configuration as server pages give it, read into the Hub's own settings,
and the catalog of servers checked against the Hub's client."""
import os
import subprocess

import pytest

from app.agent import mcp_import as I

TAKEN = [{"name": "MDN", "transport": "http", "url": "https://mcp.mdn.mozilla.net/"},
         {"name": "calc", "transport": "stdio", "command": "npx -y calc-mcp"}]


def one(text: str, taken=None) -> dict:
    got = I.read_config(text, TAKEN if taken is None else taken)
    assert got["error"] is None and len(got["servers"]) == 1, got
    return got["servers"][0]


def brief(f: dict) -> tuple:
    c = f["config"]
    return c["name"], c["transport"], c["url"] or c["command"], c["headers"], c["env"]


KEY_NEED = {"where": "header", "key": "CONTEXT7_API_KEY", "placeholder": "YOUR_API_KEY", "label": "API key",
            "optional": False, "secret": True}
C7 = ("context7", "http", "https://mcp.context7.com/mcp", {"CONTEXT7_API_KEY": "YOUR_API_KEY"}, {})


@pytest.mark.parametrize("text", [
    # Cursor and Claude Desktop
    '{"mcpServers": {"context7": {"url": "https://mcp.context7.com/mcp", "headers": {"CONTEXT7_API_KEY": "YOUR_API_KEY"}}}}',
    # Windsurf
    '{"mcpServers": {"context7": {"serverUrl": "https://mcp.context7.com/mcp", "headers": {"CONTEXT7_API_KEY": "YOUR_API_KEY"}}}}',
    # Gemini CLI, with an Accept header the Hub sets itself
    '{"mcpServers": {"context7": {"httpUrl": "https://mcp.context7.com/mcp", "headers": {"CONTEXT7_API_KEY": '
    '"YOUR_API_KEY", "Accept": "application/json, text/event-stream"}}}}',
    # Codex's TOML
    '[mcp_servers.context7]\nurl = "https://mcp.context7.com/mcp"\nhttp_headers = { "CONTEXT7_API_KEY" = "YOUR_API_KEY" }\n',
    # the command line, and amp's form of it
    'claude mcp add --transport http context7 https://mcp.context7.com/mcp --header "CONTEXT7_API_KEY: YOUR_API_KEY"',
    'amp mcp add context7 --header "CONTEXT7_API_KEY=YOUR_API_KEY" https://mcp.context7.com/mcp',
    # a README's code block, with the prose around it and a comment and a trailing comma in it
    'Add this to your config:\n```jsonc\n{\n  // remote\n  "mcpServers": {\n    "context7": {\n      "url": '
    '"https://mcp.context7.com/mcp",\n      "headers": {"CONTEXT7_API_KEY": "YOUR_API_KEY"},\n    },\n  },\n}\n```\nDone.',
])
def test_a_remote_server_in_any_clients_words(text):
    f = one(text)
    assert brief(f) == C7 and f["needs"] == [KEY_NEED] and not f["problem"] and not f["notes"]


@pytest.mark.parametrize("text", [
    '{"mcpServers": {"context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp", "--api-key", "YOUR_API_KEY"]}}}',
    '{"mcp": {"context7": {"type": "local", "command": ["npx", "-y", "@upstash/context7-mcp", "--api-key", '
    '"YOUR_API_KEY"], "enabled": true}}}',                                                       # Opencode
    'claude mcp add context7 -- npx -y @upstash/context7-mcp --api-key YOUR_API_KEY',
    '```bash\nnpm install -g @upstash/context7-mcp\n```\nthen\n```\nnpx -y @upstash/context7-mcp --api-key YOUR_API_KEY\n```',
])
def test_a_key_in_a_command_line_goes_into_the_environment(text):
    """A key in the command would show in Settings and in the sandbox's process list: the line names a variable,
    and the variable - masked like a header - holds the key."""
    f = one(text)
    assert brief(f) == ("context7", "stdio", 'npx -y @upstash/context7-mcp --api-key "${API_KEY}"', {}, {})
    assert f["needs"] == [{"where": "env", "key": "API_KEY", "placeholder": None, "label": "API key",
                           "optional": False, "secret": True}]


def test_vs_code_with_its_inputs_and_settings_fragment():
    f = one('{"inputs": [{"type": "promptString", "id": "github_token", "description": "GitHub Personal Access Token",'
            ' "password": true}], "servers": {"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/",'
            ' "headers": {"Authorization": "Bearer ${input:github_token}"}}}}')
    assert f["needs"] == [{"where": "header", "key": "Authorization", "placeholder": "${input:github_token}",
                           "label": "GitHub Personal Access Token", "optional": False, "secret": True}]
    f = one('"mcp": {\n  // settings.json\n  "servers": {\n    "docs": {"type": "http", "url": "https://docs.example.com/mcp",},'
            '\n  },\n},')
    assert brief(f) == ("docs", "http", "https://docs.example.com/mcp", {}, {})


def test_zed_and_a_bare_url_and_add_json():
    f = one('{"context_servers": {"time": {"command": {"path": "uvx", "args": ["mcp-server-time"], "env": {}}}}}')
    assert brief(f) == ("time", "stdio", "uvx mcp-server-time", {}, {})
    assert brief(one("https://docs.example.com/mcp")) == ("docs-example", "http", "https://docs.example.com/mcp", {}, {})
    f = one("claude mcp add-json weather '{\"type\":\"http\",\"url\":\"https://weather.example.com/mcp\"}'")
    assert brief(f) == ("weather", "http", "https://weather.example.com/mcp", {}, {})


def test_what_cannot_work_here_is_said():
    f = one('{"mcpServers": {"github": {"command": "docker", "args": ["run", "-i", "--rm", "-e", '
            '"GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"], "env": '
            '{"GITHUB_PERSONAL_ACCESS_TOKEN": "<YOUR_TOKEN>"}}}}')
    assert f["problem"].startswith("It runs in Docker, which the sandbox does not have. Look on its page for a URL")
    f = one('claude mcp add --transport stdio --scope user gitea \\\n  --env GITEA_ACCESS_TOKEN=token \\\n'
            '  --env GITEA_HOST=https://gitea.com \\\n  -- go run gitea.com/gitea/gitea-mcp@latest -t stdio')
    assert f["config"]["name"] == "gitea" and f["problem"].startswith("It is started with Go")
    assert f["config"]["env"] == {"GITEA_ACCESS_TOKEN": "token", "GITEA_HOST": "https://gitea.com"}
    f = one('{"mcpServers": {"forgejo": {"url": "http://localhost:8080/sse"}}}')
    assert f["config"]["url"] == "http://localhost:8080/mcp" and not f["problem"]
    assert [n.split(":")[0] for n in f["notes"]] == ["Its page gives /sse, the old SSE transport, which the Hub does not "
                                                     "speak", "localhost here is the Hub's own container"]
    f = one('{"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem",'
            ' "/Users/me/Desktop"]}}}')
    assert f["config"]["name"] == "filesystem" and "/Users/me/Desktop is a path on another computer" in f["notes"][0]
    assert "The sandbox must have bun" not in str(one("npx -y x-mcp")["notes"])
    assert one('{"mcpServers": {"r": {"command": "ruby", "args": ["s.rb"]}}}')["notes"] == [
        "The sandbox must have ruby: it has Node.js 24 (npx), Python with uv (uvx) and .NET 10."]


def test_a_host_to_fill_in_and_a_bridge_taken_away():
    f = one('{"mcpServers": {"forgejo": {"url": "https://your-forgejo-instance.org/mcp", "headers": '
            '{"Authorization": "token <your personal access token>"}}}}')
    assert [(n["where"], n["placeholder"], n["label"], n["secret"]) for n in f["needs"]] == [
        ("url", "your-forgejo-instance.org", "Forgejo instance (its host name)", False),
        ("header", "<your personal access token>", "Personal access token", True)]
    f = one('{"mcpServers": {"x": {"command": "npx", "args": ["-y", "mcp-remote@latest", "https://example.com/sse", '
            '"--header", "Authorization: Bearer ${AUTH_TOKEN}"], "env": {"AUTH_TOKEN": "<token>"}}}}')
    assert brief(f) == ("x", "http", "https://example.com/mcp", {"Authorization": "Bearer <token>"}, {})
    assert f["notes"][0].startswith("mcp-remote is a bridge") and f["needs"][0]["placeholder"] == "<token>"


def test_names_stay_unique_and_a_server_set_up_already_is_named():
    got = I.read_config('{"mcpServers": {"mdn": {"url": "https://mcp.mdn.mozilla.net"}, "calc": {"command": "npx", '
                        '"args": ["-y", "calc-mcp"]}, "calc ": {"url": "https://calc.example.com/mcp"}}}', TAKEN)
    assert [(f["config"]["name"], f["same_as"]) for f in got["servers"]] == [("mdn 2", "MDN"), ("calc 2", "calc"),
                                                                            ("calc 3", None)]
    assert I.read_config("hello there", [])["error"].startswith("No MCP server in it.")
    assert I.read_config("  ", [])["error"].startswith("Paste the configuration")
    for line in ("npm install -g some-mcp", "uv pip install mcp-server-x", "dotnet tool install -g x-mcp",
                 "pnpm add -g x-mcp"):
        assert I.read_config(line, [])["error"], line                    # installing is not starting it


def test_the_command_line_quotes_what_it_does_not_fill_in(tmp_path):
    """Only the variables the Hub made are expanded: $HOME, backticks and quotes in a page's arguments stay text."""
    f = one('{"mcpServers": {"t": {"command": "npx", "args": ["-y", "pkg", "--api-key", "YOUR_API_KEY", "a b", "$HOME",'
            ' "--x=$HOME`id`\\"<token>"]}}}')
    cmd = f["config"]["command"]
    assert [n["key"] for n in f["needs"]] == ["API_KEY", "TOKEN"]
    script = "set -- " + cmd.split(" ", 1)[1] + '; printf "%s\\n" "$@"'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True,
                         env={**os.environ, "API_KEY": "k1", "TOKEN": "k2", "HOME": str(tmp_path)}).stdout
    assert out.splitlines() == ["-y", "pkg", "--api-key", "k1", "a b", "$HOME", '--x=$HOME`id`"k2']


def test_the_catalog():
    entries = I.catalog(TAKEN)
    assert [e["id"] for e in entries] == ["mdn", "context7", "microsoft-learn", "deepwiki", "huggingface"]
    assert [e["added"] for e in entries] == ["MDN", None, None, None, None]
    for e in entries:
        cfg = e["found"]["config"]
        assert cfg["url"].startswith("https://") and cfg["transport"] == "http" and cfg["name"] == e["name"]
        assert e["checked"] == I.CHECKED and e["about"] and e["docs"].startswith("https://")
        assert all(n["optional"] for n in e["found"]["needs"])           # each works without an account
    hf = entries[-1]["found"]
    assert hf["config"]["headers"] == {"Authorization": "Bearer <token>"} and hf["needs"][0]["placeholder"] == "<token>"


async def test_the_routes(signed_in, app):
    r = await signed_in.post("/hub/api/mcp/read-config", json={"text": C7[2]})
    assert r.status_code == 200 and r.json()["servers"][0]["config"]["url"] == C7[2]
    await signed_in.put("/hub/api/settings", json={"section": "mcp", "values": {"servers": [
        {"name": "Docs", "transport": "http", "url": "https://learn.microsoft.com/api/mcp", "enabled": False,
         "headers": {"X-Key": "s3cret"}}]}})
    cat = (await signed_in.get("/hub/api/mcp/catalog")).json()
    assert {e["id"]: e["added"] for e in cat}["microsoft-learn"] == "Docs" and "s3cret" not in str(cat)
    got = (await signed_in.post("/hub/api/mcp/read-config", json={"text": "https://learn.microsoft.com/api/mcp/"})).json()
    assert got["servers"][0]["same_as"] == "Docs" and "s3cret" not in str(got)
