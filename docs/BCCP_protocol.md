# Tapo BCCP Protocol — Reverse-Engineered Findings

Decoded 2026-05-02 from `apk_unzipped/classes13.dex` via androguard bytecode
analysis of `com.tplink.libtapoiotble` and `com.tplink.libtapoiotapi.repository.bccp`.

## Confirmed (high confidence)

### GATT layer (verified against real C675D in factory mode)
| Role | UUID | Notes |
|---|---|---|
| Service | `00008642-0000-1000-8000-00805f9b34fb` (16-bit `0x8642`) | advertised by cam |
| Write | `0x8885` | `[write, write-without-response]`, CCCD descriptor present but `start_notify()` returns ATT err 6 — no notify |
| Read | `0x8884` | `[read]`, descriptor `0x2901 = "TP-LINK read characteristic"` — response polling |
| Adv name | `C675D_<8-char-suffix>` | suffix changes after first connect to ` ` (space) |

### BCCP packet header — 18 bytes, big-endian, source `BCCPHeader.pack()`
```
+0   mainVer        (1 byte)   ┐
+1   secondVer      (1 byte)   ├ BCCPGeneralHeader
+2   controlCode    (1 byte)   │
+3   reasonCode     (1 byte)   ┘
+4   payloadLen     (2 bytes BE)
+6   flags          (1 byte)
+7   errCode        (1 byte)
+8   serialNum      (4 bytes BE)
+12  checksum       (4 bytes BE)
+16  businessType   (1 byte)
+17  businessVersion(1 byte)
```
Followed by `payloadLen` bytes of payload (encrypted post-handshake; plain JSON during handshake).

### SRP-6a parameters — source `srp/e.<init>` and `SRPCalculator.<clinit>`
- **Group**: RFC 5054 group #15 = **MODP-3072** (RFC 3526). Constant
  `"rfc5054_3072"` is referenced via `BouncyCastle.SRP6StandardGroups.rfc5054_3072`.
  Prime starts `FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B…`.
