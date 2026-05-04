"""Tests for src/onvif_server.py.

Covers the pure helpers (envelope / soap_fault / parse_op), the
`_virtual_camera` builder (UUID determinism is critical — see
CLAUDE.md "Don't break these"), each registered SOAP handler, and a
smoke test that a freshly-bound HTTP server answers a real
GetDeviceInformation call end-to-end.
"""
import http.client
import socket
import threading
import xml.etree.ElementTree as ET

import pytest


SOAP_NS = "{http://www.w3.org/2003/05/soap-envelope}"
TDS_NS  = "{http://www.onvif.org/ver10/device/wsdl}"
TRT_NS  = "{http://www.onvif.org/ver10/media/wsdl}"
TT_NS   = "{http://www.onvif.org/ver10/schema}"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_envelope_is_well_formed_xml(server_modules):
    onvif, _ = server_modules
    body = onvif.envelope("<tds:GetWsdlUrlResponse/>")
    root = ET.fromstring(body)
    assert root.tag == f"{SOAP_NS}Envelope"
    assert root.find(f"{SOAP_NS}Body") is not None


def test_soap_fault_returns_proper_fault(server_modules):
    onvif, _ = server_modules
    body = onvif.soap_fault("custom reason")
    root = ET.fromstring(body)
    fault = root.find(f"{SOAP_NS}Body/{SOAP_NS}Fault")
    assert fault is not None
    reason = fault.find(f"{SOAP_NS}Reason/{SOAP_NS}Text").text
    assert reason == "custom reason"


def test_parse_op_extracts_operation_name(server_modules):
    onvif, _ = server_modules
    body = onvif.envelope("<tds:GetCapabilities/>")
    assert onvif.parse_op(body) == "GetCapabilities"


def test_parse_op_returns_empty_on_garbage(server_modules):
    onvif, _ = server_modules
    assert onvif.parse_op(b"not xml at all") == ""


def test_parse_op_returns_empty_on_empty_body(server_modules):
    onvif, _ = server_modules
    body = onvif.envelope("")
    assert onvif.parse_op(body) == ""


def test_parse_op_does_not_expand_internal_entities(server_modules):
    """Regression for the billion-laughs class of attacks. The previous
    implementation parsed untrusted XML with xml.etree.ElementTree,
    whose docs explicitly warn it is not safe against malicious data.
    The replacement uses regex, so an entity-bomb body must be handled
    in O(n) without ever materialising the expansion."""
    onvif, _ = server_modules
    bomb = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE root ['
        b'<!ENTITY a "AAAAAAAAAA">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
        b']>'
        b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        b'<s:Body><tds:GetCapabilities xmlns:tds="x">&c;</tds:GetCapabilities>'
        b'</s:Body></s:Envelope>'
    )
    # We only care that the dispatcher still returns the op tag without
    # blowing up RAM/CPU. Body length is the literal payload — never
    # the expanded form.
    assert onvif.parse_op(bomb) == "GetCapabilities"


def test_parse_op_only_returns_word_chars(server_modules):
    """The op name is echoed in the SOAP fault for unknown ops without
    XML escaping; if parse_op ever returned a value containing `<` or
    `&` it would break the response. The regex is `\\w+` so this is
    structurally guaranteed — pin it with a test."""
    onvif, _ = server_modules
    body = (
        b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        b'<s:Body><foo>'        # opening tag
    )
    op = onvif.parse_op(body)
    assert op == "" or op.replace("_", "").isalnum()


# ---------------------------------------------------------------------------
# _virtual_camera + UUID stability
# ---------------------------------------------------------------------------
def test_virtual_camera_uuid_is_deterministic(server_modules):
    """Regression for the hash() bug: SerialNumber must NOT change
    between processes for the same (name, kind), or homebridge-unifi-
    protect's MAC-based override silently breaks."""
    onvif, _ = server_modules
    cam  = {"name": "garden", "model": "c675d"}
    lens = {"kind": "wide", "stream_path": "garden_wide",
            "snap_path": "/garden_wide", "onvif_port": 8081}
    a = onvif._virtual_camera(cam, lens)
    b = onvif._virtual_camera(cam, lens)
    assert a["uuid"] == b["uuid"]


