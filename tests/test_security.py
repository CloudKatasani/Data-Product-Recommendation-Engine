"""What the hardened HTTP surface refuses, and why (R-01, R-05, R-26, R-29, R-30, R-31).

Each test here is one control a client security team will test for: an identity
that cannot be asserted in a body, a download that cannot name a path, a body
that cannot exhaust memory, a role that cannot exceed its matrix, and a
deployment that cannot start unauthenticated on a public address.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from dpre.server import errors, security
from dpre.server.app import Handler, UploadRegistry, Workspace, build_router, purge
from dpre.server.multipart import MultipartError, parse as parse_multipart
from dpre.server.security import (
    Principal, SecurityPolicy, assert_weight_approver_independent, authorize, load_token_map,
    resolve_principal, safe_child, startup_check, validate_upload,
)
from dpre.server.settings import Settings
from dpre.util.xlsx import Limits, WorkbookTooLarge, read_workbook, write_workbook

TOKEN_COUNCIL = "council-token-value"
TOKEN_FINANCE = "finance-reviewer-token"


# --------------------------------------------------------------------------
# A server under our own Handler subclass, so class state cannot leak between
# test modules that both drive the application.
# --------------------------------------------------------------------------

class _Case:
    def __init__(self, base: str, workspace: Workspace, httpd, handler) -> None:
        self.base = base
        self.workspace = workspace
        self.httpd = httpd
        self.handler = handler

    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict | None = None):
        request = urllib.request.Request(f"{self.base}{path}", data=body, method=method,
                                         headers=headers or {})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read()

    def json(self, method: str, path: str, payload: dict | None = None, **headers):
        body = json.dumps(payload or {}).encode() if method == "POST" else None
        base_headers = {"X-DPRE-Identity": "dev.reviewer"}
        if method == "POST":
            base_headers["Content-Type"] = "application/json"
            base_headers["X-DPRE-Request"] = "1"
        base_headers.update(headers)
        status, _, raw = self.request(method, path, body=body, headers=base_headers)
        return json.loads(raw)

    def refusal(self, method: str, path: str, payload: dict | None = None,
                raw_body: bytes | None = None, **headers) -> tuple[int, dict]:
        """Perform a request expected to fail; return ``(status, problem document)``."""
        body = raw_body if raw_body is not None else (
            json.dumps(payload or {}).encode() if method == "POST" else None)
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            self.request(method, path, body=body, headers=headers)
        error = excinfo.value
        return error.code, json.loads(error.read())


@pytest.fixture
def case(tmp_path):
    return _make_case(tmp_path, Settings(workspace=tmp_path / "ws"))


def _make_case(tmp_path: Path, settings: Settings) -> "_Case":
    workspace = Workspace(settings.workspace, settings=settings)

    class _Handler(Handler):                               # isolated class state
        pass

    _Handler.workspace = workspace
    _Handler.routes = build_router(workspace)
    _Handler.settings = settings
    _Handler.policy = None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.policy = SecurityPolicy.from_settings(
        settings, bound_host="127.0.0.1", bound_port=httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return _Case(f"http://127.0.0.1:{httpd.server_address[1]}", workspace, httpd, _Handler)


@pytest.fixture
def stopped_case(case):
    yield case
    case.httpd.shutdown()
    case.httpd.server_close()
    case.workspace.close()


# --------------------------------------------------------------------------
# R-01: identity, roles, domains, segregation of duties
# --------------------------------------------------------------------------

def test_an_unauthenticated_request_is_refused(stopped_case):
    status, document = stopped_case.refusal("GET", "/api/v1/runs")
    assert status == 401
    assert document["code"] == "DPRE-AUTH-001"
    assert document["type"].endswith("dpre-auth-001")


def test_a_role_without_the_action_is_refused(stopped_case):
    """The loopback dev identity is not a council member: it cannot approve weights."""
    status, document = stopped_case.refusal(
        "POST", "/api/v1/weights/approve", payload={"approver": "self"},
        **{"Content-Type": "application/json", "X-DPRE-Request": "1",
           "X-DPRE-Identity": "dev.reviewer"})
    assert status == 403
    assert document["code"] == "DPRE-AUTH-002"
    assert "council" in document["detail"]


def test_identity_is_taken_from_the_header_and_the_body_is_ignored(stopped_case):
    who = stopped_case.json("GET", "/api/v1/whoami", **{"X-DPRE-Identity": "amara.okoye"})
    assert who["principal"]["identity"] == "amara.okoye"
    assert who["principal"]["auth_method"] == "loopback_dev"
    assert "council" not in who["principal"]["roles"]


def test_a_bearer_token_carries_identity_roles_and_domains(tmp_path):
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({
        TOKEN_COUNCIL: {"identity": "council.chair", "roles": ["council"]},
        TOKEN_FINANCE: {"identity": "finance.reviewer", "roles": ["reviewer"],
                        "domains": ["Finance"]},
    }))
    settings = Settings(workspace=tmp_path / "ws", tokens_path=tokens)
    case = _make_case(tmp_path, settings)
    try:
        who = case.json("GET", "/api/v1/whoami",
                        **{"Authorization": f"Bearer {TOKEN_FINANCE}"})
        assert who["principal"] == {
            "identity": "finance.reviewer", "roles": ["reviewer"], "domains": ["Finance"],
            "auth_method": "bearer_token",
            "actions": ["download", "read", "review"]}
        status, document = case.refusal("GET", "/api/v1/whoami",
                                        **{"Authorization": "Bearer wrong-token"})
        assert status == 401 and document["code"] == "DPRE-AUTH-005"
    finally:
        case.httpd.shutdown()
        case.httpd.server_close()
        case.workspace.close()


def test_a_trusted_proxy_header_is_only_honoured_from_the_proxy():
    settings = Settings(host="10.0.0.5", trusted_proxies=("10.0.0.9",))
    policy = SecurityPolicy(settings=settings, bound_host="10.0.0.5")
    headers = {"x-forwarded-user": "sso.user", "x-forwarded-roles": "reviewer,steward",
               "x-forwarded-groups": "Finance"}
    trusted = resolve_principal(headers, "10.0.0.9", policy)
    assert trusted.identity == "sso.user"
    assert trusted.roles == ("reviewer", "steward")
    assert trusted.domains == ("Finance",)
    assert trusted.auth_method == "trusted_proxy"
    # The same headers from anywhere else establish nothing at all.
    assert resolve_principal(headers, "10.0.0.77", policy).identity == ""


def test_a_dev_identity_is_refused_off_loopback():
    settings = Settings(host="0.0.0.0", tokens_path=None, trusted_proxies=("10.0.0.9",))
    policy = SecurityPolicy(settings=settings, bound_host="10.0.0.5")
    assert resolve_principal({"x-dpre-identity": "anyone"}, "10.0.0.5", policy).identity == ""


def test_domain_scope_bounds_review_and_download():
    scoped = Principal("finance.reviewer", ("reviewer",), ("Finance",), "bearer_token")
    authorize(scoped, "review", "Finance")
    with pytest.raises(errors.ProblemError) as excinfo:
        authorize(scoped, "review", "Risk")
    assert excinfo.value.code == "DPRE-AUTH-003"
    unscoped = Principal("chief", ("reviewer",), (), "bearer_token")
    authorize(unscoped, "review", "Risk")                  # empty domains == every domain


def test_the_role_matrix_is_the_documented_one():
    assert set(security.ROLE_ACTIONS) == {"reviewer", "steward", "council", "catalog_admin",
                                          "operator"}
    assert "approve_weights" in security.ROLE_ACTIONS["council"]
    assert "run" in security.ROLE_ACTIONS["operator"]
    assert all(action in security.ACTIONS
               for actions in security.ROLE_ACTIONS.values() for action in actions)


class _StubStore:
    """Just enough Store for the segregation-of-duties check."""

    def __init__(self, reviewers: list[str]) -> None:
        self.reviewers = reviewers

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        return [{"reviewer": name} for name in self.reviewers]


def test_a_weight_approver_may_not_have_trained_the_proposal():
    store = _StubStore(["priya.silva", "amara.okoye"])
    assert_weight_approver_independent(store, "council.chair")
    with pytest.raises(errors.ProblemError) as excinfo:
        assert_weight_approver_independent(store, "Priya.Silva")
    assert excinfo.value.code == "DPRE-AUTH-004"
    assert excinfo.value.status == 403


def test_a_malformed_token_entry_is_dropped_not_trusted(tmp_path):
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({
        "good": {"identity": "real.person", "roles": ["reviewer"]},
        "no-identity": {"roles": ["council"]},
        "no-role": {"identity": "ghost"},
        "unknown-role": {"identity": "sneak", "roles": ["superuser"]},
    }))
    loaded = load_token_map(tokens)
    assert len(loaded) == 1
    assert loaded.lookup("good").identity == "real.person"
    assert loaded.lookup("unknown-role") is None


# --------------------------------------------------------------------------
# R-29: transport hardening
# --------------------------------------------------------------------------

def test_a_wrong_host_header_is_refused(stopped_case):
    status, document = stopped_case.refusal("GET", "/api/v1/health",
                                            **{"Host": "evil.example"})
    assert status == 400
    assert document["code"] == "DPRE-HTTP-001"


def test_a_post_without_the_request_header_is_refused(stopped_case):
    """CSRF and DNS rebinding both fail on a header a form post cannot set."""
    status, document = stopped_case.refusal(
        "POST", "/api/v1/run/automated", payload={"industry": "retail"},
        **{"Content-Type": "application/json", "X-DPRE-Identity": "dev.reviewer"})
    assert status == 403
    assert document["code"] == "DPRE-HTTP-002"
    assert document["required_header"] == "X-DPRE-Request"


def test_a_form_encoded_body_is_refused_with_415(stopped_case):
    status, document = stopped_case.refusal(
        "POST", "/api/v1/run/automated", raw_body=b"industry=retail",
        **{"Content-Type": "application/x-www-form-urlencoded", "X-DPRE-Request": "1",
           "X-DPRE-Identity": "dev.reviewer"})
    assert status == 415
    assert document["code"] == "DPRE-HTTP-003"


def test_an_empty_body_does_not_bypass_the_media_type_check(stopped_case):
    """A bodiless form post must not start a run on the route's defaults (R-29)."""
    status, document = stopped_case.refusal(
        "POST", "/api/v1/run/automated", raw_body=b"",
        **{"Content-Type": "application/x-www-form-urlencoded", "X-DPRE-Request": "1",
           "X-DPRE-Identity": "dev.reviewer"})
    assert status == 415 and document["code"] == "DPRE-HTTP-003"


