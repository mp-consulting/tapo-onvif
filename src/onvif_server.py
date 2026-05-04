#!/usr/bin/env python3
"""Minimal Python ONVIF Profile S server for the Tapo bridge.

Hand-written SOAP responses for the ~15 ONVIF operations that UniFi Protect,
Scrypted, Homebridge, and generic ONVIF clients actually use. Returns proper
SOAP faults for unknown operations (no HTTP 500 / TypeError).

One virtual ONVIF camera is served per (real-cam, lens) pair, on the port
configured in cameras.yml under `onvif_ports.<kind>`. So a single dual-lens
C675D becomes two ONVIF endpoints on two ports; N cams × M lenses = N*M ports.

Replaces daniela-hase/onvif-server (Node.js + soap@1.1.5) which crashed on
operations not in its WSDL stub.
"""
import hashlib
import http.server
import socketserver
import datetime
import logging
import os
import re
import sys
import threading

log = logging.getLogger("tapo-onvif")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _env import load_dotenv
from _cameras import load_cameras

ENV  = load_dotenv(HERE)
CAMS = load_cameras(HERE)
LISTEN_HOST    = ENV.get("ONVIF_LISTEN_HOST", "0.0.0.0")
PUBLIC_HOST    = ENV.get("PUBLIC_HOST",       "127.0.0.1")  # MUST be reachable by clients
RTSP_PORT      = int(ENV.get("RTSP_PORT",     "8555"))
SNAP_PORT      = int(ENV.get("SNAPSHOT_PORT", "8683"))
RTSP_USER      = ENV.get("READ_USER",         "")
RTSP_PASS      = ENV.get("READ_PASS",         "")

if not RTSP_USER or not RTSP_PASS:
    sys.exit("ERROR: .env missing READ_USER / READ_PASS — these are the "
             "credentials advertised in the ONVIF/RTSP URL to UniFi etc.")

# Hard cap on the SOAP request body. Real ONVIF requests are <1 KB;
# anything larger is either misuse or an attempt to drive the parser
# into pathological memory use. Reject before allocating.
MAX_BODY_BYTES = 65536


def _virtual_camera(cam: dict, lens: dict) -> dict:
    """Build the per-(cam,lens) ONVIF descriptor served on its own port."""
    # Friendly name shown in UniFi/Scrypted before the user renames it.
    # Kept short (`<name>_<kind>`) — UniFi prepends the Manufacturer
    # ("TP-Link"), so a long Model string is doubly long in the UI.
    pretty = f"{cam['name']}_{lens['kind']}"
    # Stable UUID/SerialNumber derived from name+kind. Must be
    # deterministic: UniFi derives an internal MAC from the SerialNumber,
    # and homebridge-unifi-protect's `cameraOverrides[].mac` matches on
    # that MAC. Python's builtin hash() is randomized per process
    # (PYTHONHASHSEED), so using it here means the MAC changes on every
    # bridge restart and overrides silently stop matching — symptom is
    # HomeKit tiles working only for one lens because the user keeps
    # adding new override entries to chase the moving MAC.
    seed = f"{cam['name']}_{lens['kind']}".encode()
    h = int(hashlib.md5(seed).hexdigest(), 16)
    uuid = f"11111111-2222-3333-4444-{h % 10**12:012d}"
    return {
        "name": pretty,
        "model": cam["model"].upper(),
        "uuid": uuid,
        "snap_port": SNAP_PORT,
        "profiles": [
            {"token": "main_stream",
             "name": lens["kind"].capitalize(),
             "rtsp_path": "/" + lens["stream_path"],
             "snap_path": lens["snap_path"],
             "width": 1920, "height": 1080},
        ],
    }


# port → virtual-camera descriptor
CAMERAS = {
    lens["onvif_port"]: _virtual_camera(cam, lens)
    for cam in CAMS for lens in cam["lenses"]
}