def test_virtual_camera_uuid_distinguishes_lenses(server_modules):
    onvif, _ = server_modules
    cam = {"name": "garden", "model": "c675d"}
    wide = {"kind": "wide", "stream_path": "garden_wide",
            "snap_path": "/garden_wide", "onvif_port": 8081}
    tele = {"kind": "tele", "stream_path": "garden_tele",
            "snap_path": "/garden_tele", "onvif_port": 8082}
    assert onvif._virtual_camera(cam, wide)["uuid"] \
        != onvif._virtual_camera(cam, tele)["uuid"]


def test_virtual_camera_uuid_against_known_md5(server_modules):
    """Pin the UUID derivation to the exact md5(name+'_'+kind) scheme.
    Catches any silent change to the hash input."""
    import hashlib
    onvif, _ = server_modules
    seed = b"garden_wide"
    h = int(hashlib.md5(seed).hexdigest(), 16)
    expected = f"11111111-2222-3333-4444-{h % 10**12:012d}"

    vc = onvif._virtual_camera(
        {"name": "garden", "model": "c675d"},
        {"kind": "wide", "stream_path": "garden_wide",
         "snap_path": "/garden_wide", "onvif_port": 8081},
    )
    assert vc["uuid"] == expected


def test_virtual_camera_shape(server_modules):
    onvif, _ = server_modules
    vc = onvif._virtual_camera(
        {"name": "garden", "model": "c675d"},
        {"kind": "wide", "stream_path": "garden_wide",
         "snap_path": "/garden_wide", "onvif_port": 8081},
    )
    assert vc["name"]  == "garden_wide"
    assert vc["model"] == "C675D"            # uppercased
    assert len(vc["profiles"]) == 1
    p = vc["profiles"][0]
    assert p["rtsp_path"] == "/garden_wide"
    assert p["snap_path"] == "/garden_wide"
    assert p["width"] == 1920 and p["height"] == 1080


def test_select_profile_returns_first_when_no_token(server_modules):
    onvif, _ = server_modules
    cam = onvif.CAMERAS[8081]
    assert onvif._select_profile(cam, b"") is cam["profiles"][0]


def test_select_profile_falls_back_when_token_unknown(server_modules):
    """User-supplied tokens that don't match any profile must NOT
    cause a KeyError — fall back to the first profile, so handlers
    can interpolate fields without escaping."""
    onvif, _ = server_modules
    cam = onvif.CAMERAS[8081]
    body = (b'<s:Envelope xmlns:s="x"><s:Body><trt:GetProfile xmlns:trt="y">'
            b'<trt:ProfileToken>does-not-exist</trt:ProfileToken>'
            b'</trt:GetProfile></s:Body></s:Envelope>')
    assert onvif._select_profile(cam, body) is cam["profiles"][0]


def test_cameras_dict_is_keyed_by_onvif_port(server_modules):
    onvif, _ = server_modules
    # 2 lenses on garden + 1 on front = 3 ONVIF ports.
    assert sorted(onvif.CAMERAS.keys()) == [8081, 8082, 8083]
    assert onvif.CAMERAS[8081]["name"] == "garden_wide"
    assert onvif.CAMERAS[8083]["name"] == "front_main"


# ---------------------------------------------------------------------------
# Operation handlers
# ---------------------------------------------------------------------------
def _wrap(onvif, cam, op_name, body=b""):
    """Run a handler the way the HTTP path does: inject `port` and wrap
    the result in a SOAP envelope. Returns parsed ElementTree root."""
    handler = onvif.HANDLERS[op_name]
    fragment = handler({**cam, "port": cam["onvif_port"]}, body)
    return ET.fromstring(onvif.envelope(fragment))


@pytest.fixture
def garden_wide_cam(server_modules):
    onvif, _ = server_modules
    cam = onvif.CAMERAS[8081].copy()
    cam["onvif_port"] = 8081
    return cam


def test_handlers_registered_for_known_ops(server_modules):
    onvif, _ = server_modules
    expected = {
        "GetSystemDateAndTime", "GetCapabilities", "GetServices",
        "GetServiceCapabilities", "GetDeviceInformation", "GetScopes",
        "GetNetworkInterfaces", "GetEndpointReference", "GetWsdlUrl",
        "GetProfiles", "GetProfile", "GetVideoSources",
        "GetVideoEncoderConfigurations", "GetVideoSourceConfigurations",
        "GetStreamUri", "GetSnapshotUri",
    }
    assert expected <= set(onvif.HANDLERS)