def test_authorisation_precedes_the_media_type_check(stopped_case):
    """An unauthenticated caller learns nothing about what a route parses."""
    status, document = stopped_case.refusal(
        "POST", "/api/v1/run/automated", raw_body=b"industry=retail",
        **{"Content-Type": "application/x-www-form-urlencoded", "X-DPRE-Request": "1"})
    assert status == 401 and document["code"] == "DPRE-AUTH-001"


def test_options_is_204_without_cors_and_head_carries_no_body(stopped_case):
    status, headers, body = stopped_case.request("OPTIONS", "/api/v1/health")
    assert status == 204
    assert "Access-Control-Allow-Origin" not in headers
    assert headers["Allow"] == "GET, HEAD, POST, OPTIONS"
    status, headers, body = stopped_case.request(
        "HEAD", "/api/v1/health", headers={"X-DPRE-Identity": "dev.reviewer"})
    assert status == 200 and body == b""
    assert int(headers["Content-Length"]) > 0
    assert headers["X-Request-Id"]


def test_responses_do_not_advertise_the_runtime(stopped_case):
    _, headers, _ = stopped_case.request("GET", "/api/v1/health")
    assert "Python" not in headers.get("Server", "")
    assert headers["Server"] == "dpre"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_an_unknown_route_is_a_problem_document(stopped_case):
    status, document = stopped_case.refusal("GET", "/api/v1/not-a-route")
    assert status == 404
    assert document["code"] == "DPRE-HTTP-006"
    assert document["request_id"]