# --------------------------------------------------------------------------
# SOAP helpers
# --------------------------------------------------------------------------
def envelope(body_xml: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<s:Envelope '
        'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
        'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        f'<s:Body>{body_xml}</s:Body></s:Envelope>'
    ).encode("utf-8")


def soap_fault(reason: str = "Method not implemented") -> bytes:
    return envelope(
        '<s:Fault><s:Code><s:Value>s:Sender</s:Value>'
        '<s:Subcode><s:Value>ter:ActionNotSupported</s:Value></s:Subcode></s:Code>'
        f'<s:Reason><s:Text xml:lang="en">{reason}</s:Text></s:Reason></s:Fault>'
    )


# Regex-only SOAP body inspection. Originally used xml.etree on the raw
# request bytes, but Python's stdlib XML parser expands internal DTD
# entities — i.e. a "billion laughs" payload could exhaust memory. We
# only need the first child element name of soap:Body to dispatch, so
# a regex skips the parser entirely. The capture is `\w+`, which means
# the result is always safe to drop into the SOAP fault we echo back.
_BODY_OPEN_RE = re.compile(rb"<(?:[\w.-]+:)?Body\b[^>]*>")
_FIRST_CHILD_TAG_RE = re.compile(rb"<\s*(?:[\w.-]+:)?(\w+)")
_PROFILE_TOKEN_RE = re.compile(
    rb"<\s*(?:[\w.-]+:)?ProfileToken\s*>([^<]+)</\s*(?:[\w.-]+:)?ProfileToken\s*>"
)


def parse_op(body: bytes) -> str:
    m = _BODY_OPEN_RE.search(body)
    if not m:
        return ""
    tag = _FIRST_CHILD_TAG_RE.match(body, m.end())
    if tag is None:
        # Try lstrip in case there's whitespace between <Body> and the op.
        rest = body[m.end():].lstrip()
        tag = _FIRST_CHILD_TAG_RE.match(rest)
    return tag.group(1).decode("ascii", errors="replace") if tag else ""


def _select_profile(cam: dict, body: bytes) -> dict:
    """Return the profile referenced by <ProfileToken>…</ProfileToken>
    in the request body, or the first profile if none/unknown. Always
    returns a profile from `cam['profiles']` — never user-controlled
    text — so callers can interpolate fields without escaping."""
    m = _PROFILE_TOKEN_RE.search(body)
    if m:
        token = m.group(1).decode("ascii", errors="replace").strip()
        for p in cam["profiles"]:
            if p["token"] == token:
                return p
    return cam["profiles"][0]


def now_utc(): return datetime.datetime.now(datetime.timezone.utc)


# --------------------------------------------------------------------------
# Operation handlers — each returns a SOAP body fragment (str)
# --------------------------------------------------------------------------
def op_GetSystemDateAndTime(cam, body):
    n = now_utc()
    return f"""<tds:GetSystemDateAndTimeResponse>
<tds:SystemDateAndTime><tt:DateTimeType>NTP</tt:DateTimeType>
<tt:DaylightSavings>false</tt:DaylightSavings>
<tt:TimeZone><tt:TZ>UTC0</tt:TZ></tt:TimeZone>
<tt:UTCDateTime>
  <tt:Time><tt:Hour>{n.hour}</tt:Hour><tt:Minute>{n.minute}</tt:Minute><tt:Second>{n.second}</tt:Second></tt:Time>
  <tt:Date><tt:Year>{n.year}</tt:Year><tt:Month>{n.month}</tt:Month><tt:Day>{n.day}</tt:Day></tt:Date>
</tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>"""