def test_GetSystemDateAndTime(server_modules, garden_wide_cam):
    onvif, _ = server_modules
    root = _wrap(onvif, garden_wide_cam, "GetSystemDateAndTime")
    utc = root.find(
        f"{SOAP_NS}Body/{TDS_NS}GetSystemDateAndTimeResponse/"
        f"{TDS_NS}SystemDateAndTime/{TT_NS}UTCDateTime")
    assert utc is not None
    year = int(utc.find(f"{TT_NS}Date/{TT_NS}Year").text)
    assert 2024 <= year <= 2100


def test_GetDeviceInformation_includes_uuid_and_model(server_modules, garden_wide_cam):
    onvif, _ = server_modules
    root = _wrap(onvif, garden_wide_cam, "GetDeviceInformation")
    resp = root.find(f"{SOAP_NS}Body/{TDS_NS}GetDeviceInformationResponse")
    assert resp.find(f"{TDS_NS}Manufacturer").text == "TP-Link"
    assert resp.find(f"{TDS_NS}Model").text == garden_wide_cam["model"]
    assert resp.find(f"{TDS_NS}SerialNumber").text == garden_wide_cam["uuid"]


def test_GetCapabilities_advertises_correct_xaddr(server_modules, garden_wide_cam):
    onvif, _ = server_modules
    root = _wrap(onvif, garden_wide_cam, "GetCapabilities")
    xaddr = root.find(
        f"{SOAP_NS}Body/{TDS_NS}GetCapabilitiesResponse/"
        f"{TDS_NS}Capabilities/{TT_NS}Device/{TT_NS}XAddr").text
    assert xaddr == f"http://{onvif.PUBLIC_HOST}:8081/onvif/device_service"


def test_GetServices_lists_device_and_media(server_modules, garden_wide_cam):
    onvif, _ = server_modules
    root = _wrap(onvif, garden_wide_cam, "GetServices")
    namespaces = [
        ns.text for ns in root.findall(
            f"{SOAP_NS}Body/{TDS_NS}GetServicesResponse/"
            f"{TDS_NS}Service/{TDS_NS}Namespace")
    ]
    assert "http://www.onvif.org/ver10/device/wsdl" in namespaces
    assert "http://www.onvif.org/ver10/media/wsdl"  in namespaces


def test_GetProfiles_one_profile_per_lens(server_modules, garden_wide_cam):
    onvif, _ = server_modules
    root = _wrap(onvif, garden_wide_cam, "GetProfiles")
    profiles = root.findall(
        f"{SOAP_NS}Body/{TRT_NS}GetProfilesResponse/{TRT_NS}Profiles")
    assert len(profiles) == len(garden_wide_cam["profiles"])


def test_GetStreamUri_embeds_credentials_and_path(server_modules, garden_wide_cam):
    onvif, _ = server_modules
    body = (b'<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
            b'<s:Body><trt:GetStreamUri xmlns:trt="x">'
            b'<trt:ProfileToken>main_stream</trt:ProfileToken>'
            b'</trt:GetStreamUri></s:Body></s:Envelope>')
    root = _wrap(onvif, garden_wide_cam, "GetStreamUri", body)
    uri = root.find(
        f"{SOAP_NS}Body/{TRT_NS}GetStreamUriResponse/"
        f"{TRT_NS}MediaUri/{TT_NS}Uri").text
    assert uri.startswith(f"rtsp://{onvif.RTSP_USER}:{onvif.RTSP_PASS}@")
    assert uri.endswith("/garden_wide")


def test_GetSnapshotUri_points_at_snap_port(server_modules, garden_wide_cam):
    onvif, _ = server_modules
    root = _wrap(onvif, garden_wide_cam, "GetSnapshotUri")
    uri = root.find(
        f"{SOAP_NS}Body/{TRT_NS}GetSnapshotUriResponse/"
        f"{TRT_NS}MediaUri/{TT_NS}Uri").text
    assert uri == f"http://{onvif.PUBLIC_HOST}:{onvif.SNAP_PORT}/garden_wide"


def test_GetScopes_lists_camera_name(server_modules, garden_wide_cam):
    onvif, _ = server_modules
    root = _wrap(onvif, garden_wide_cam, "GetScopes")
    items = [s.text for s in root.findall(
        f"{SOAP_NS}Body/{TDS_NS}GetScopesResponse/"
        f"{TDS_NS}Scopes/{TT_NS}ScopeItem")]
    assert any(garden_wide_cam["name"] in s for s in items)