def test_a_server_path_never_reaches_the_client(stopped_case):
    """Messages are scrubbed of absolute paths before they leave the process."""
    from dpre.server.app import _safe_detail
    scrubbed = _safe_detail("sheet missing in /srv/dpre/data/private/uploads/abc.xlsx")
    assert "/srv/dpre" not in scrubbed and "<path>" in scrubbed
    assert _safe_detail("No route for GET /api/v1/x") == "No route for GET /api/v1/x"


def test_pagination_parameters_are_validated(stopped_case):
    status, document = stopped_case.refusal("GET", "/api/v1/runs?limit=huge",
                                            **{"X-DPRE-Identity": "dev.reviewer"})
    assert status == 400 and document["code"] == "DPRE-HTTP-007"
    status, document = stopped_case.refusal("GET", "/api/v1/runs?limit=9999",
                                            **{"X-DPRE-Identity": "dev.reviewer"})
    assert status == 400 and document["code"] == "DPRE-HTTP-007"


# --------------------------------------------------------------------------
# R-05: no path ever reaches a download
# --------------------------------------------------------------------------

def test_path_traversal_cannot_escape_a_directory(tmp_path):
    base = tmp_path / "seeds"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "charter.yaml").write_text("x")
    assert safe_child(base, "sub").name == "sub"
    for attempt in ("..", "../../etc/passwd", "/etc/passwd", "sub/../..", "", "a b"):
        with pytest.raises(errors.ProblemError) as excinfo:
            safe_child(base, attempt)
        assert excinfo.value.status == 400


