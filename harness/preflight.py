"""Release blockers and high-confidence secret scan; never prints secret values."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"sk-(?:or-v1-|proj-)?[0-9A-Za-z_-]{24,}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def scan_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def stub_functions():
    found = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                body = [s for s in node.body if not isinstance(s,ast.Expr) or not isinstance(s.value,ast.Constant)]
                if len(body) == 1 and isinstance(body[0],ast.Raise) and "NotImplementedError" in ast.unparse(body[0]):
                    found.append(f"{path.relative_to(ROOT).as_posix()}:{node.name}")
    return found


def audit(history=False):
    command = ["git","ls-files","--cached","--others","--exclude-standard","-z"]
    proc = subprocess.run(command,cwd=ROOT,capture_output=True,check=True,timeout=30)
    findings = []
    for filename in set(proc.stdout.decode("utf-8").split("\0")) - {""}:
        path = ROOT / filename
        if path.suffix.lower() in {".pdf", ".png", ".mp4"} or not path.is_file(): continue
        if scan_text(path.read_text(encoding="utf-8",errors="replace")):
            findings.append({"location":filename,"reason":"possible credential/private key; inspect locally"})
    if history:
        history_result = subprocess.run(["git","log","--all","-p","--format=commit %h"],cwd=ROOT,capture_output=True,check=True,timeout=60)
        if scan_text(history_result.stdout.decode("utf-8",errors="replace")):
            findings.append({"location":"git history","reason":"possible credential pattern; inspect locally"})
    tracked = subprocess.run(["git","ls-files","-z"],cwd=ROOT,capture_output=True,check=True,timeout=30).stdout.decode().split("\0")
    for name in tracked:
        if Path(name).name == ".env" or name.endswith((".key", ".pem")):
            findings.append({"location":name,"reason":"tracked secret-bearing file"})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file",type=Path,default=ROOT / ".env")
    parser.add_argument("--history",action="store_true")
    parser.add_argument("--manifest",type=Path,default=ROOT / "deploy/submission.json")
    args = parser.parse_args()
    load_dotenv(args.env_file,override=False)
    blockers = []
    stubs = stub_functions()
    if stubs: blockers.append("production modules still contain unimplemented functions")
    for key in ("LLM_API_KEY","LLM_FALLBACK_API_KEY"):
        if not os.getenv(key): blockers.append(f"{key} is not configured")
    if os.getenv("ALLOW_NO_LLM","").lower() in {"1","true","yes","on"}:
        blockers.append("ALLOW_NO_LLM is enabled")
    findings = audit(args.history)
    if findings: blockers.append("secret audit needs review")
    required = ("public_base_url","repository_url","image_reference","video_url","video_duration_seconds","verified_commit")
    if not args.manifest.exists(): blockers.append("submission manifest is missing")
    else:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        blockers.extend(f"submission field missing: {name}" for name in required if not manifest.get(name))
        if (manifest.get("video_duration_seconds") or 0) > 180: blockers.append("video exceeds 180 seconds")
        image = manifest.get("image_reference","")
        if image and "@sha256:" not in image: blockers.append("record the verified immutable image digest")
    print(json.dumps({"ready_for_external_verification":not blockers,"blockers":blockers,
                      "unimplemented":stubs,"secret_findings":findings,
                      "scope":"pattern scan is not proof of no secrets; inspect runtime logs and image separately"},indent=2))
    return int(bool(blockers))


if __name__ == "__main__": raise SystemExit(main())
