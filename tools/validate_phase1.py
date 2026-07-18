#!/usr/bin/env python3
"""
Offline structural validator for the Data Source Validator Splunk app.

Checks Phase 1 packaging artifacts (app.manifest, default/*.conf,
metadata/default.meta) for internal consistency: no duplicate stanzas or
keys, cross-references between files agree (capabilities, roles,
collections, versions), and required files exist. Also covers Phase 2:
every shipped bin/ module must parse as valid Python 3, must not import
or call anything that would shell out or execute OS processes (not
permitted on Splunk Cloud), and the full mocked unit test suite under
tests/unit must pass. Runs with no network access and no Splunk
installation - pure text/JSON/AST parsing plus in-process unittest.

Usage:
    python3 tools/validate_phase1.py
Exit code 0 on success, 1 if any check fails.
"""
import ast
import io
import json
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

FAILURES = []
WARNINGS = []
PASSES = []


def ok(msg):
    PASSES.append(msg)


def fail(msg):
    FAILURES.append(msg)


def warn(msg):
    WARNINGS.append(msg)


# ---------------------------------------------------------------------------
# Minimal Splunk .conf parser
# ---------------------------------------------------------------------------

class ConfParseError(Exception):
    pass


def parse_conf(path):
    """
    Parse a Splunk .conf file into an ordered dict of
    {stanza_name: {key: value}}.

    Raises ConfParseError on duplicate stanza names or duplicate keys
    within a stanza (both are silently-wrong states in real Splunk conf
    layering and worth catching in a single file before it ships).
    """
    stanzas = {}
    stanza_order = []
    current = None
    pending_key = None
    pending_val = None

    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    lineno = 0
    n = len(raw_lines)
    while lineno < n:
        line = raw_lines[lineno]
        lineno += 1
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1]
            if name in stanzas:
                raise ConfParseError(
                    f"{path.name}:{lineno}: duplicate stanza [{name}]"
                )
            stanzas[name] = {}
            stanza_order.append(name)
            current = name
            continue

        if "=" not in stripped:
            raise ConfParseError(
                f"{path.name}:{lineno}: line is not a stanza header or "
                f"key=value pair: {stripped!r}"
            )

        key, _, val = stripped.partition("=")
        key = key.strip()
        val = val.strip()

        # Splunk conf line continuation: trailing backslash joins the next
        # physical line onto this value.
        while val.endswith("\\") and lineno < n:
            val = val[:-1] + raw_lines[lineno].strip()
            lineno += 1

        if current is None:
            raise ConfParseError(
                f"{path.name}:{lineno}: key={key!r} appears before any stanza"
            )
        if key in stanzas[current]:
            raise ConfParseError(
                f"{path.name}:{lineno}: duplicate key {key!r} in "
                f"stanza [{current}]"
            )
        stanzas[current][key] = val

    return stanzas, stanza_order


def load_conf(rel_path):
    path = APP_ROOT / rel_path
    if not path.is_file():
        fail(f"missing required file: {rel_path}")
        return None, []
    try:
        stanzas, order = parse_conf(path)
    except ConfParseError as e:
        fail(str(e))
        return None, []
    ok(f"{rel_path}: parsed, {len(stanzas)} stanza(s), no duplicates")
    return stanzas, order


# ---------------------------------------------------------------------------
# Required files
# ---------------------------------------------------------------------------

REQUIRED_FILES = [
    "app.manifest",
    "README.md",
    "README/PRIVACY.md",
    "README/RELEASE_NOTES.md",
    "LICENSE",
    "default/app.conf",
    "default/authorize.conf",
    "default/collections.conf",
    "default/restmap.conf",
    "default/web.conf",
    "default/inputs.conf",
    "default/props.conf",
    "default/transforms.conf",
    "default/savedsearches.conf",
    "metadata/default.meta",
    "default/data/ui/nav/default.xml",
    "default/data/ui/views/setup.xml",
    "default/data/ui/views/home.xml",
    "default/data/ui/views/health.xml",
    "appserver/static/app.js",
    "appserver/static/app.css",
    "bin/dsv_validation_worker.py",
    "bin/search_executor.py",
    "bin/validation_controller.py",
    "bin/rest_config.py",
    "bin/rest_validation.py",
    "bin/rest_query.py",
    "bin/rest_health.py",
    "bin/app/splunk_client.py",
    "bin/app/rest_base.py",
]