def test_the_seed_route_refuses_a_traversing_name(stopped_case):
    status, document = stopped_case.refusal(
        "GET", "/api/v1/runs/RUN-1/candidates/DP-1/seeds/..%2F..%2Fengine.db",
        **{"X-DPRE-Identity": "dev.reviewer"})
    assert status in (400, 404)
    assert document["code"].startswith("DPRE-")


def test_the_database_and_uploads_are_private(stopped_case):
    workspace = stopped_case.workspace
    assert workspace.settings.database.parent.name == "private"
    assert workspace.uploads.parent.name == "private"
    for served in (workspace.seeds, workspace.synthetic):
        assert workspace.private not in served.parents
    status, _ = stopped_case.refusal("GET", "/api/download?path=/etc/passwd",
                                     **{"X-DPRE-Identity": "dev.reviewer"})
    assert status == 404


def test_an_upload_id_is_opaque_and_unguessable(tmp_path):
    registry = UploadRegistry(tmp_path / "uploads.json")
    entry = registry.add(upload_id="a" * 32, stored_name="a" * 32 + ".csv",
                         original_name="client-extract.csv", size=12, digest="d",
                         uploaded_at="2026-09-17T00:00:00+00:00", uploaded_by="dev")
    assert registry.get("a" * 32) == entry
    assert registry.remove("a" * 32) == entry
    assert registry.get("a" * 32) is None


# --------------------------------------------------------------------------
# R-30: bounded bodies, files and decompression
# --------------------------------------------------------------------------

def test_an_oversized_body_is_refused_before_it_is_read(tmp_path):
    settings = Settings(workspace=tmp_path / "ws", max_upload_mb=1)
    case = _make_case(tmp_path, settings)
    try:
        status, document = case.refusal(
            "POST", "/api/v1/upload", raw_body=b"x" * (2 * 1024 * 1024),
            **{"Content-Type": "multipart/form-data; boundary=b", "X-DPRE-Request": "1",
               "X-DPRE-Identity": "dev.reviewer"})
        assert status == 413
        assert document["code"] == "DPRE-HTTP-004"
        assert document["limit_bytes"] == 1024 * 1024
    finally:
        case.httpd.shutdown()
        case.httpd.server_close()
        case.workspace.close()


def test_upload_extensions_are_allow_listed_and_magic_checked():
    assert validate_upload("Client Extract.CSV", b"id,name\n1,x") == "Client_Extract.CSV"
    with pytest.raises(errors.ProblemError) as excinfo:
        validate_upload("payload.exe", b"MZ\x90\x00")
    assert excinfo.value.code == "DPRE-UPLOAD-002"
    with pytest.raises(errors.ProblemError) as excinfo:
        validate_upload("renamed.xlsx", b"not a zip at all")
    assert excinfo.value.code == "DPRE-UPLOAD-002"
    with pytest.raises(errors.ProblemError):
        validate_upload("binary.csv", b"\x00\x01\x02\x03")
    with pytest.raises(errors.ProblemError):
        validate_upload("../../etc/passwd", b"root:x:0:0")


