"""Build a Windows standalone or onefile distribution using the current venv."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("standalone", "onefile"), default="standalone")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if sys.platform != "win32":
        parser.error("此构建脚本面向 Windows x64")
    from echolivetui import __version__
    output = root / "build" / args.mode
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "nuitka", f"--mode={args.mode}", "--msvc=latest", "--assume-yes-for-downloads", "--windows-console-mode=force", f"--jobs={args.jobs}", "--include-package=echolivetui", "--include-package=textual", "--include-package=rich", "--include-package-data=textual", "--include-package-data=jieba", "--include-package-data=pypinyin", "--include-distribution-metadata=textual", "--include-distribution-metadata=rich", "--output-filename=EchoLiveTUI.exe", f"--output-dir={output}", f"--report={output / 'compilation-report.xml'}", f"--product-version={__version__}", f"--file-version={__version__}", "--product-name=EchoLiveTUI", "--file-description=Echo Live terminal broadcaster", str(root / "launcher.py")]
    if args.mode == "onefile":
        # Otherwise Nuitka's parent can kill the child after Ctrl+C even when
        # Python/Textual intentionally keeps it running. Requires Nuitka >=2.8.1.
        command.insert(-1, "--onefile-child-grace-time=infinity")
    subprocess.run(command, cwd=root, check=True)
    executable = output / ("launcher.dist/EchoLiveTUI.exe" if args.mode == "standalone" else "EchoLiveTUI.exe")
    subprocess.run([str(executable), "--self-test"], cwd=output, check=True, timeout=120)
    subprocess.run([sys.executable, str(root / "scripts/check-windows-signals.py"), str(executable)], cwd=root, check=True, timeout=250)
    release = root / "dist"
    release.mkdir(exist_ok=True)
    name = f"EchoLiveTUI-{__version__}-windows-x64-{args.mode}"
    bundle = release / f"{name}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        files = executable.parent.rglob("*") if args.mode == "standalone" else [executable]
        for file in files:
            if file.is_file():
                archive.write(file, f"{name}/{file.relative_to(executable.parent).as_posix()}")
        for filename in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            if (root / filename).is_file():
                archive.write(root / filename, f"{name}/{filename}")
    checksum = hashlib.sha256(bundle.read_bytes()).hexdigest()
    bundle.with_suffix(".zip.sha256").write_text(f"{checksum}  {bundle.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(bundle), "executable": str(executable), "sha256": checksum}, indent=2))


if __name__ == "__main__":
    main()