def op_GetCapabilities(cam, body):
    base = f"http://{PUBLIC_HOST}:{cam['port']}"
    return f"""<tds:GetCapabilitiesResponse><tds:Capabilities>
<tt:Device><tt:XAddr>{base}/onvif/device_service</tt:XAddr>
<tt:Network><tt:IPFilter>false</tt:IPFilter><tt:ZeroConfiguration>false</tt:ZeroConfiguration><tt:IPVersion6>false</tt:IPVersion6></tt:Network>
<tt:System><tt:DiscoveryResolve>false</tt:DiscoveryResolve><tt:DiscoveryBye>false</tt:DiscoveryBye></tt:System></tt:Device>
<tt:Media><tt:XAddr>{base}/onvif/media_service</tt:XAddr>
<tt:StreamingCapabilities><tt:RTPMulticast>false</tt:RTPMulticast><tt:RTP_TCP>true</tt:RTP_TCP><tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP></tt:StreamingCapabilities></tt:Media>
</tds:Capabilities></tds:GetCapabilitiesResponse>"""


def op_GetServices(cam, body):
    base = f"http://{PUBLIC_HOST}:{cam['port']}"
    return f"""<tds:GetServicesResponse>
<tds:Service><tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>
<tds:XAddr>{base}/onvif/device_service</tds:XAddr>
<tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version></tds:Service>
<tds:Service><tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>
<tds:XAddr>{base}/onvif/media_service</tds:XAddr>
<tds:Version><tt:Major>2</tt:Major><tt:Minor>5</tt:Minor></tds:Version></tds:Service>
</tds:GetServicesResponse>"""


def op_GetServiceCapabilities(cam, body):
    return """<tds:GetServiceCapabilitiesResponse>
<tds:Capabilities><tds:Network IPFilter="false" ZeroConfiguration="false" IPVersion6="false"/>
<tds:Security TLS1.1="false" TLS1.2="false" RESTAPI="false"/>
<tds:System DiscoveryResolve="false" DiscoveryBye="false"/></tds:Capabilities>
</tds:GetServiceCapabilitiesResponse>"""


def op_GetDeviceInformation(cam, body):
    return f"""<tds:GetDeviceInformationResponse>
<tds:Manufacturer>TP-Link</tds:Manufacturer>
<tds:Model>{cam['model']}</tds:Model>
<tds:FirmwareVersion>1.1.2</tds:FirmwareVersion>
<tds:SerialNumber>{cam['uuid']}</tds:SerialNumber>
<tds:HardwareId>1.0</tds:HardwareId></tds:GetDeviceInformationResponse>"""


def op_GetScopes(cam, body):
    base = "onvif://www.onvif.org"
    return f"""<tds:GetScopesResponse>
<tds:Scopes><tt:ScopeDef>Fixed</tt:ScopeDef><tt:ScopeItem>{base}/type/video_encoder</tt:ScopeItem></tds:Scopes>
<tds:Scopes><tt:ScopeDef>Fixed</tt:ScopeDef><tt:ScopeItem>{base}/Profile/Streaming</tt:ScopeItem></tds:Scopes>
<tds:Scopes><tt:ScopeDef>Configurable</tt:ScopeDef><tt:ScopeItem>{base}/name/{cam['name']}</tt:ScopeItem></tds:Scopes>
<tds:Scopes><tt:ScopeDef>Configurable</tt:ScopeDef><tt:ScopeItem>{base}/hardware/Tapo</tt:ScopeItem></tds:Scopes>
</tds:GetScopesResponse>"""


def op_GetNetworkInterfaces(cam, body):
    return """<tds:GetNetworkInterfacesResponse>
<tds:NetworkInterfaces token="eth0"><tt:Enabled>true</tt:Enabled>
<tt:Info><tt:Name>eth0</tt:Name><tt:HwAddress>02:00:00:00:00:00</tt:HwAddress><tt:MTU>1500</tt:MTU></tt:Info>
</tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse>"""


def op_GetEndpointReference(cam, body):
    return f"""<tds:GetEndpointReferenceResponse>
<tds:GUID>urn:uuid:{cam['uuid']}</tds:GUID></tds:GetEndpointReferenceResponse>"""