def check_required_files():
    for rel in REQUIRED_FILES:
        p = APP_ROOT / rel
        if p.is_file():
            ok(f"required file present: {rel}")
        else:
            fail(f"required file missing: {rel}")


DISALLOWED_PHASE1_FILES = [
    "default/commands.conf",
]


def check_no_placeholder_files():
    for rel in DISALLOWED_PHASE1_FILES:
        p = APP_ROOT / rel
        if p.is_file():
            warn(
                f"{rel} exists - HANDOFF.md deliberately omits this in "
                f"Phase 1 (no empty/placeholder files); confirm it has real "
                f"content before keeping it"
            )


# ---------------------------------------------------------------------------
# app.manifest
# ---------------------------------------------------------------------------

def check_manifest():
    path = APP_ROOT / "app.manifest"
    if not path.is_file():
        fail("app.manifest missing")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"app.manifest: invalid JSON: {e}")
        return None
    ok("app.manifest: valid JSON")

    if data.get("schemaVersion") != "2.0.0":
        fail(f"app.manifest: schemaVersion must be 2.0.0, got "
             f"{data.get('schemaVersion')!r}")
    else:
        ok("app.manifest: schemaVersion == 2.0.0")

    info = data.get("info", {})
    app_id = info.get("id", {})
    if app_id.get("name") != "datasource_validator":
        fail(f"app.manifest: info.id.name must be 'datasource_validator', "
             f"got {app_id.get('name')!r}")
    else:
        ok("app.manifest: info.id.name == datasource_validator")

    version = app_id.get("version")
    if not version:
        fail("app.manifest: info.id.version is required")
    else:
        ok(f"app.manifest: info.id.version == {version}")

    lic = info.get("license", {})
    if "apache" not in (lic.get("name") or "").lower():
        fail("app.manifest: info.license.name should reference Apache "
             "License 2.0 per HANDOFF's locked license decision")
    else:
        ok("app.manifest: license is Apache 2.0")

    splunk_req = (data.get("platformRequirements") or {}).get("splunk", {})
    ent = splunk_req.get("Enterprise")
    if not ent:
        fail("app.manifest: platformRequirements.splunk.Enterprise is required")
    elif not _version_at_least(ent, "9.3.0"):
        fail(f"app.manifest: platformRequirements.splunk.Enterprise "
             f"{ent!r} must be >= 9.3.0")
    else:
        ok(f"app.manifest: min Splunk Enterprise version {ent} >= 9.3.0")

    if "Cloud" not in splunk_req:
        fail("app.manifest: platformRequirements.splunk.Cloud is required "
             "(Splunkbase/Cloud vetting target)")
    else:
        ok("app.manifest: declares Cloud platform support")

    deployments = data.get("supportedDeployments") or []
    forbidden = {"_indexer_clustering", "_forwarder"}
    bad = forbidden.intersection(deployments)
    if bad:
        fail(f"app.manifest: supportedDeployments includes non-SH-only "
             f"targets: {sorted(bad)}")
    else:
        ok("app.manifest: supportedDeployments is SH-only")

    return version


def _version_at_least(version_str, minimum_str):
    def parts(s):
        return tuple(int(x) for x in s.split(".") if x.isdigit())
    try:
        return parts(version_str) >= parts(minimum_str)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# default/app.conf
# ---------------------------------------------------------------------------

