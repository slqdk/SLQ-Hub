#!/usr/bin/env python3
"""The Windows parts of .NET for the sandbox image (stdlib and zstd; run by the image build, as root).

    dotnet_windows.py packs DOTNET_DIR    Windows targeting packs for the Linux SDK in DOTNET_DIR
    dotnet_windows.py runtime DIR         the Windows build of the .NET 10 runtime, with Windows Desktop, for Wine

packs: the Windows Forms/WPF reference pack and the win-x64 app host, which the Linux SDK otherwise fetches from
NuGet for every project once EnableWindowsTargeting is on (so nothing built offline): put where the SDK looks
first, under the version it asks for (its BundledVersions.props). Then a Windows Forms project is built with no
package source at all, to prove nothing is fetched.

runtime: dotnet.exe, hostfxr and the Microsoft.NETCore.App and Microsoft.WindowsDesktop.App frameworks, which
winshot runs Windows programs with.

The files are Microsoft's (MIT), as conda-forge repackages them for win-64 - one pinned source, checked by
SHA-256. A pack from 10.0.12 serves an SDK that asks for 10.0.13: within a major version the reference
assemblies and the app host do not change.
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

CONDA = "https://conda.anaconda.org/conda-forge/win-64/"
PACKAGES = {
    "runtime": ("dotnet-runtime-10.0.12-h57928b3_0.conda",
                "bcf439cbbb01489280a97e4f7807c4ae99e4f4289ce184beca5506d0ebfbd5a5"),
    "desktop": ("dotnet-desktop-10.0.12-h57928b3_0.conda",
                "0986885431453cb4c0a9dd1cddc94f72874a6c83c5a76d6c15c8aa01cd829213"),
    "sdk": ("dotnet-sdk-10.0.401-h57928b3_0.conda",
            "3c61ec4ede8597e95e9fef8ea79b7c5d8c1e167fbfd934dd50ef41f877980a68"),
}
REF_PACK, HOST_PACK = "Microsoft.WindowsDesktop.App.Ref", "Microsoft.NETCore.App.Host.win-x64"


def fetch(key: str, folder: str) -> str:
    """Download a pinned package (three tries) and check it. Returns its path."""
    name, sha = PACKAGES[key]
    path = os.path.join(folder, name)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(CONDA + name, timeout=120) as r, open(path, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            break
        except OSError as exc:
            if attempt == 2:
                raise SystemExit(f"dotnet_windows: could not download {name}: {exc}") from exc
            time.sleep(5 * (attempt + 1))
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != sha:
        raise SystemExit(f"dotnet_windows: {name} has SHA-256 {h.hexdigest()}, not {sha}")
    return path


def unpack(conda: str, dest: str, members: tuple[str, ...]) -> None:
    """Extract members (path prefixes) of a .conda file (a zip around pkg-*.tar.zst) into dest, safely (tarfile's
    data filter), streaming."""
    with zipfile.ZipFile(conda) as z:
        inner = next(n for n in z.namelist() if n.startswith("pkg-") and n.endswith(".tar.zst"))
        z.extract(inner, dest)
    packed = os.path.join(dest, inner)
    found = 0
    with subprocess.Popen(["zstd", "-dc", packed], stdout=subprocess.PIPE) as zstd, \
            tarfile.open(fileobj=zstd.stdout, mode="r|") as tar:
        for m in tar:
            if any(m.name == p or m.name.startswith(p + "/") for p in members):
                tar.extract(m, dest, filter="data")
                found += 1
    os.unlink(packed)
    if zstd.returncode != 0 or not found:
        raise SystemExit(f"dotnet_windows: {', '.join(members)} not unpacked from {os.path.basename(conda)}")


def wanted(dotnet_dir: str) -> dict[str, str]:
    """The versions of the two packs the installed SDK asks for, for net10.0."""
    props = glob.glob(os.path.join(dotnet_dir, "sdk", "*", "Microsoft.NETCoreSdk.BundledVersions.props"))
    if not props:
        raise SystemExit(f"dotnet_windows: no SDK in {dotnet_dir}")
    with open(props[0], encoding="utf-8") as f:
        text = f.read()
    out = {}
    items = ((REF_PACK, r'KnownFrameworkReference\s+Include="Microsoft\.WindowsDesktop\.App"', "TargetingPackVersion"),
             (HOST_PACK, r'KnownAppHostPack\s+Include="Microsoft\.NETCore\.App"', "AppHostPackVersion"))
    for pack, item, attr in items:
        for m in re.finditer(r"<" + item + r"([^>]*)>", text):
            if 'TargetFramework="net10.0"' in m.group(1):
                v = re.search(attr + r'="([^"]+)"', m.group(1))
                if v:
                    out[pack] = v.group(1)
                    break
        if pack not in out:
            raise SystemExit(f"dotnet_windows: the SDK names no {attr} for net10.0")
    return out


PROJECT = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>WinExe</OutputType>
    <TargetFramework>net10.0-windows</TargetFramework>
    <UseWindowsForms>true</UseWindowsForms>
    <EnableWindowsTargeting>true</EnableWindowsTargeting>
  </PropertyGroup>
</Project>
"""
PROGRAM = ("static class P { [System.STAThread] static void Main() => "
           "System.Windows.Forms.Application.Run(new System.Windows.Forms.Form()); }\n")