def op_GetWsdlUrl(cam, body):
    return "<tds:GetWsdlUrlResponse><tds:WsdlUrl>http://www.onvif.org/onvif/ver10/wsdl</tds:WsdlUrl></tds:GetWsdlUrlResponse>"


def _profile_xml(p):
    return f"""<trt:Profiles fixed="true" token="{p['token']}">
<tt:Name>{p['name']}</tt:Name>
<tt:VideoSourceConfiguration token="VideoSrcConfig">
<tt:Name>VideoSource</tt:Name><tt:UseCount>2</tt:UseCount><tt:SourceToken>VideoSrc</tt:SourceToken>
<tt:Bounds x="0" y="0" width="{p['width']}" height="{p['height']}"/>
</tt:VideoSourceConfiguration>
<tt:VideoEncoderConfiguration token="enc_{p['token']}">
<tt:Name>{p['name']}Enc</tt:Name><tt:UseCount>1</tt:UseCount>
<tt:Encoding>H264</tt:Encoding>
<tt:Resolution><tt:Width>{p['width']}</tt:Width><tt:Height>{p['height']}</tt:Height></tt:Resolution>
<tt:Quality>4</tt:Quality>
<tt:RateControl><tt:FrameRateLimit>15</tt:FrameRateLimit><tt:EncodingInterval>1</tt:EncodingInterval><tt:BitrateLimit>3000</tt:BitrateLimit></tt:RateControl>
<tt:H264><tt:GovLength>30</tt:GovLength><tt:H264Profile>Main</tt:H264Profile></tt:H264>
<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address></tt:Address><tt:Port>0</tt:Port><tt:TTL>1</tt:TTL><tt:AutoStart>false</tt:AutoStart></tt:Multicast>
<tt:SessionTimeout>PT60S</tt:SessionTimeout>
</tt:VideoEncoderConfiguration></trt:Profiles>"""


def op_GetProfiles(cam, body):
    profiles = "".join(_profile_xml(p) for p in cam["profiles"])
    return f"<trt:GetProfilesResponse>{profiles}</trt:GetProfilesResponse>"


def op_GetProfile(cam, body):
    p = _select_profile(cam, body)
    return f"<trt:GetProfileResponse><trt:Profile fixed=\"true\" token=\"{p['token']}\"><tt:Name>{p['name']}</tt:Name></trt:Profile></trt:GetProfileResponse>"


def op_GetVideoSources(cam, body):
    p = cam["profiles"][0]
    return f"""<trt:GetVideoSourcesResponse>
<trt:VideoSources token="VideoSrc"><tt:Framerate>15</tt:Framerate>
<tt:Resolution><tt:Width>{p['width']}</tt:Width><tt:Height>{p['height']}</tt:Height></tt:Resolution>
</trt:VideoSources></trt:GetVideoSourcesResponse>"""


def op_GetVideoEncoderConfigurations(cam, body):
    out = ""
    for p in cam["profiles"]:
        out += f"""<trt:Configurations token="enc_{p['token']}"><tt:Name>{p['name']}Enc</tt:Name>
<tt:UseCount>1</tt:UseCount><tt:Encoding>H264</tt:Encoding>
<tt:Resolution><tt:Width>{p['width']}</tt:Width><tt:Height>{p['height']}</tt:Height></tt:Resolution>
<tt:Quality>4</tt:Quality></trt:Configurations>"""
    return f"<trt:GetVideoEncoderConfigurationsResponse>{out}</trt:GetVideoEncoderConfigurationsResponse>"


def op_GetVideoSourceConfigurations(cam, body):
    p = cam["profiles"][0]
    return f"""<trt:GetVideoSourceConfigurationsResponse>
<trt:Configurations token="VideoSrcConfig"><tt:Name>VideoSource</tt:Name>
<tt:UseCount>2</tt:UseCount><tt:SourceToken>VideoSrc</tt:SourceToken>
<tt:Bounds x="0" y="0" width="{p['width']}" height="{p['height']}"/>
</trt:Configurations></trt:GetVideoSourceConfigurationsResponse>"""