def check_app_conf(manifest_version):
    stanzas, _ = load_conf("default/app.conf")
    if stanzas is None:
        return

    install = stanzas.get("install", {})
    if install.get("is_configured") != "false":
        fail("default/app.conf: [install] is_configured must be false "
             "until setup completes")
    else:
        ok("default/app.conf: is_configured = false")

    ui = stanzas.get("ui", {})
    if ui.get("setup_view") != "setup":
        fail("default/app.conf: [ui] setup_view must be 'setup'")
    else:
        ok("default/app.conf: setup_view = setup")

    package = stanzas.get("package", {})
    if package.get("id") != "datasource_validator":
        fail("default/app.conf: [package] id must be 'datasource_validator'")
    else:
        ok("default/app.conf: package id matches manifest name")

    if package.get("check_for_updates") not in ("true", "1"):
        fail("default/app.conf: [package] check_for_updates should be "
             "enabled per locked decisions (\"updates on\")")
    else:
        ok("default/app.conf: check_for_updates enabled")

    launcher = stanzas.get("launcher", {})
    launcher_version = launcher.get("version")
    if manifest_version and launcher_version != manifest_version:
        fail(f"default/app.conf: [launcher] version {launcher_version!r} "
             f"does not match app.manifest info.id.version "
             f"{manifest_version!r}")
    else:
        ok("default/app.conf: launcher version matches manifest version")


# ---------------------------------------------------------------------------
# default/authorize.conf
# ---------------------------------------------------------------------------

def check_authorize():
    stanzas, order = load_conf("default/authorize.conf")
    if stanzas is None:
        return None, None

    capabilities = [s for s in order if s.startswith("capability::")]
    roles = [s for s in order if s.startswith("role_")]

    if len(capabilities) != 4:
        fail(f"default/authorize.conf: expected 4 capabilities, found "
             f"{len(capabilities)}: {capabilities}")
    else:
        ok("default/authorize.conf: 4 capabilities defined")

    if len(roles) != 4:
        fail(f"default/authorize.conf: expected 4 roles, found "
             f"{len(roles)}: {roles}")
    else:
        ok("default/authorize.conf: 4 roles defined")

    cap_names = {c.split("::", 1)[1] for c in capabilities}

    if "role_dsv_service" not in stanzas:
        fail("default/authorize.conf: role_dsv_service is required "
             "(the dedicated service identity)")
    else:
        svc = stanzas["role_dsv_service"]
        if (svc.get("importRoles") or "").strip():
            fail("default/authorize.conf: role_dsv_service must not "
                 "importRoles (least privilege - no inherited capabilities)")
        else:
            ok("default/authorize.conf: dsv_service imports no roles")

        if svc.get("rtSrchJobsQuota") != "0":
            fail("default/authorize.conf: role_dsv_service must set "
                 "rtSrchJobsQuota = 0 (real-time search off, per locked "
                 "decisions)")
        else:
            ok("default/authorize.conf: dsv_service has real-time search off")

        enabled_caps = [
            cap for cap in cap_names
            if svc.get(cap) == "enabled"
        ]
        if enabled_caps:
            fail(f"default/authorize.conf: role_dsv_service must not hold "
                 f"any admin/app capability, found enabled: {enabled_caps}")
        else:
            ok("default/authorize.conf: dsv_service holds no app "
               "capabilities (least privilege)")

        if svc.get("srchIndexesAllowed") in (None, "", "*"):
            fail("default/authorize.conf: role_dsv_service.srchIndexesAllowed "
                 "must be an explicit index list, not empty or '*'")
        else:
            ok("default/authorize.conf: dsv_service has an explicit "
               "srchIndexesAllowed")

    for admin_cap in ("admin_all_objects",):
        for role_name in roles:
            if stanzas[role_name].get(admin_cap) == "enabled":
                fail(f"default/authorize.conf: [{role_name}] enables "
                     f"{admin_cap} - violates the no-admin_all_objects rule")
    else:
        ok("default/authorize.conf: no role enables admin_all_objects")

    human_role_names = {r[len("role_"):] for r in roles if r != "role_dsv_service"}
    return cap_names, human_role_names


# ---------------------------------------------------------------------------
# default/collections.conf
# ---------------------------------------------------------------------------