- **Generator g** = `0x05` (HomeKit pattern, NOT RFC 5054's g=2).
- **Hash** = **SHA-512** (output buffer is `new-array v2, 64, [B`; padding to 384 bytes = 3072 bits in `srp/d.j`).
- 4-message handshake M1 → M4. Field names in JSON payloads:
  - **M1** (client→cam): `{state, type, si, un, comp}`  — `un` = username
  - **M2** (cam→client): `{state, type, si, salt, pk, comp}` — `salt`, `pk` = B (server pub, hex)
  - **M3** (client→cam): `{state, type, pk, proof}` — `pk` = A (client pub, hex), `proof` = M1 evidence
  - **M4** (cam→client): `{state, type, si, proof}` — `proof` = M2 evidence

### Post-handshake encryption — source `srp/c` + `srp/a`
- **AEAD = ChaCha20-Poly1305** (Java `Cipher.getInstance("ChaCha20-Poly1305")`).
  On Android API <29, falls back to BouncyCastle's `ChaChaPoly1305Engine`.
- **Two derived keys** via HKDF-SHA-512 over the SRP shared key K:
  - `app_key` = HKDF(K, salt=`"SplitSetupSalt"`, info=`"AppEncryptControl"`, len=32)
  - `dut_key` = HKDF(K, salt=`"SplitSetupSalt"`, info=`"DutEncryptControl"`, len=32)
  - DUT = "Device Under Test" → cam-side
- 12-byte nonce per IV (BCCP packet-relative; serialNum likely participates).

## Inferred / partial (medium confidence)

### Orchestrator class hierarchy
- Entry point: `BLEQuickSetupRepository.openBluetoothBCCPConnect`
- Handshake driver: `BCCPBLEConnectStatusRepository.startBccpHandshake`
- Wraps an `IoTBLEManager` that owns the GATT client.

### `isDefaultPassword` flag
- Boolean field on (multiple) device-context classes (e.g. `Lcj0/b;->h0()`, `Lef0/b;->h0()`, etc.).
- Strongly suggests a **factory-default password mode** the cam accepts during initial pairing — but the actual default-password VALUE is not a plain `const-string` we found.
- Likely candidates (none yet validated):
  - A constant baked into a method we haven't found yet
  - Derived from deviceId/MAC/serial via a hash function
  - Read from cam via a pre-handshake BLE read on `0x8884`
  - Stored in a separate dex (`classes11.dex` also references `BCCPBLEConnectStatusRepository`)

## Resolved through jadx + live test (2026-05-02 round 2)

All five "unknowns" listed below were resolved:

- BCCPHeader byte-zero defaults: all bytes 0 except payloadLen (BE short, = jsonBytes.length + 2) and checksum (CRC32, computed over the packet with placeholder `0x5A6B7C8D` substituted in for bytes 12-15). Source: [`w80/u0.S()`](apk_unzipped/jadx_out/sources/w80/u0.java#L361) and [`x80/a.java`](apk_unzipped/jadx_out/sources/x80/a.java).
- M1 JSON shape: `{"method":"login","params":{"type":0,"state":1,"un":"admin","comp":1}}` — Gson omits null fields by default, so `si` is absent on first connection.
- BCCP state/type integer values: `STATE_M1=1, STATE_M2=2, STATE_M3=3, STATE_M4=4, TYPE_DEVICE_HANDSHAKE=0, COMP_VERSION=1, USERNAME="admin"` — all from [`x80/a.java`](apk_unzipped/jadx_out/sources/x80/a.java).
- Checksum algorithm: `java.util.zip.CRC32` (== `zlib.crc32` in Python).
- Response transport: poll-read of `0x8884`. The Android app polls every 50ms (l.java:38). Notify subscribe fails on both characteristics (`ATT err 6 "request not supported"`).

## SRP password formula (per dex)

```
SRP_password = SHA-256( SHA-1(localUserName) || SHA-1(cloud_password) )    # 32 raw bytes
```

Where `localUserName` and `cloud_password` come from the TP-Link cloud account (`c50.a.k()` and `c50.a.l()` respectively). For our cam: `localUserName ≈ "your.cloud@example.com"`, cloud_password from `.env`. Source: [`w80/u0.c0()`](apk_unzipped/jadx_out/sources/w80/u0.java#L550).

## NEW BLOCKER (2026-05-02 live test) — firmware uses KLAP, not SRP

Sending a correct BCCPHandshakeM1 packet (above) elicited a clean BCCP-wrapped JSON response from the cam — **transport works**. But the inner JSON is a **KLAP1 v3 session-expired error**, NOT the SRP M2 the dex code expects:

```json
{"result":{"data":{"code":-40401,"time":9,"max_time":10,"encrypt_type":["3"],"key":"MFwwDQYJ…<base64 RSA pubkey>…"}}}
```

`-40401 = CameraErrorCode.SESSION_EXPIRED`. `time:9, max_time:10` are attempt counters (10 attempts before lockout). The cam expects the **same KLAP1 v3 handshake used over WiFi port 443**, just transported over BCCP/BLE:

1. Round 1: `{"method":"login","params":{"username":"admin","cnonce":"<16 bytes hex>","encrypt_type":"3"}}` → cam returns nonce + RSA pubkey + device_confirm
2. Round 2: `{"method":"login","params":{"digest_passwd":"<sha256(...)>"}}` → cam returns stok session token
3. All subsequent requests wrapped in `{"method":"securePassthrough","params":{"request":"<AES-CBC base64>"}}`

This is the EXACT same KLAP protocol pytapo speaks over WiFi. The dex's BCCP/SRP scaffolding (BCCPHandshakeM*, SRPCalculator) is either legacy or used only after a fresh device_password has been set via KLAP first.

**This means the original blocker resurfaces**: the KLAP digest_passwd requires `device_password` — the per-cam ~16-char string we've been trying to recover all along. With 10 attempts before lockout, blind brute-forcing on the cam directly is the wrong path.

## Round 1 captured (2026-05-02 live)

```
cnonce         = E047930FC14324B4   (we sent)
nonce          = D0BB39E6AB41CB46   (cam's challenge)
device_confirm = B9C145D7EBB7E292BB8A70A707528F1E021342E509EA9150FA72333987AFCDC9
                 D0BB39E6AB41CB46 E047930FC14324B4
                 ──────64-hex SHA256─────────────── nonce            cnonce
RSA pubkey     = MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAPdqOpwGI7rFGBYixqJS+ZwTzswO
                 DzhkqRS9g109HvLcpUz/pie8JMdl5h9oXJ1K3CDaTMDZE53ebwjwNVgxX/k=
outer error    = -40413 INVALID_NONCE  (regular login flow, NOT FFS)
inner code     = -40401 SESSION_EXPIRED
attempts       = 8/10 remaining
```

Now wired into [`tapo_check_password.py`](tapo_check_password.py) as a 5th constraint
alongside the 4 captured WiFi tuples.

## Two cracking paths now available

### Path 1 — keep brute-forcing
Run [`tapo_crack.py`](tapo_crack.py) with wider lists. Adding the BLE constraint doesn't reduce the search space, but provides a 2nd-formula confirmation.

### Path 2 — LAT (Local Access Token) — INVESTIGATED, dead end
Cams support TWO password modes (LocalSecureSession.A:53-73):
- `TP_PASSWORD` — uses the cloud account password
- `LAT` — uses a randomly-generated Local Access Token

`SecureUtils__SecureUtilsKt.r()` (`isDeviceUseTPPassword`) discriminates: if `SHA-256(cnonce + SHA-256(cloud_pw) + nonce)` matches the device_confirm prefix, mode = TP_PASSWORD; else LAT. **We tested our cam against the cloud password — no match — so our cam is in LAT mode.**

LAT generation flow (yi.java:5184, `syncDevicePassword(isLat=true, str)`):
1. iOS app generates a random LAT (`str`)
2. Calls `change_admin_password` over cloud passthrough to TELL the cam its new admin password = LAT
3. Stores LAT locally in iOS Keychain
4. Cloud merely forwards the call; **does NOT cache the LAT itself**

So "fetch LAT from cloud" doesn't work — the cloud doesn't have it. The LAT only exists on the iPhone Keychain (or, equivalently, on the cam itself in NVRAM). Same blocker as before.

## Path 3 — change the cam's LAT to one we choose, via BLE

The iOS app changes the cam's admin password by calling `change_admin_password` (Method.CHANGE_ADMIN_PASSWORD in ai.java:440) **through the cloud passthrough channel**. That channel is only available while the cam is on WiFi and reachable from `eu-wap.tplinkcloud.com`. Our cam is offline cloud-side.

**But** — `change_admin_password` is just a request method on the cam. If we can reach the cam via BLE/BCCP (we proved we can) and the cam exposes the same method via BLE before login completes, we could send it without knowing the old password.

Realistically, the cam will require auth via `secure_pass_through` first, which itself needs the current admin password. So no — this path also requires the current LAT.

## Path 4 — accept the blocker and pivot

The fundamental constraint: the LAT is a per-cam ~16-char random string stored ONLY in iOS Keychain on the iPhone that did the original onboarding. Recovery options:

1. **Re-do iOS Keychain Access search on Mac** — make absolutely sure iCloud Keychain sync is enabled (System Settings → Apple ID → iCloud → Passwords & Keychain) on BOTH the iPhone and the Mac, then search Keychain Access for `Tapo`, `tplink`, `c675`, the cam's MAC `AA:BB:CC:DD:EE:FF`, the deviceId, etc. The Tapo app may sync some entries even though prior searches turned up empty.
2. **Cooperative iOS app debugging** — without a jailbreak: install the Tapo IPA on a sideloaded test device (e.g. via AltStore/Sideloadly), use Frida-iOS-Dump or similar to inspect Keychain contents.
3. **Hardware swap** — cam isn't precious; Reolink/Amcrest with native ONVIF eliminates the entire problem.
4. **PacketLogger BLE capture during a fresh iOS pairing** — once we have an UNBOUND cam (proper factory reset), an iOS pairing with PacketLogger+Proxyman captures the LAT being generated and pushed. This gives us the LAT directly, and validates our BCCP/KLAP implementation end-to-end.

## FFSDefaultPwd (different but related)

Not what our cam uses. Formula was discovered:
```
FFSDefaultPwd = localUserName + "_" + h.a(mac)
```
Triggered only when cam returns `error_code = -90000 (FFS_NONE_PWD)` or `-90001 (TSS_NONE_PWD)`. Our cam returned `-40413 (INVALID_NONCE)` so it's in regular auth, not FFS.

## Why we stopped manual RE here

Each remaining unknown lives behind multiple layers of:
- Kotlin coroutine continuations (synthetic `$1`, `$2`, `$3` inner classes)
- RxJava chained operators
- Obfuscated single-letter classnames in 16 dex files

Resolving them by static analysis is multi-day work with high error risk.

## What ends this in 5 minutes

A **single iOS pairing run with PacketLogger + Proxyman simultaneously capturing**:
- PacketLogger's HCI capture gives every BLE write/read in order, with timing → BCCP header bytes (controlCode, businessType, state, type, etc.) decoded directly.
- Proxyman captures the cam's onboarding HTTPS to the cloud (we already proved `setUserAccount` is called) → reveals the device-local password the app generates.
- The two combined collapse all five remaining unknowns at once.

This is the recommendation in the README's "Realistic ways forward" path 3.
