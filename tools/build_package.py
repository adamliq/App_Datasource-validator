#!/usr/bin/env python3
"""
Build the Splunkbase-ready .spl package for Data Source Validator.

Pipeline: clean -> structural/conf/XML/JSON lint + full unit test suite
(delegates to tools/validate_phase1.py) -> secret scan -> dependency
scan -> stage only the files that should ship -> gzip tar as
<name>-<version>.spl -> SHA-256 -> build report.

This does NOT run AppInspect itself - per HANDOFF.md, that has to be run
for real (splunk-appinspect inspect <pkg> --mode precert, or the
AppInspect API / Splunkbase submission flow) against the .spl this
produces; no quality gate here should be read as "AppInspect passed."

Usage:
    python3 tools/build_package.py
Exit code 0 on success, 1 if any stage fails (nothing is packaged).
"""
import datetime
import hashlib
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "datasource_validator"
DIST_DIR = APP_ROOT.parent / "dist"

# Directories/files that exist in the working tree for development but
# must never ship inside the .spl.
EXCLUDE_DIR_NAMES = {"tests", "tools", "__pycache__", ".git", ".pytest_cache"}
EXCLUDE_FILE_NAMES = {"HANDOFF.md"}
EXCLUDE_FILE_SUFFIXES = {".pyc", ".pyo"}

REPORT = {"stages": [], "ok": True}


def stage(name):
    def decorator(fn):
        def wrapped(*args, **kwargs):
            print(f"==> {name}")
            result = fn(*args, **kwargs)
            passed = result is not False
            REPORT["stages"].append({"name": name, "passed": passed})
            if not passed:
                REPORT["ok"] = False
            return result
        return wrapped
    return decorator


@stage("clean")
def clean():
    removed = 0
    for path in APP_ROOT.rglob("__pycache__"):
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    for path in APP_ROOT.rglob("*.py[co]"):
        path.unlink(missing_ok=True)
        removed += 1
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)
    print(f"    removed {removed} cached artifact(s), reset {DIST_DIR}")
    return True


@stage("conf/XML/JSON lint + unit tests (tools/validate_phase1.py)")
def run_validator():
    sys.path.insert(0, str(APP_ROOT / "tools"))
    import validate_phase1  # noqa: E402 - deliberately imported after sys.path setup

    validate_phase1.FAILURES.clear()
    validate_phase1.WARNINGS.clear()
    validate_phase1.PASSES.clear()
    code = validate_phase1.main()
    REPORT["validate_phase1"] = {
        "passes": len(validate_phase1.PASSES),
        "warnings": list(validate_phase1.WARNINGS),
        "failures": list(validate_phase1.FAILURES),
    }
    return code == 0


SECRET_PATTERNS = [
    ("PEM private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |)?PRIVATE KEY-----")),
    ("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("hardcoded credential assignment", re.compile(
        r'(?i)\b(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*["\'][A-Za-z0-9+/=_\-]{8,}["\']'
    )),
]


@stage("secret scan")
def secret_scan():
    hits = []
    for path in _shippable_files():
        if path.suffix not in (".py", ".conf", ".xml", ".js", ".json", ".css", ".md", ""):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                hits.append(f"{path.relative_to(APP_ROOT)}: {label}: {match.group(0)[:60]!r}")

    if hits:
        print("    secret-shaped string(s) found:")
        for h in hits:
            print(f"      {h}")
        REPORT["secret_scan_hits"] = hits
        return False
    print("    no secret-shaped strings found in shippable files")
    return True


STDLIB_ALLOWED = {
    "sys", "os", "json", "time", "hashlib", "uuid", "datetime", "socket",
    "threading", "logging", "re", "dataclasses", "typing", "pathlib",
    "collections", "xml", "traceback", "io",
}
LOCAL_MODULE_PREFIXES = {"app"}


@stage("dependency scan")
def dependency_scan():
    import ast

    local_top_level_modules = {
        p.stem for p in (APP_ROOT / "bin").glob("*.py")
    }

    unexpected = []
    for path in (APP_ROOT / "bin").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if (
                    name in STDLIB_ALLOWED
                    or name in LOCAL_MODULE_PREFIXES
                    or name in local_top_level_modules
                    or name == "splunk"
                ):
                    continue
                unexpected.append(f"{path.relative_to(APP_ROOT)}: imports {name!r}")

    if unexpected:
        print("    unexpected (non-stdlib, non-platform) dependencies found:")
        for u in sorted(set(unexpected)):
            print(f"      {u}")
        REPORT["dependency_scan_hits"] = sorted(set(unexpected))
        return False
    print("    zero third-party dependencies - nothing to vendor, nothing to scan further")
    return True


def _shippable_files():
    for path in APP_ROOT.rglob("*"):
        if path.is_dir():
            continue
        rel_parts = path.relative_to(APP_ROOT).parts
        if any(part in EXCLUDE_DIR_NAMES for part in rel_parts):
            continue
        # AppInspect rejects any file or directory whose name starts with
        # "." (dotfiles, __MACOSX, etc.) anywhere in the shipped app.
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.name in EXCLUDE_FILE_NAMES:
            continue
        if path.suffix in EXCLUDE_FILE_SUFFIXES:
            continue
        yield path


@stage("stage + package .spl")
def package():
    manifest = json.loads((APP_ROOT / "app.manifest").read_text(encoding="utf-8"))
    version = manifest["info"]["id"]["version"]
    spl_name = f"{APP_NAME}-{version}.spl"
    spl_path = DIST_DIR / spl_name

    files = sorted(_shippable_files())
    print(f"    staging {len(files)} file(s) into {spl_name}")

    with tarfile.open(spl_path, "w:gz") as tar:
        for path in files:
            arcname = str(Path(APP_NAME) / path.relative_to(APP_ROOT))
            tar.add(path, arcname=arcname, recursive=False)

    digest = hashlib.sha256(spl_path.read_bytes()).hexdigest()
    (DIST_DIR / f"{spl_name}.sha256").write_text(f"{digest}  {spl_name}\n", encoding="utf-8")

    REPORT["package"] = {
        "spl_path": str(spl_path),
        "sha256": digest,
        "file_count": len(files),
        "size_bytes": spl_path.stat().st_size,
        "version": version,
    }
    print(f"    wrote {spl_path} ({spl_path.stat().st_size} bytes)")
    print(f"    sha256: {digest}")
    return True


def write_report():
    REPORT["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report_path = DIST_DIR / "build_report.json"
    report_path.write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")
    print(f"\nbuild report: {report_path}")


def main():
    clean()
    if REPORT["ok"]:
        run_validator()
    if REPORT["ok"]:
        secret_scan()
    if REPORT["ok"]:
        dependency_scan()
    if REPORT["ok"]:
        package()

    write_report()

    if REPORT["ok"]:
        print("\nbuild: PASSED")
        return 0
    print("\nbuild: FAILED - no .spl was produced")
    return 1


if __name__ == "__main__":
    sys.exit(main())