def check_collections():
    stanzas, order = load_conf("default/collections.conf")
    if stanzas is None:
        return None

    if len(order) != 7:
        fail(f"default/collections.conf: expected 7 collections, found "
             f"{len(order)}: {order}")
    else:
        ok("default/collections.conf: 7 collections defined")

    for name in order:
        fields = {k: v for k, v in stanzas[name].items()
                   if k.startswith("field.")}
        if not fields:
            fail(f"default/collections.conf: [{name}] declares no typed "
                 f"fields")
        for fkey, ftype in fields.items():
            if ftype not in ("string", "number", "bool", "time", "cidr"):
                fail(f"default/collections.conf: [{name}] {fkey} has "
                     f"unknown type {ftype!r}")
    else:
        ok("default/collections.conf: all collections declare typed fields")

    return set(order)


# ---------------------------------------------------------------------------
# default/restmap.conf
# ---------------------------------------------------------------------------

def check_restmap(cap_names):
    stanzas, order = load_conf("default/restmap.conf")
    if stanzas is None:
        return None

    script_stanzas = [s for s in order if s.startswith("script:")]
    if len(script_stanzas) != 11:
        fail(f"default/restmap.conf: expected 11 REST endpoints, found "
             f"{len(script_stanzas)}: {script_stanzas}")
    else:
        ok("default/restmap.conf: 11 REST endpoints defined")

    bad_auth = []
    bad_caps = []
    for name in script_stanzas:
        st = stanzas[name]
        if st.get("passSystemAuth") != "false":
            bad_auth.append(name)
        method_caps = {k: v for k, v in st.items() if k.startswith("capability.")}
        if not method_caps:
            bad_caps.append(name)
        elif cap_names is not None:
            for mk, mv in method_caps.items():
                if mv not in cap_names:
                    fail(f"default/restmap.conf: [{name}] {mk} = {mv!r} "
                         f"is not a capability defined in authorize.conf")

    if bad_auth:
        fail(f"default/restmap.conf: endpoints must set "
             f"passSystemAuth = false: {bad_auth}")
    else:
        ok("default/restmap.conf: all endpoints require authentication")

    if bad_caps:
        fail(f"default/restmap.conf: endpoints missing per-method "
             f"capability gating: {bad_caps}")
    else:
        ok("default/restmap.conf: all endpoints are capability-gated "
           "per method")

    missing_scripts = []
    for name in script_stanzas:
        script = stanzas[name].get("script")
        if not script:
            fail(f"default/restmap.conf: [{name}] has no 'script' key")
            continue
        if not (APP_ROOT / "bin" / script).is_file():
            missing_scripts.append(f"[{name}] -> bin/{script}")
    if missing_scripts:
        fail(f"default/restmap.conf: script file(s) referenced but missing "
             f"from bin/: {missing_scripts}")
    else:
        ok("default/restmap.conf: every endpoint's script file exists under bin/")

    return script_stanzas


# ---------------------------------------------------------------------------
# default/web.conf
# ---------------------------------------------------------------------------

def check_web(restmap_script_count):
    stanzas, order = load_conf("default/web.conf")
    if stanzas is None:
        return

    expose_stanzas = [s for s in order if s.startswith("expose:")]
    if restmap_script_count is not None and len(expose_stanzas) != restmap_script_count:
        fail(f"default/web.conf: {len(expose_stanzas)} expose stanzas but "
             f"restmap.conf defines {restmap_script_count} endpoints - "
             f"every endpoint must be exposed to Splunk Web")
    else:
        ok(f"default/web.conf: {len(expose_stanzas)} expose stanzas match "
           f"restmap.conf endpoint count")

    for name in expose_stanzas:
        if "pattern" not in stanzas[name]:
            fail(f"default/web.conf: [{name}] missing 'pattern'")
        if "methods" not in stanzas[name]:
            fail(f"default/web.conf: [{name}] missing 'methods'")


# ---------------------------------------------------------------------------
# default/inputs.conf
# ---------------------------------------------------------------------------

