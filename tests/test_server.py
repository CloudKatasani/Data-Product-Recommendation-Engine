"""The HTTP API, exercised against a live server on a loopback port."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from dpre.server.app import Handler, Workspace, build_router
from dpre.server.multipart import parse


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    workspace = Workspace(tmp_path_factory.mktemp("server"))
    Handler.workspace = workspace
    Handler.routes = build_router(workspace)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, workspace
    httpd.shutdown()
    httpd.server_close()
    workspace.store.close()


def get(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=90) as response:
        return json.loads(response.read())


def post(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read())


def test_static_application_is_served(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/", timeout=30) as response:
        page = response.read().decode()
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


def test_automated_run_and_the_views_it_feeds(server):
    base, _ = server
    result = post(base, "/api/run/automated",
                  {"industry": "insurance", "as_of": "2026-09-17", "catalog": "collibra"})
    assert result["ok"] is True
    run_id = result["run_id"]
    assert result["candidates"] and result["portfolio"]["coverage_curve"]
    assert all(gate["passed"] for gate in result["summary"]["quality_gates"])

    candidates = get(base, f"/api/runs/{run_id}/candidates")["candidates"]
    assert candidates
    detail = get(base, f"/api/runs/{run_id}/candidates/{candidates[0]['candidate_id']}")
    assert detail["metrics"] and detail["evidence"] and detail["seeds"]
    assert {"coverage_curve", "retirement_map", "conflict_heat_map"} <= set(
        get(base, f"/api/runs/{run_id}/portfolio"))
    assert get(base, f"/api/runs/{run_id}/gaps")["quarantine"]
    assert get(base, f"/api/runs/{run_id}/agents")["agents"]
    assert get(base, f"/api/runs/{run_id}/conflicts")["conflicts"]


def test_review_requires_a_named_reviewer(server):
    base, _ = server
    run_id = get(base, "/api/runs")["runs"][0]["run_id"]
    candidate_id = get(base, f"/api/runs/{run_id}/candidates")["candidates"][0]["candidate_id"]
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(base, f"/api/runs/{run_id}/review",
             {"candidate_id": candidate_id, "decision": "Accept"})
    assert excinfo.value.code == 400
    outcome = post(base, f"/api/runs/{run_id}/review",
                   {"candidate_id": candidate_id, "decision": "Accept",
                    "reviewer": "priya.silva", "reason_code": "value_clear"})
    assert outcome["outcome"]["status"] == "Accepted"


def test_chat_answers_cite_evidence(server):
    base, _ = server
    run_id = get(base, "/api/runs")["runs"][0]["run_id"]
    answer = post(base, "/api/chat",
                  {"question": "which candidates retire the most reports", "run_id": run_id})
    assert answer["citations"]
    assert "semantic view" in answer["bound_to"]


def test_manual_upload_and_run(server, tmp_path):
    base, workspace = server
    from dpre.synth import generate_pack
    from dpre.synth.workbook import write_pack_workbook
    import datetime as _dt

    pack = generate_pack("retail", as_of=_dt.date(2026, 9, 17))
    path = write_pack_workbook(pack, tmp_path / "retail.xlsx")
    boundary = "----dpretest"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="files"; filename="retail.xlsx"\r\n'
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{base}/api/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        uploaded = json.loads(response.read())

    tables = uploaded["files"][0]["tables"]
    detected = {t["suggested_schema"] for t in tables if t["suggested_schema"]}
    assert {"cognos_rationalization", "cognos_kpi_lineage", "collibra_metadata"} <= detected

    sources = [{"path": uploaded["files"][0]["path"], "schema_key": t["suggested_schema"],
                "sheet": t["sheet"], "mapping": t["mapping"]}
               for t in tables
               if t["suggested_schema"] and t["confidence"] >= 0.4
               and t["suggested_schema"] != "alation_metadata"]
    result = post(base, "/api/run/manual", {"sources": sources, "as_of": "2026-09-17"})
    assert result["ok"] is True
    assert result["candidates"]
    assert result["summary"]["mode"] == "manual"


def test_manual_run_without_lineage_is_refused(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(base, "/api/run/manual", {"sources": []})
    assert excinfo.value.code == 400


def test_download_is_confined_to_the_workspace(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{base}/api/download?path=/etc/passwd", timeout=30)
    assert excinfo.value.code == 404


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


def test_conflicts_remain_readable_and_resolvable_through_the_api(server):
    """The register is still produced, cited and adjudicable outside the browser."""
    base, _ = server
    runs = get(base, "/api/runs")["runs"]
    run_id = runs[0]["run_id"] if runs else post(
        base, "/api/run/automated", {"industry": "utility", "as_of": "2026-09-17"})["run_id"]
    conflicts = get(base, f"/api/runs/{run_id}/conflicts")["conflicts"]
    assert conflicts
    conflict = conflicts[0]
    assert conflict["difference_summary"] and conflict["semantic_model_decision"]
    assert conflict["resolution_status"] == "OPEN"

    resolved = post(base, f"/api/runs/{run_id}/conflicts/{conflict['conflict_id']}/resolve",
                    {"status": "RESOLVED_A", "reviewer": "priya.silva",
                     "note": "definition A is the certified basis"})
    assert resolved["status"] == "RESOLVED_A"
    after = get(base, f"/api/runs/{run_id}/conflicts")["conflicts"]
    updated = next(c for c in after if c["conflict_id"] == conflict["conflict_id"])
    assert updated["resolution_status"] == "RESOLVED_A"

    # The closed vocabulary and the rationale requirement are enforced, not advisory.
    for bad in ({"status": "RESOLVED", "reviewer": "priya.silva", "note": "n"},
                {"status": "RESOLVED_B", "reviewer": "priya.silva"}):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            post(base, f"/api/runs/{run_id}/conflicts/{conflict['conflict_id']}/resolve", bad)
        assert excinfo.value.code == 400

    # And the heat map still ranks them by usage at stake.
    assert get(base, f"/api/runs/{run_id}/portfolio")["conflict_heat_map"]