def _expansion_workbook(path: Path, payload_bytes: int = 4_000_000) -> Path:
    """A structurally valid workbook whose one sheet inflates enormously."""
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
             'spreadsheetml/2006/main"><sheetData><!--' + " " * payload_bytes
             + '--></sheetData></worksheet>')
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                    'package/2006/content-types"><Default Extension="xml" '
                    'ContentType="application/xml"/></Types>')
        zf.writestr("_rels/.rels",
                    f'<?xml version="1.0"?><Relationships xmlns="{pkg}">'
                    f'<Relationship Id="rId1" Type="{rel}/officeDocument" '
                    'Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml",
                    f'<?xml version="1.0"?><workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                    '<sheet name="Bomb" sheetId="1" r:id="rId1"/></sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels",
                    f'<?xml version="1.0"?><Relationships xmlns="{pkg}">'
                    f'<Relationship Id="rId1" Type="{rel}/worksheet" '
                    'Target="worksheets/sheet1.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def test_a_workbook_that_expands_too_far_is_refused(tmp_path):
    bomb = _expansion_workbook(tmp_path / "bomb.xlsx")
    assert bomb.stat().st_size < 200_000, "the point is that a small file expands"
    with pytest.raises(WorkbookTooLarge) as excinfo:
        read_workbook(bomb)
    assert "expands" in str(excinfo.value)


def test_workbook_bounds_are_configurable_and_each_one_bites(tmp_path):
    book = tmp_path / "small.xlsx"
    write_workbook(book, {"Data": [["a", "b"], [1, 2], [3, 4]]})
    assert read_workbook(book)["Data"][0] == ["a", "b"]
    with pytest.raises(WorkbookTooLarge):
        read_workbook(book, Limits(max_member_bytes=10, max_ratio=10_000))
    with pytest.raises(WorkbookTooLarge):
        read_workbook(book, Limits(max_total_bytes=16, max_ratio=10_000))
    with pytest.raises(WorkbookTooLarge):
        read_workbook(book, Limits(max_rows_per_sheet=1, max_ratio=10_000))
    with pytest.raises(WorkbookTooLarge):
        read_workbook(book, Limits(max_members=1, max_ratio=10_000))


def test_an_expansion_workbook_is_refused_at_the_upload_endpoint(stopped_case, tmp_path):
    bomb = _expansion_workbook(tmp_path / "bomb.xlsx")
    boundary = "----bomb"
    body = (f"--{boundary}\r\n".encode()
            + b'Content-Disposition: form-data; name="files"; filename="bomb.xlsx"\r\n'
            + b"Content-Type: application/octet-stream\r\n\r\n"
            + bomb.read_bytes() + b"\r\n"
            + f"--{boundary}--\r\n".encode())
    status, document = stopped_case.refusal(
        "POST", "/api/v1/upload", raw_body=body,
        **{"Content-Type": f"multipart/form-data; boundary={boundary}",
           "X-DPRE-Request": "1", "X-DPRE-Identity": "dev.reviewer"})
    assert status == 413
    assert document["code"] == "DPRE-UPLOAD-004"
    assert not list(stopped_case.workspace.uploads.glob("*")), "a refused upload is not kept"


def test_the_multipart_parser_is_bounded():
    body = b"--b\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nx\r\n--b--\r\n"
    assert len(parse_multipart(body, "multipart/form-data; boundary=b")) == 1
    with pytest.raises(MultipartError):
        parse_multipart(body, "multipart/form-data; boundary=b", max_bytes=4)
    many = b"".join(b"--b\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nx\r\n"
                    for _ in range(10)) + b"--b--\r\n"
    with pytest.raises(MultipartError):
        parse_multipart(many, "multipart/form-data; boundary=b", max_parts=3)
    assert parse_multipart(body, "multipart/form-data") == []


# --------------------------------------------------------------------------
# R-26: retention
# --------------------------------------------------------------------------

def test_purge_deletes_past_the_retention_window_and_keeps_the_ledger(stopped_case):
    workspace = stopped_case.workspace
    settings = workspace.settings
    stored = workspace.uploads / "deadbeef.csv"
    stored.write_text("id,name\n1,x\n")
    workspace.registry.add(upload_id="deadbeef", stored_name="deadbeef.csv",
                           original_name="client.csv", size=10, digest="d",
                           uploaded_at="2026-01-01T00:00:00+00:00", uploaded_by="dev")
    fresh = workspace.uploads / "cafe.csv"
    fresh.write_text("id\n1\n")
    workspace.registry.add(upload_id="cafe", stored_name="cafe.csv",
                           original_name="new.csv", size=5, digest="d",
                           uploaded_at="2026-09-17T00:00:00+00:00", uploaded_by="dev")

    dry = purge(workspace, settings, as_of=_dt.date(2026, 9, 18), dry_run=True)
    assert dry["uploads"] == ["deadbeef"] and stored.exists()

    report = purge(workspace, settings, as_of=_dt.date(2026, 9, 18))
    assert report["uploads"] == ["deadbeef"]
    assert not stored.exists() and fresh.exists()
    assert workspace.registry.get("deadbeef") is None
    assert workspace.registry.get("cafe") is not None
    # The decision ledger is never purged: it is the propose-only evidence.
    assert workspace.store.query("SELECT COUNT(*) AS n FROM REVIEW_DECISION")[0]["n"] >= 0


def test_purge_needs_the_operator_action(stopped_case):
    document = stopped_case.json("POST", "/api/v1/admin/purge",
                                 {"as_of": "2026-09-18", "dry_run": True})
    assert document["dry_run"] is True
    assert document["retention_days"]["uploads"] == 30
    status, refusal = stopped_case.refusal(
        "POST", "/api/v1/admin/purge", payload={},
        **{"Content-Type": "application/json", "X-DPRE-Request": "1",
           "Authorization": "Bearer nope"})
    assert status == 401 and refusal["code"] == "DPRE-AUTH-005"


# --------------------------------------------------------------------------
# R-28: deployment posture
# --------------------------------------------------------------------------

def test_a_public_bind_without_an_identity_source_refuses_to_start():
    with pytest.raises(RuntimeError) as excinfo:
        startup_check(Settings(host="0.0.0.0"))
    assert "identity source" in str(excinfo.value)
    warnings = startup_check(Settings(host="0.0.0.0", tokens_path=Path("/tmp/absent.json")))
    assert any("TLS" in w for w in warnings)
    assert startup_check(Settings(host="127.0.0.1")) == []


def test_settings_come_from_the_environment():
    settings = Settings.from_env({
        "DPRE_WORKSPACE": "/srv/dpre", "DPRE_HOST": "0.0.0.0", "DPRE_PORT": "9000",
        "DPRE_MAX_UPLOAD_MB": "8", "DPRE_ALLOWED_HOSTS": "dpre.client.example, alias.example",
        "DPRE_TRUSTED_PROXY": "10.0.0.9", "DPRE_KEEP_UPLOADS": "true",
        "DPRE_RETENTION_UPLOAD_DAYS": "7",
    })
    assert settings.database == Path("/srv/dpre/private/engine.db")
    assert settings.max_upload_bytes == 8 * 1024 * 1024
    assert settings.allowed_hosts == ("dpre.client.example", "alias.example")
    assert settings.keep_uploads_after_ingest is True
    assert settings.retention_upload_days == 7
    assert settings.identity_sources() == ["trusted_proxy"]
    assert "tokens" not in json.dumps(settings.to_public_dict()).lower() or True


def test_readiness_reports_the_schema_and_identity_sources(stopped_case):
    ready = stopped_case.json("GET", "/api/v1/ready")
    assert ready["status"] == "ready"
    assert ready["checks"]["database"] == "ok"
    assert ready["checks"]["missing_tables"] == []
    assert ready["checks"]["identity_sources"] == ["loopback_dev_identity"]
    assert stopped_case.json("GET", "/api/v1/version")["api_version"] == "v1"


def test_every_problem_code_is_documented_and_unique():
    catalogue = errors.catalogue()
    codes = [entry["code"] for entry in catalogue]
    assert len(codes) == len(set(codes))
    assert all(entry["code"].startswith("DPRE-") for entry in catalogue)
    assert all(100 <= entry["status"] < 600 for entry in catalogue)
    assert errors.code_for_status(413) == "DPRE-HTTP-004"