def check_inputs():
    stanzas, order = load_conf("default/inputs.conf")
    if stanzas is None:
        return

    worker_stanzas = [s for s in order if s.startswith("dsv_validation_worker://")]
    if not worker_stanzas:
        fail("default/inputs.conf: no dsv_validation_worker:// stanza found")
        return
    if len(worker_stanzas) > 1:
        warn(f"default/inputs.conf: multiple worker stanzas defined: "
             f"{worker_stanzas} - locked decision is a single dispatcher, "
             f"confirm this is intentional")

    for name in worker_stanzas:
        if stanzas[name].get("disabled") != "1":
            fail(f"default/inputs.conf: [{name}] must be disabled = 1 "
                 f"until setup completes")
        else:
            ok(f"default/inputs.conf: [{name}] disabled until setup")

        stanza_prefix = name.split("://", 1)[0]
        script_path = APP_ROOT / "bin" / f"{stanza_prefix}.py"
        if not script_path.is_file():
            fail(f"default/inputs.conf: [{name}] has no matching modular "
                 f"input script at bin/{stanza_prefix}.py")
        else:
            ok(f"default/inputs.conf: modular input script bin/{stanza_prefix}.py exists")


# ---------------------------------------------------------------------------
# default/props.conf
# ---------------------------------------------------------------------------

def check_props():
    stanzas, order = load_conf("default/props.conf")
    if stanzas is None:
        return

    source_stanzas = [s for s in order if s.startswith("source::")]
    sourcetypes_referenced = {stanzas[s].get("sourcetype") for s in source_stanzas}

    if not source_stanzas:
        fail("default/props.conf: no source:: stanza routes the app's log "
             "file to a sourcetype")
    else:
        ok("default/props.conf: source:: stanza present")

    missing = [st for st in sourcetypes_referenced if st and st not in stanzas]
    if missing:
        fail(f"default/props.conf: source:: stanza references sourcetype(s) "
             f"with no matching stanza: {missing}")
    else:
        ok("default/props.conf: referenced sourcetype(s) are defined")


# ---------------------------------------------------------------------------
# UI cross-references: setup_view and nav must point at views that exist
# ---------------------------------------------------------------------------

def check_ui_views():
    app_conf_stanzas, _ = load_conf("default/app.conf")
    views_dir = APP_ROOT / "default" / "data" / "ui" / "views"

    if app_conf_stanzas is not None:
        setup_view = app_conf_stanzas.get("ui", {}).get("setup_view")
        if setup_view:
            if (views_dir / f"{setup_view}.xml").is_file():
                ok(f"default/app.conf: setup_view {setup_view!r} has a matching view file")
            else:
                fail(f"default/app.conf: setup_view {setup_view!r} has no "
                     f"default/data/ui/views/{setup_view}.xml")

    nav_path = APP_ROOT / "default" / "data" / "ui" / "nav" / "default.xml"
    if not nav_path.is_file():
        fail("default/data/ui/nav/default.xml is missing")
        return

    try:
        tree = ET.parse(nav_path)
    except ET.ParseError as e:
        fail(f"default/data/ui/nav/default.xml: not well-formed XML: {e}")
        return

    view_names = [v.get("name") for v in tree.getroot().iter("view") if v.get("name")]
    if not view_names:
        fail("default/data/ui/nav/default.xml: no <view> entries found")
        return

    missing_views = [v for v in view_names if not (views_dir / f"{v}.xml").is_file()]
    if missing_views:
        fail(f"default/data/ui/nav/default.xml: references view(s) with no "
             f"matching file: {missing_views}")
    else:
        ok(f"default/data/ui/nav/default.xml: all {len(view_names)} referenced view(s) exist")

    all_wellformed = True
    for xml_path in sorted(views_dir.glob("*.xml")):
        try:
            ET.parse(xml_path)
        except ET.ParseError as e:
            fail(f"{xml_path.relative_to(APP_ROOT)}: not well-formed XML: {e}")
            all_wellformed = False
    if all_wellformed:
        ok("default/data/ui/views: all view XML files are well-formed")


# ---------------------------------------------------------------------------
# default/transforms.conf <-> default/collections.conf cross-check
# ---------------------------------------------------------------------------