def op_GetStreamUri(cam, body):
    p = _select_profile(cam, body)
    uri = f"rtsp://{RTSP_USER}:{RTSP_PASS}@{PUBLIC_HOST}:{RTSP_PORT}{p['rtsp_path']}"
    return f"""<trt:GetStreamUriResponse><trt:MediaUri>
<tt:Uri>{uri}</tt:Uri>
<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
<tt:Timeout>PT60S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>"""


def op_GetSnapshotUri(cam, body):
    p = _select_profile(cam, body)
    uri = f"http://{PUBLIC_HOST}:{cam['snap_port']}{p['snap_path']}"
    return f"""<trt:GetSnapshotUriResponse><trt:MediaUri>
<tt:Uri>{uri}</tt:Uri>
<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
<tt:Timeout>PT60S</tt:Timeout></trt:MediaUri></trt:GetSnapshotUriResponse>"""


HANDLERS = {
    "GetSystemDateAndTime":           op_GetSystemDateAndTime,
    "GetCapabilities":                op_GetCapabilities,
    "GetServices":                    op_GetServices,
    "GetServiceCapabilities":         op_GetServiceCapabilities,
    "GetDeviceInformation":           op_GetDeviceInformation,
    "GetScopes":                      op_GetScopes,
    "GetNetworkInterfaces":           op_GetNetworkInterfaces,
    "GetEndpointReference":           op_GetEndpointReference,
    "GetWsdlUrl":                     op_GetWsdlUrl,
    "GetProfiles":                    op_GetProfiles,
    "GetProfile":                     op_GetProfile,
    "GetVideoSources":                op_GetVideoSources,
    "GetVideoEncoderConfigurations":  op_GetVideoEncoderConfigurations,
    "GetVideoSourceConfigurations":   op_GetVideoSourceConfigurations,
    "GetStreamUri":                   op_GetStreamUri,
    "GetSnapshotUri":                 op_GetSnapshotUri,
}


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
def make_handler(cam):
    class OnvifHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            if not self.path.startswith("/onvif/"):
                return self._send(404, b"")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400, b"")
            if length < 0 or length > MAX_BODY_BYTES:
                # 413 Payload Too Large. Reject before reading so a
                # hostile client can't pin RAM with a huge body.
                return self._send(413, b"")
            body = self.rfile.read(length) if length else b""
            op = parse_op(body)
            handler = HANDLERS.get(op)
            if handler is None:
                # `op` is matched by `\w+` in parse_op so it's safe to
                # interpolate — no XML escaping needed.
                resp = soap_fault(f"Operation '{op}' not supported")
                code = 200
            else:
                try:
                    resp = envelope(handler({**cam, "port": self.server.server_port}, body))
                    code = 200
                except Exception:
                    # Don't echo exception text to the client (could
                    # leak paths, internals). Log it server-side.
                    log.exception("ONVIF handler %s raised", op)
                    resp = soap_fault("Internal error")
                    code = 500
            self._send(code, resp, "application/soap+xml; charset=utf-8")

        def do_GET(self):
            return self._send(200, b"OK\n", "text/plain")

        def _send(self, code, body, ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body: self.wfile.write(body)

        def log_message(self, *_a, **_k): pass
    return OnvifHandler


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port, cam):
    srv = ThreadedHTTPServer((LISTEN_HOST, port), make_handler(cam))
    print(f"ONVIF :{port} → {cam['name']} ({len(cam['profiles'])} profile/s)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    threads = []
    for port, cam in CAMERAS.items():
        t = threading.Thread(target=serve, args=(port, cam), daemon=True)
        t.start()
        threads.append(t)
    for t in threads: t.join()
