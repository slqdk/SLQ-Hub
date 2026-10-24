"""C# in the sandbox (0.15.1): the .NET 10 SDK in the image, build output kept out of the working folder (sync and
the files card would pick up bin/ and obj/), and what the assistant is told. 0.16.0: the Windows packs in the image
(Windows projects build offline), Wine for windows_app (test_windows.py)."""
from __future__ import annotations

from pathlib import Path

from .test_agent import finish, new_chat, say
from .test_people import add_person, client_from, sign_in, turn, user_msg

ROOT = Path(__file__).resolve().parents[2]


def test_the_sandbox_image_has_the_sdk_and_no_build_servers():
    docker = (ROOT / "services" / "sandbox" / "Dockerfile").read_text()
    layer = next(r for r in docker.split("\nRUN ") if "dotnet-sdk-10.0" in r)
    assert "apt-get install -y --no-install-recommends dotnet-sdk-10.0" in layer   # not the 60 MB AOT compiler
    assert docker.index("dotnet-sdk-10.0") > docker.index("pip install")         # its own layer: the others stay cached
    env = docker.split("ENV DOTNET_CLI_TELEMETRY_OPTOUT", 1)[1].split("\n\n", 1)[0]
    for setting in ("DOTNET_NOLOGO=1", "MSBUILDDISABLENODEREUSE=1", "DOTNET_CLI_USE_MSBUILD_SERVER=0",
                    "UseSharedCompilation=false"):
        assert setting in env, setting


def test_windows_projects_build_offline_and_run_under_wine():
    """The Windows packs sit beside the SDK's own before EnableWindowsTargeting goes on for every project (without
    them each restore would fetch one from NuGet); Wine, the Windows runtime and the prefix for windows_app."""
    docker = (ROOT / "services" / "sandbox" / "Dockerfile").read_text()
    packs = docker.index("dotnet_windows.py packs /usr/lib/dotnet")
    assert docker.index("dotnet-sdk-10.0") < packs < docker.index("ENV EnableWindowsTargeting=true")
    assert "dotnet_windows.py runtime /opt/dotnet-win" in docker
    wine = next(r for r in docker.split("\nRUN ") if "wine64" in r)
    for pkg in ("wine64", "libwine", "fonts-wine", "xvfb", "xdotool", "x11-utils", "zstd"):
        assert pkg in wine.split(), pkg
    assert docker.index("wine64") < packs                      # zstd unpacks the packages
    assert "runuser -u sandbox -- /usr/bin/python3 -I /opt/execd/winshot.py --setup /opt/hub-wine" in docker
    assert docker.index("--setup /opt/hub-wine") < docker.index("USER sandbox")


async def test_builds_go_to_hub_dotnet(signed_in, app, fakes):
    fakes.llama["replies"] = [{"tool_calls": [{"name": "bash", "arguments": {"command": "dotnet build"}}]},
                              {"content": "Bygget."}, {"content": "Build"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Byg projektet"))
    assert run.status == "done"
    ex = fakes.sandbox["execs"][-1]
    assert ex["env"]["ArtifactsPath"] == f"/workspace/chats/{c['id']}/.hub/dotnet"
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert ".NET 10 (C#)" in system and "build output goes to .hub/dotnet" in system
    assert "dotnet publish -c Release -p:DefaultAppHostRuntimeIdentifier=win-x64 -o publish" in system
    assert "Target net10.0 (net10.0-windows for Windows Forms and WPF)" in system and "cannot run here" in system
    tools = {t["function"]["name"]: t["function"] for t in fakes.llama["requests"][0]["tools"]}
    assert "the .NET 10 SDK (C#)" in tools["bash"]["description"]


async def test_a_persons_build_stays_in_her_turn(signed_in, app, fakes):
    """Her build output goes to .hub/dotnet inside her turn's folder (writable for her confined command, and never
    sent back to her device); without code she is told nothing about C#."""
    person = await add_person(signed_in, may_run_code=True)
    fakes.llama["replies"] = [{"tool_calls": [{"name": "bash", "arguments": {"command": "dotnet run"}}]},
                              {"content": "Kørt."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "C#", "messages": [user_msg("Kør programmet")]})
        assert res["status"] == "done"
        ex = fakes.sandbox["execs"][-1]
        assert ex["env"]["ArtifactsPath"] == ex["confine"]["root"] + "/.hub/dotnet"
        assert not [f for f in res.get("files") or [] if ".hub/" in (f.get("path") or "")]
    fakes.llama["requests"].clear()
    r = await signed_in.patch(f"/hub/api/people/{person['id']}", json={"may_run_code": False},
                              headers={"Origin": "http://hub"})
    assert r.status_code == 200, r.text
    fakes.llama["replies"] = [{"content": "Ingen kode."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        await turn(app, phone, {"title": "C#", "messages": [user_msg("Hej")]})
    assert ".NET" not in fakes.llama["requests"][0]["messages"][0]["content"]