def check_transforms(collection_names):
    path = APP_ROOT / "default" / "transforms.conf"
    if not path.is_file():
        warn("default/transforms.conf not found - skipping lookup/collection cross-check")
        return
    stanzas, order = load_conf("default/transforms.conf")
    if stanzas is None:
        return

    if collection_names is None:
        return

    bad = []
    for name in order:
        collection = stanzas[name].get("collection")
        if stanzas[name].get("external_type") == "kvstore" and collection not in collection_names:
            bad.append(f"[{name}] -> collection={collection!r}")
    if bad:
        fail(f"default/transforms.conf: lookup(s) reference unknown "
             f"collections: {bad}")
    else:
        ok("default/transforms.conf: every kvstore lookup references a real collection")


# ---------------------------------------------------------------------------
# metadata/default.meta
# ---------------------------------------------------------------------------

def check_meta(human_role_names, collection_names):
    stanzas, order = load_conf("metadata/default.meta")
    if stanzas is None:
        return

    role_pattern_ok = True
    for name, body in stanzas.items():
        for perm_key in ("access",):
            val = body.get(perm_key, "")
            if "admin_all_objects" in val:
                fail(f"metadata/default.meta: [{name}] references "
                     f"admin_all_objects - violates least-privilege rule")
                role_pattern_ok = False
            if human_role_names:
                for token in _extract_roles(val):
                    if token not in human_role_names:
                        fail(f"metadata/default.meta: [{name}] references "
                             f"unknown role {token!r} (not defined in "
                             f"authorize.conf)")
                        role_pattern_ok = False
    if role_pattern_ok:
        ok("metadata/default.meta: all referenced roles exist in "
           "authorize.conf and no ACL uses admin_all_objects")

    if collection_names is not None:
        meta_collections = {
            s.split("/", 1)[1] for s in order if s.startswith("collections/")
        }
        missing = meta_collections - collection_names
        uncovered = collection_names - meta_collections
        if missing:
            fail(f"metadata/default.meta: ACLs reference unknown "
                 f"collections: {sorted(missing)}")
        if uncovered:
            fail(f"metadata/default.meta: collections with no explicit "
                 f"ACL (would fall back to a looser default): "
                 f"{sorted(uncovered)}")
        if not missing and not uncovered:
            ok("metadata/default.meta: every KV Store collection has an "
               "explicit least-privilege ACL")


def _extract_roles(value):
    # value looks like: "read : [ dsv_admin, dsv_editor ], write : [ dsv_admin ]"
    roles = set()
    for chunk in value.split("["):
        if "]" not in chunk:
            continue
        inner = chunk.split("]", 1)[0]
        for token in inner.split(","):
            token = token.strip()
            if token and token != "*":
                roles.add(token)
    return roles


# ---------------------------------------------------------------------------
# Placeholder token report (informational only)
# ---------------------------------------------------------------------------

KNOWN_PLACEHOLDERS = (
    "[YOUR ORG]",
    "[SUPPORT EMAIL]",
    "[SUPPORT MODEL]",
    "[DATE]",
    "[VERSION]",
    "REPLACE_WITH_",
)


def check_placeholders():
    hits = []
    for path in sorted(APP_ROOT.rglob("*")):
        if path.is_dir():
            continue
        if ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for token in KNOWN_PLACEHOLDERS:
            if token in text:
                hits.append(f"{path.relative_to(APP_ROOT)}: {token}")
    if hits:
        warn("known deployment placeholders still present (fill in before "
             "packaging - see HANDOFF.md \"Placeholders to fill\"):\n    " +
             "\n    ".join(hits))


# ---------------------------------------------------------------------------
# Python syntax + lint (Phase 2)
# ---------------------------------------------------------------------------

# Forbidden regardless of context - shelling out or spawning OS processes
# is not permitted for a Splunk Cloud app.
FORBIDDEN_IMPORT_MODULES = {"subprocess", "pty", "commands", "shlex"}
FORBIDDEN_OS_ATTRS = {
    "system", "popen", "popen2", "popen3", "popen4",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "fork", "forkpty",
}
FORBIDDEN_BUILTIN_CALLS = {"eval", "exec", "execfile", "compile"}