def prove(dotnet_dir: str) -> None:
    """A Windows Forms program builds - .exe and all - with no package source and an empty package cache."""
    with tempfile.TemporaryDirectory() as tmp:
        proj, empty = os.path.join(tmp, "p"), os.path.join(tmp, "no-packages")
        os.makedirs(proj)
        os.makedirs(empty)
        with open(os.path.join(proj, "p.csproj"), "w") as f:
            f.write(PROJECT)
        with open(os.path.join(proj, "Program.cs"), "w") as f:
            f.write(PROGRAM)
        env = dict(os.environ, HOME=tmp, DOTNET_CLI_HOME=tmp, NUGET_PACKAGES=os.path.join(tmp, "cache"),
                   DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1", MSBUILDDISABLENODEREUSE="1",
                   DOTNET_CLI_USE_MSBUILD_SERVER="0", UseSharedCompilation="false")
        res = subprocess.run([os.path.join(dotnet_dir, "dotnet"), "build", proj, "-nologo",
                              f"-p:RestoreSources={empty}", "-p:DefaultAppHostRuntimeIdentifier=win-x64",
                              "-o", os.path.join(tmp, "out")], env=env, capture_output=True, text=True, check=False)
        exe = os.path.join(tmp, "out", "p.exe")
        if res.returncode != 0 or not os.path.isfile(exe):
            raise SystemExit("dotnet_windows: a Windows Forms build still wants packages:\n" + res.stdout[-3000:])
    print("dotnet_windows: a Windows Forms program builds offline, .exe included")


def packs(dotnet_dir: str) -> None:
    versions = wanted(dotnet_dir)
    with tempfile.TemporaryDirectory() as tmp:
        unpack(fetch("sdk", tmp), tmp, tuple(f"dotnet/packs/{p}" for p in versions))
        for pack, version in versions.items():
            got = sorted(os.listdir(os.path.join(tmp, "dotnet", "packs", pack)))
            dest = os.path.join(dotnet_dir, "packs", pack, version)
            if os.path.exists(dest):
                shutil.rmtree(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(os.path.join(tmp, "dotnet", "packs", pack, got[-1]), dest)
            print(f"dotnet_windows: {pack} {got[-1]} as {version}")
    prove(dotnet_dir)


def runtime(dest: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for key in ("runtime", "desktop"):
            unpack(fetch(key, tmp), tmp, ("dotnet",))
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.move(os.path.join(tmp, "dotnet"), dest)
    for need in ("dotnet.exe", "host/fxr", "shared/Microsoft.NETCore.App", "shared/Microsoft.WindowsDesktop.App"):
        if not os.path.exists(os.path.join(dest, need)):
            raise SystemExit(f"dotnet_windows: {need} missing in {dest}")
    print(f"dotnet_windows: Windows .NET runtime in {dest}: "
          f"{', '.join(sorted(os.listdir(os.path.join(dest, 'shared', 'Microsoft.WindowsDesktop.App'))))}")


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "packs":
        packs(argv[1])
    elif len(argv) == 2 and argv[0] == "runtime":
        runtime(argv[1])
    else:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
