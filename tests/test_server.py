"""The HTTP API, exercised against a live server on a loopback port.

Every request here carries what the hardened surface now requires (R-01, R-29):
a dev identity header, which loopback accepts, and ``X-DPRE-Request`` on every
state-changing POST. The refusals themselves live in ``tests/test_security.py``.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from dpre.server.app import Handler, Workspace, build_router
from dpre.server.multipart import parse

IDENTITY = "dev.reviewer"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    workspace = Workspace(tmp_path_factory.mktemp("server"))
    Handler.workspace = workspace
    Handler.routes = build_router(workspace)
    Handler.policy = None                      # rebuilt from the bound socket
    Handler.settings = None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, workspace
    httpd.shutdown()
    httpd.server_close()
    workspace.store.close()
    Handler.policy = None


def get(base: str, path: str, identity: str = IDENTITY, **headers):
    request = urllib.request.Request(f"{base}{path}", method="GET",
                                     headers={"X-DPRE-Identity": identity, **headers})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read())


def post(base: str, path: str, payload: dict, identity: str = IDENTITY, **headers):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-DPRE-Request": "1",
                 "X-DPRE-Identity": identity, **headers}, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read())


@pytest.fixture(scope="module")
def run_id(server):
    """One automated run the read tests share, so no test depends on another."""
    base, _ = server
    result = post(base, "/api/v1/run/automated",
                  {"industry": "insurance", "as_of": "2026-09-17", "catalog": "collibra"})
    assert result["ok"] is True
    return result["run_id"]


def test_static_application_is_served(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/", timeout=30) as response:
        page = response.read().decode()
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "Data Product Recommendation Engine" in page
    assert 'data-view="backlog"' in page
    for asset in ("/app.js", "/styles.css"):
        with urllib.request.urlopen(f"{base}{asset}", timeout=30) as response:
            assert len(response.read()) > 1000


def test_the_application_carries_no_conflicts_surface(server):
    """Conflicts left the browser entirely: no section, and no panel on the card."""
    import re
    base, _ = server
    with urllib.request.urlopen(f"{base}/", timeout=30) as response:
        page = response.read().decode()
    tabs = set(re.findall(r'data-view="([a-z]+)"', page))
    views = set(re.findall(r'id="view-([a-z]+)"', page))
    assert tabs == views
    assert tabs == {"start", "backlog", "portfolio", "gaps", "ask", "runs"}
    assert "view-conflicts" not in page

    with urllib.request.urlopen(f"{base}/app.js", timeout=30) as response:
        script = response.read().decode()
    for gone in ("renderConflicts", "paintCandidateConflicts", "resolveConflict"):
        assert gone not in script, gone
    names = re.search(r"const names = \[(.*?)\];", script, re.S).group(1)
    panels = re.findall(r"^  panels\.(\w+) =", script, re.M)
    assert re.findall(r"'([^']+)'", names) == panels
    assert "Conflicts" not in panels


def test_reference_endpoints(server):
    base, _ = server
    assert get(base, "/api/health")["status"] == "ok"
    industries = get(base, "/api/industries")["industries"]
    assert len(industries) == 9
    schemas = get(base, "/api/schemas")
    assert "cognos_kpi_lineage" in schemas["required"]
    assert any(s["key"] == "alation_metadata" for s in schemas["schemas"])
    assert get(base, "/api/seed-index")["seeds"]
    assert get(base, "/api/semantic-view")["queries"]


def test_versioned_and_legacy_prefixes_serve_the_same_route(server):
    """`/api/v1` is canonical; `/api` stays an alias for one release (R-31)."""
    base, _ = server
    assert get(base, "/api/v1/industries") == get(base, "/api/industries")
    assert get(base, "/api/v1/version")["api_version"] == "v1"
    assert get(base, "/api/v1/ready")["status"] == "ready"


def test_openapi_document_describes_the_router(server):
    base, _ = server
    document = get(base, "/api/v1/openapi.json")
    assert document["openapi"] == "3.1.0"
    paths = document["paths"]
    assert "/api/v1/runs/{run_id}/candidates" in paths
    assert "/api/v1/runs/{run_id}/candidates/{candidate_id}/seeds/{name}" in paths
    assert "/api/download" not in paths
    approve = paths["/api/v1/weights/approve"]["post"]
    assert "approve_weights" in approve["description"]
    assert any(p.get("$ref", "").endswith("/csrf") for p in approve["parameters"])
    assert "bearerToken" in document["components"]["securitySchemes"]


def test_automated_run_and_the_views_it_feeds(server, run_id):
    base, _ = server
    candidates = get(base, f"/api/runs/{run_id}/candidates")["candidates"]
    assert candidates
    detail = get(base, f"/api/runs/{run_id}/candidates/{candidates[0]['candidate_id']}")
    assert detail["metrics"] and detail["evidence"] and detail["seeds"]
    assert {"coverage_curve", "retirement_map", "conflict_heat_map"} <= set(
        get(base, f"/api/runs/{run_id}/portfolio"))
    assert get(base, f"/api/runs/{run_id}/gaps")["quarantine"]
    assert get(base, f"/api/runs/{run_id}/agents")["agents"]
    assert get(base, f"/api/runs/{run_id}/conflicts")["conflicts"]


def test_list_routes_page(server, run_id):
    base, _ = server
    full = get(base, f"/api/v1/runs/{run_id}/candidates")
    assert full["page"]["total"] == len(full["candidates"])
    first = get(base, f"/api/v1/runs/{run_id}/candidates?limit=1")
    assert len(first["candidates"]) == 1
    assert first["page"] == {"limit": 1, "offset": 0, "total": full["page"]["total"],
                             "returned": 1}
    second = get(base, f"/api/v1/runs/{run_id}/candidates?limit=1&offset=1")
    assert second["candidates"][0]["candidate_id"] != first["candidates"][0]["candidate_id"]
    assert get(base, "/api/v1/runs")["page"]["total"] >= 1


def test_review_identity_comes_from_the_principal_not_the_body(server, run_id):
    """R-01: the reviewer is who authenticated, whatever the body claims."""
    base, _ = server
    candidate_id = get(base, f"/api/runs/{run_id}/candidates")["candidates"][0]["candidate_id"]
    outcome = post(base, f"/api/runs/{run_id}/review",
                   {"candidate_id": candidate_id, "decision": "Accept",
                    "reviewer": "someone.else", "reason_code": "value_clear"},
                   identity="priya.silva")
    assert outcome["outcome"]["status"] == "Accepted"
    assert outcome["outcome"]["reviewer"] == "priya.silva"
    assert outcome["reviewer"] == "priya.silva"
    decisions = get(base, f"/api/v1/runs/{run_id}/decisions")["decisions"]
    assert "someone.else" not in {d["reviewer"] for d in decisions}


def test_seed_download_is_keyed_by_run_and_candidate(server, run_id):
    """R-05: no path parameter anywhere; the server composes the path."""
    base, _ = server
    candidates = get(base, f"/api/runs/{run_id}/candidates")["candidates"]
    detail = get(base, f"/api/runs/{run_id}/candidates/{candidates[0]['candidate_id']}")
    seed = detail["seeds"][0]
    assert seed["download"].startswith(f"/api/v1/runs/{run_id}/candidates/")
    request = urllib.request.Request(f"{base}{seed['download']}", method="GET",
                                     headers={"X-DPRE-Identity": IDENTITY})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        assert response.headers["Content-Disposition"].startswith("attachment")
    assert len(body) == seed["size"]


def test_synthetic_workbook_download_is_mapped_server_side(server):
    base, _ = server
    described = get(base, "/api/v1/synthetic/retail/workbook")
    assert described["download"] == "/api/v1/synthetic/retail/workbook/download"
    assert "path" not in described
    request = urllib.request.Request(f"{base}{described['download']}", method="GET",
                                     headers={"X-DPRE-Identity": IDENTITY})
    with urllib.request.urlopen(request, timeout=60) as response:
        assert response.read()[:4] == b"PK\x03\x04"


def test_chat_answers_cite_evidence(server, run_id):
    base, _ = server
    answer = post(base, "/api/chat",
                  {"question": "which candidates retire the most reports", "run_id": run_id})
    assert answer["citations"]
    assert "semantic view" in answer["bound_to"]


def upload_workbook(base: str, path, filename: str = "retail.xlsx"):
    boundary = "----dpretest"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
             ).encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{base}/api/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-DPRE-Request": "1", "X-DPRE-Identity": IDENTITY})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def test_manual_upload_and_run(server, tmp_path):
    base, workspace = server
    from dpre.synth import generate_pack
    from dpre.synth.workbook import write_pack_workbook
    import datetime as _dt

    pack = generate_pack("retail", as_of=_dt.date(2026, 9, 17))
    path = write_pack_workbook(pack, tmp_path / "retail.xlsx")
    uploaded = upload_workbook(base, path)

    entry = uploaded["files"][0]
    assert "path" not in entry, "an upload must never hand back a server path (R-05)"
    assert entry["upload_id"] and entry["sha256"]
    tables = entry["tables"]
    detected = {t["suggested_schema"] for t in tables if t["suggested_schema"]}
    assert {"cognos_rationalization", "cognos_kpi_lineage", "collibra_metadata"} <= detected

    inspected = post(base, f"/api/v1/uploads/{entry['upload_id']}/inspect", {})
    assert inspected["upload_id"] == entry["upload_id"]

    sources = [{"upload_id": entry["upload_id"], "schema_key": t["suggested_schema"],
                "sheet": t["sheet"], "mapping": t["mapping"]}
               for t in tables
               if t["suggested_schema"] and t["confidence"] >= 0.4
               and t["suggested_schema"] != "alation_metadata"]
    result = post(base, "/api/run/manual", {"sources": sources, "as_of": "2026-09-17"})
    assert result["ok"] is True
    assert result["candidates"]
    assert result["summary"]["mode"] == "manual"
    # R-26: the client's extract is not kept after it has been ingested.
    assert result["uploads_deleted"] == [entry["upload_id"]]
    assert workspace.registry.get(entry["upload_id"]) is None
    assert not list(workspace.uploads.glob("*"))


def test_manual_run_without_lineage_is_refused(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(base, "/api/run/manual", {"sources": []})
    assert excinfo.value.code == 400
    problem = json.loads(excinfo.value.read())
    assert problem["code"] == "DPRE-RUN-001"
    assert problem["type"].endswith("dpre-run-001")


def test_the_path_download_route_is_gone(server):
    """R-05: /api/download served the database; it no longer exists."""
    base, _ = server
    for target in ("/api/download?path=/etc/passwd",
                   f"/api/download?path={_db_path(server)}"):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{base}{target}", timeout=30)
        assert excinfo.value.code == 404


def _db_path(server) -> str:
    _, workspace = server
    return str(workspace.settings.database)


def test_the_database_lives_outside_anything_served(server):
    _, workspace = server
    assert workspace.settings.database.parent == workspace.private
    assert not (workspace.root / "engine.db").exists()
    assert workspace.uploads.parent == workspace.private


def test_multipart_parser_reads_fields_and_files():
    boundary = "xyz"
    body = (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="note"\r\n\r\nhello\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="files"; filename="a.csv"\r\n'
            "Content-Type: text/csv\r\n\r\nid,name\r\n1,x\r\n"
            f"--{boundary}--\r\n").encode()
    parts = parse(body, f"multipart/form-data; boundary={boundary}")
    assert len(parts) == 2
    assert parts[0].name == "note" and parts[0].text() == "hello"
    assert parts[1].is_file and parts[1].filename == "a.csv"
    assert parts[1].content.startswith(b"id,name")


def test_conflicts_remain_readable_and_resolvable_through_the_api(server, run_id):
    """The register is still produced, cited and adjudicable outside the browser."""
    base, _ = server
    conflicts = get(base, f"/api/runs/{run_id}/conflicts")["conflicts"]
    assert conflicts
    conflict = conflicts[0]
    assert conflict["difference_summary"] and conflict["semantic_model_decision"]
    assert conflict["resolution_status"] == "OPEN"

    resolved = post(base, f"/api/runs/{run_id}/conflicts/{conflict['conflict_id']}/resolve",
                    {"status": "RESOLVED_A", "reviewer": "ignored.name",
                     "note": "Side A matches the certified definition."},
                    identity="priya.silva")
    assert resolved["status"] == "RESOLVED_A"
    assert resolved["reviewer"] == "priya.silva"   # not the name in the body (R-01)
    after = get(base, f"/api/runs/{run_id}/conflicts")["conflicts"]
    updated = next(c for c in after if c["conflict_id"] == conflict["conflict_id"])
    assert updated["resolution_status"] == "RESOLVED_A"

    # And the heat map still ranks them by usage at stake.
    assert get(base, f"/api/runs/{run_id}/portfolio")["conflict_heat_map"]


def test_the_browser_application_obeys_its_own_content_security_policy(server):
    """The policy is default-src 'self' with no unsafe-inline, so a style
    attribute in markup is refused by the browser and the layout collapses
    silently. Nothing shipped may carry one."""
    base, _ = server
    for asset in ("/", "/app.js"):
        with urllib.request.urlopen(f"{base}{asset}", timeout=30) as response:
            body = response.read().decode()
        assert 'style="' not in body, f"{asset} carries an inline style attribute"
        assert "style: '" not in body, f"{asset} sets a style attribute through el()"
        assert "style: \"" not in body, f"{asset} sets a style attribute through el()"
    # Scripts and styles come from this origin only; there is no CDN to trust.
    with urllib.request.urlopen(f"{base}/", timeout=30) as response:
        page = response.read().decode()
        policy = response.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert "<script" not in page.replace('<script src="/app.js"></script>', "")


def test_the_backlog_row_carries_what_a_board_sequences_on(server, run_id):
    """A score alone cannot sequence a programme: size, money and wave ride
    along on the row so the table needs no second request per candidate."""
    base, _ = server
    row = get(base, f"/api/v1/runs/{run_id}/candidates")["candidates"][0]
    for field in ("size", "build_weeks", "annual_benefit", "wave", "rank_low", "rank_high",
                  "consumer_confirmed", "lineage_id"):
        assert field in row, field
    assert row["lineage_id"].startswith("LIN-")


def test_the_programme_and_governance_routes_answer(server, run_id):
    base, _ = server
    assert get(base, f"/api/v1/runs/{run_id}/waves")["waves"]
    assert get(base, f"/api/v1/runs/{run_id}/raid")["raid"]
    assert get(base, f"/api/v1/runs/{run_id}/value")["values"]
    assert get(base, f"/api/v1/runs/{run_id}/effort")["efforts"]
    assert get(base, f"/api/v1/runs/{run_id}/dependencies")["dependencies"]
    assert get(base, f"/api/v1/runs/{run_id}/status")["measures"]
    assert get(base, f"/api/v1/runs/{run_id}/sensitivity")["sensitivity"]
    assert get(base, f"/api/v1/runs/{run_id}/benchmark")["benchmark"]
    assert get(base, f"/api/v1/runs/{run_id}/delta")["run_id"] == run_id
    assert get(base, "/api/v1/transitions")["transitions"]
    assert get(base, "/api/v1/weights")["current"]["weight_version"]
    # A fresh estate has no council approval on file, and the audit says so
    # rather than reporting a clean bill.
    audit = get(base, "/api/v1/audit")
    assert audit["chain"]["ok"] is True
    assert audit["ok"] is False and audit["findings"]


def test_a_consumer_confirmation_clears_g1_and_is_keyed_by_lineage(server, run_id):
    """Gate G1 has a half no machine can supply. The four facts a human writes
    down are recorded against the lineage, so they survive the next run."""
    base, _ = server
    rows = get(base, f"/api/v1/runs/{run_id}/candidates?limit=500")["candidates"]
    exploratory = [c for c in rows if c["status"] == "Exploratory"]
    if not exploratory:
        pytest.skip("this pack produced no Exploratory candidate")
    candidate = exploratory[0]
    assert candidate["consumer_confirmed"] is False
    response = post(
        base, f"/api/v1/runs/{run_id}/candidates/{candidate['candidate_id']}/confirm-consumer",
        {"business_unit": "Retail Credit Risk",
         "blocked_decision": "weekly provisioning sign-off",
         "latency_tolerance": "next business day",
         "consequence": "the provision is set on last week's exposures"},
        identity="maria.chen")
    assert response["confirmed_by"] == "maria.chen"
    assert response["outcome"]["reason_code"] == "consumer_confirmed"
    history = get(base, f"/api/v1/runs/{run_id}/candidates/"
                        f"{candidate['candidate_id']}/history")
    assert history["candidate_id"] == candidate["candidate_id"]


def test_an_unauthenticated_upload_is_refused_with_the_remedy_that_applies(server):
    """The Manual path writes, so it needs an identity. On a loopback instance
    the remedy is to sign in, not to configure a single sign-on proxy: naming
    the wrong one sends somebody to fix something that is not broken."""
    base, _ = server
    boundary = "----dpretest"
    body = (f"--{boundary}\r\n".encode()
            + b'Content-Disposition: form-data; name="files"; filename="a.csv"\r\n'
            + b"Content-Type: text/csv\r\n\r\nid,name\r\n1,x\r\n"
            + f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"{base}/api/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "X-DPRE-Request": "1"})          # no identity header
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=30)
    assert excinfo.value.code == 401
    problem = json.loads(excinfo.value.read())
    assert problem["code"] == "DPRE-AUTH-001"
    assert "Sign in" in problem["detail"]
    # The flag the browser reads, so it can offer the button instead of
    # reporting the refusal and leaving the user stuck.
    assert problem["sign_in"] is True
    assert problem["required_action"] == "run"


def test_running_the_engine_unauthenticated_is_refused_the_same_way(server):
    base, _ = server
    request = urllib.request.Request(
        f"{base}/api/run/automated", data=json.dumps({"industry": "retail"}).encode(),
        headers={"Content-Type": "application/json", "X-DPRE-Request": "1"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=30)
    assert excinfo.value.code == 401
    assert json.loads(excinfo.value.read())["sign_in"] is True


def test_the_browser_asks_for_a_name_before_it_sends_a_client_extract(server):
    """An extract is the client's data. The application refuses locally rather
    than uploading the bytes and being refused afterwards."""
    base, _ = server
    with urllib.request.urlopen(f"{base}/app.js", timeout=30) as response:
        script = response.read().decode()
    upload = script.split("async function uploadFiles(")[1].split("\n}")[0]
    assert "state.principal" in upload and "signInBanner" in upload
    assert upload.index("signInBanner") < upload.index("new FormData")

    # Selecting the same file twice must fire the change event twice, or a
    # retry after a failure silently does nothing.
    assert "event.target.value = ''" in script

    # The Start tab says so before anything is attempted.
    with urllib.request.urlopen(f"{base}/", timeout=30) as response:
        page = response.read().decode()
    assert 'id="start-identity"' in page
    assert "function paintStartNotice" in script


def test_the_browser_sends_the_upload_id_the_server_hands_back(server):
    """The regression that broke the Manual path end to end.

    Uploads became opaque ids when paths stopped being served (R-05), and the
    browser's own source builder still sent `path`. Every run was refused as an
    unknown upload. Tests that assembled sources by hand could not see it,
    because the thing that was wrong was the assembling.
    """
    base, _ = server
    with urllib.request.urlopen(f"{base}/app.js", timeout=30) as response:
        script = response.read().decode()
    builder = script.split("function selectedSources(")[1].split("\n}")[0]
    assert "upload_id: file.upload_id" in builder
    assert "path: file.path" not in builder, "the server no longer serves paths"

    # And the response it reads that from really does carry one.
    with urllib.request.urlopen(f"{base}/api/v1/openapi.json", timeout=30) as response:
        assert json.loads(response.read())["openapi"]


def test_the_run_outcome_survives_the_click_it_came_from(server):
    """The status line was rewritten by the readiness helper in a finally
    block, so success, refusal and error all vanished and the panel read as
    though the button had done nothing."""
    base, _ = server
    with urllib.request.urlopen(f"{base}/app.js", timeout=30) as response:
        script = response.read().decode()
    handler = script.split("$('#btn-run-manual').addEventListener")[1].split("\n});")[0]
    # Read the code, not the comments: a comment explaining why the helper is
    # not called would otherwise fail this.
    code = re.sub(r"/\*.*?\*/", "", handler, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    assert "updateManualReadiness(" not in code, \
        "the finally block must not rewrite the status the run just wrote"
    # A failure is shown where the button is, not only at the top of the page.
    assert "status.append" in code and "banner error" in code


def test_one_estate_is_read_through_one_catalog(server, tmp_path):
    """Collibra and Alation describe the same physical estate. Ingesting both
    counts every column twice and inflates the backlog."""
    from dpre.ingest import ingest_manual
    from dpre.ingest.schemas import SCHEMAS
    from dpre.synth import generate_pack
    from dpre.synth.workbook import write_pack_workbook
    import datetime as _dt

    pack = generate_pack("utility", as_of=_dt.date(2026, 9, 17))
    path = write_pack_workbook(pack, tmp_path / "both.xlsx")
    from dpre.ingest import SourceSpec, inspect_file
    specs = []
    for table in inspect_file(path, sample_rows=0)["tables"]:
        key = table["suggested_schema"]
        if key in SCHEMAS and table["confidence"] >= 0.4:
            specs.append(SourceSpec(path=str(path), schema_key=key, sheet=table["sheet"],
                                    mapping=table["mapping"]))
    bound = {s.schema_key for s in specs}
    if not {"collibra_metadata", "alation_metadata"} <= bound:
        pytest.skip("this pack does not carry both catalogs")

    ingest = ingest_manual(specs, as_of=_dt.date(2026, 9, 17))
    catalogs = {c.catalog for c in ingest.bundle.columns}
    assert len(catalogs) == 1, "an estate read through two catalogs is counted twice"
    assert ingest.bundle.catalog in ("collibra", "alation")
    assert any(e.get("status") == "deduplicated" for e in ingest.log), \
        "setting a catalog aside is a decision the run should record"