PYTHON_SOURCE_DIRS = ["bin", "tools"]


def _iter_python_files(rel_dirs):
    for rel_dir in rel_dirs:
        base = APP_ROOT / rel_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def check_python_syntax():
    checked = 0
    for path in _iter_python_files(PYTHON_SOURCE_DIRS):
        rel = path.relative_to(APP_ROOT)
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(rel))
        except SyntaxError as e:
            fail(f"{rel}: syntax error: {e}")
        else:
            checked += 1
    if checked:
        ok(f"python syntax: {checked} file(s) under {PYTHON_SOURCE_DIRS} parse cleanly")
    else:
        warn(f"python syntax: no .py files found under {PYTHON_SOURCE_DIRS}")


def check_python_lint():
    """
    A deliberately small, dependency-free lint pass (no flake8/pylint
    required) that enforces the one thing AppInspect and Cloud vetting
    actually gate hard on for this app: no shell, no OS-process
    execution, anywhere in the shipped bin/ tree.
    """
    hits = []
    for path in _iter_python_files(["bin"]):
        rel = path.relative_to(APP_ROOT)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
        except SyntaxError:
            continue  # already reported by check_python_syntax

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module
                    else [alias.name for alias in node.names]
                )
                for name in names:
                    top_level = (name or "").split(".")[0]
                    if top_level in FORBIDDEN_IMPORT_MODULES:
                        hits.append(f"{rel}:{node.lineno}: imports forbidden module {name!r}")

            elif isinstance(node, ast.Attribute):
                if node.attr in FORBIDDEN_OS_ATTRS and isinstance(node.value, ast.Name) and node.value.id == "os":
                    hits.append(f"{rel}:{node.lineno}: calls forbidden os.{node.attr}")

            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_BUILTIN_CALLS:
                    hits.append(f"{rel}:{node.lineno}: calls forbidden builtin {node.func.id}()")

    if hits:
        for h in hits:
            fail(f"python lint: {h}")
    else:
        ok("python lint: no shell/OS-process execution found under bin/")


def run_unit_tests():
    tests_dir = APP_ROOT / "tests" / "unit"
    if not tests_dir.is_dir():
        warn("tests/unit directory not found - no unit tests were run")
        return

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(tests_dir), pattern="test_*.py", top_level_dir=str(tests_dir))
    runner = unittest.TextTestRunner(verbosity=0, stream=io.StringIO())
    result = runner.run(suite)

    total = result.testsRun
    if total == 0:
        fail("tests/unit: no tests were discovered")
        return

    if result.wasSuccessful():
        ok(f"tests/unit: {total} test(s) passed")
    else:
        fail(
            f"tests/unit: {len(result.failures)} failure(s), "
            f"{len(result.errors)} error(s) out of {total} test(s)"
        )
        for test, trace in result.failures + result.errors:
            fail(f"tests/unit: {test.id()}\n{trace}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    check_required_files()
    check_no_placeholder_files()

    manifest_version = check_manifest()
    check_app_conf(manifest_version)
    cap_names, human_role_names = check_authorize()
    collection_names = check_collections()
    script_stanzas = check_restmap(cap_names)
    check_web(len(script_stanzas) if script_stanzas is not None else None)
    check_inputs()
    check_props()
    check_ui_views()
    check_transforms(collection_names)
    check_meta(human_role_names, collection_names)
    check_placeholders()

    check_python_syntax()
    check_python_lint()
    run_unit_tests()

    print(f"\n{len(PASSES)} check(s) passed.")
    for p in PASSES:
        print(f"  PASS  {p}")

    if WARNINGS:
        print(f"\n{len(WARNINGS)} warning(s):")
        for w in WARNINGS:
            print(f"  WARN  {w}")

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s):")
        for f in FAILURES:
            print(f"  FAIL  {f}")
        print("\nphase 1 validation: FAILED")
        return 1

    print("\nphase 1 validation: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
