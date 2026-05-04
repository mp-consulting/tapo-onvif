#!/usr/bin/env python3
"""Minimal Python ONVIF Profile S server for the Tapo C675D bridge.

Hand-written SOAP responses for the ~15 ONVIF operations that UniFi Protect,
Scrypted, Homebridge, and generic ONVIF clients actually use. Returns proper
SOAP faults for unknown operations (no HTTP 500 / TypeError).

Replaces daniela-hase/onvif-server (Node.js + soap@1.1.5) which crashed with
`TypeError: Cannot read properties of undefined (reading 'description')` on
any operation not in its WSDL stub.

Two virtual ONVIF cameras are served (one per port):
  http://<host>:8081/onvif/device_service     wide lens
  http://<host>:8082/onvif/device_service     tele lens
Each camera's media service is at /onvif/media_service on the same port.
"""
import http.server
import socketserver
import datetime
import os
import re
import threading
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path: str) -> dict:
    out: dict = {}
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


ENV = load_dotenv(os.path.join(HERE, ".env"))
LISTEN_HOST    = ENV.get("ONVIF_LISTEN_HOST", "0.0.0.0")
PUBLIC_HOST    = ENV.get("PUBLIC_HOST",       "127.0.0.1")  # MUST be reachable by clients
RTSP_PORT      = int(ENV.get("RTSP_PORT",     "8555"))
SNAP_PORT      = int(ENV.get("SNAPSHOT_PORT", "8683"))
RTSP_USER      = ENV.get("PUBLISH_USER",      "publish")
RTSP_PASS      = ENV.get("PUBLISH_PASS",      "publish")
ONVIF_WIDE_PORT = int(ENV.get("ONVIF_WIDE_PORT", "8081"))
ONVIF_TELE_PORT = int(ENV.get("ONVIF_TELE_PORT", "8082"))

CAMERAS = {
    ONVIF_WIDE_PORT: {
        "name": "TapoC675D-Wide",
        "uuid": "11111111-2222-3333-4444-555555555551",
        "snap_port": SNAP_PORT,
        "profiles": [
            {"token": "main_stream", "name": "Wide", "rtsp_path": "/c675d_wide",
             "snap_path": "/wide", "width": 1920, "height": 1080},
        ],
    },
    ONVIF_TELE_PORT: {
        "name": "TapoC675D-Tele",
        "uuid": "11111111-2222-3333-4444-555555555552",
        "snap_port": SNAP_PORT,
        "profiles": [
            {"token": "main_stream", "name": "Tele", "rtsp_path": "/c675d_tele",
             "snap_path": "/tele", "width": 1920, "height": 1080},
        ],
    },
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


def parse_op(body: bytes) -> str:
    try:
        root = ET.fromstring(body)
        body_el = root.find("{http://www.w3.org/2003/05/soap-envelope}Body")
        if body_el is None or len(body_el) == 0: return ""
        return body_el[0].tag.split("}")[-1]
    except Exception:
        return ""


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
<tds:Model>{cam['name']}</tds:Model>
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
    m = re.search(r"<\w*:?ProfileToken>([^<]+)</\w*:?ProfileToken>", body.decode("utf-8", errors="ignore"))
    token = m.group(1) if m else cam["profiles"][0]["token"]
    p = next((x for x in cam["profiles"] if x["token"] == token), cam["profiles"][0])
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
</tt:Configurations></trt:GetVideoSourceConfigurationsResponse>"""


def op_GetStreamUri(cam, body):
    m = re.search(r"<\w*:?ProfileToken>([^<]+)</\w*:?ProfileToken>", body.decode("utf-8", errors="ignore"))
    token = m.group(1) if m else cam["profiles"][0]["token"]
    p = next((x for x in cam["profiles"] if x["token"] == token), cam["profiles"][0])
    uri = f"rtsp://{RTSP_USER}:{RTSP_PASS}@{PUBLIC_HOST}:{RTSP_PORT}{p['rtsp_path']}"
    return f"""<trt:GetStreamUriResponse><trt:MediaUri>
<tt:Uri>{uri}</tt:Uri>
<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
<tt:Timeout>PT60S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>"""


def op_GetSnapshotUri(cam, body):
    m = re.search(r"<\w*:?ProfileToken>([^<]+)</\w*:?ProfileToken>", body.decode("utf-8", errors="ignore"))
    token = m.group(1) if m else cam["profiles"][0]["token"]
    p = next((x for x in cam["profiles"] if x["token"] == token), cam["profiles"][0])
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
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            op = parse_op(body)
            handler = HANDLERS.get(op)
            if handler is None:
                resp = soap_fault(f"Operation '{op}' not supported")
                code = 200
            else:
                try:
                    resp = envelope(handler({**cam, "port": self.server.server_port}, body))
                    code = 200
                except Exception as e:
                    resp = soap_fault(f"Internal error: {e}")
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