# ---------------------------------------------------------------------------
# End-to-end HTTP smoke test
# ---------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_http_server_answers_get_device_information(server_modules):
    """Bind the real HTTP handler on a random port and POST a SOAP
    request — proves the request/response wiring still parses."""
    onvif, _ = server_modules
    port = _free_port()
    cam = onvif.CAMERAS[8081]
    srv = onvif.ThreadedHTTPServer(("127.0.0.1", port), onvif.make_handler(cam))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        soap = (b'<?xml version="1.0"?>'
                b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
                b'<s:Body><tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>'
                b'</s:Body></s:Envelope>')
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/onvif/device_service", soap,
                     {"Content-Type": "application/soap+xml"})
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read()
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()

    root = ET.fromstring(body)
    serial = root.find(
        f"{SOAP_NS}Body/{TDS_NS}GetDeviceInformationResponse/"
        f"{TDS_NS}SerialNumber").text
    assert serial == cam["uuid"]


def test_http_server_rejects_oversize_body(server_modules):
    """Hostile clients can claim a huge Content-Length and force the
    server to allocate megabytes per request. The handler must reject
    anything over MAX_BODY_BYTES before reading."""
    onvif, _ = server_modules
    port = _free_port()
    cam = onvif.CAMERAS[8081]
    srv = onvif.ThreadedHTTPServer(("127.0.0.1", port), onvif.make_handler(cam))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # Claim a length > MAX_BODY_BYTES; we don't need to actually
        # send that many bytes — the handler must reject on the header.
        oversize = onvif.MAX_BODY_BYTES + 1
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/onvif/device_service")
        conn.putheader("Content-Type", "application/soap+xml")
        conn.putheader("Content-Length", str(oversize))
        conn.endheaders()
        # Send a few bytes only; handler must close after the 413.
        try:
            conn.send(b"x" * 16)
        except Exception:
            pass
        resp = conn.getresponse()
        assert resp.status == 413
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_server_rejects_malformed_content_length(server_modules):
    onvif, _ = server_modules
    port = _free_port()
    cam = onvif.CAMERAS[8081]
    srv = onvif.ThreadedHTTPServer(("127.0.0.1", port), onvif.make_handler(cam))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # Raw socket — http.client refuses to send non-numeric Content-Length.
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(
            b"POST /onvif/device_service HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/soap+xml\r\n"
            b"Content-Length: not-a-number\r\n"
            b"Connection: close\r\n\r\n"
        )
        # Read the status line.
        data = b""
        while b"\r\n" not in data:
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk
        s.close()
        assert b" 400 " in data.split(b"\r\n", 1)[0]
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_server_does_not_leak_exception_text(server_modules, monkeypatch):
    """A handler that crashes must not echo the exception's str() into
    the SOAP fault body — it could expose paths or internal state."""
    onvif, _ = server_modules

    def boom(cam, body):
        raise RuntimeError("/private/secret/path was missing")
    monkeypatch.setitem(onvif.HANDLERS, "GetCapabilities", boom)

    port = _free_port()
    cam = onvif.CAMERAS[8081]
    srv = onvif.ThreadedHTTPServer(("127.0.0.1", port), onvif.make_handler(cam))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        soap = (b'<?xml version="1.0"?>'
                b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
                b'<s:Body><tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>'
                b'</s:Body></s:Envelope>')
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/onvif/device_service", soap,
                     {"Content-Type": "application/soap+xml"})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        assert resp.status == 500
        assert b"/private/secret/path" not in body
        assert b"RuntimeError"          not in body
        assert b"Internal error"        in body
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_server_returns_soap_fault_for_unknown_op(server_modules):
    onvif, _ = server_modules
    port = _free_port()
    cam = onvif.CAMERAS[8081]
    srv = onvif.ThreadedHTTPServer(("127.0.0.1", port), onvif.make_handler(cam))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        soap = (b'<?xml version="1.0"?>'
                b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
                b'<s:Body><tds:Bogus xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>'
                b'</s:Body></s:Envelope>')
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/onvif/device_service", soap,
                     {"Content-Type": "application/soap+xml"})
        resp = conn.getresponse()
        assert resp.status == 200          # ONVIF: faults travel as 200
        body = resp.read()
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()

    root = ET.fromstring(body)
    assert root.find(f"{SOAP_NS}Body/{SOAP_NS}Fault") is not None
