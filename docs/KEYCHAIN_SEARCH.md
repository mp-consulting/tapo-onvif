# Tapo C675D — iOS Keychain Search for Device LAT

The C675D's local admin password is a randomly-generated **LAT** that the iOS Tapo app stored in the iPhone's Keychain at first onboarding. If iCloud Keychain sync is on, it should also be on the Mac. Prior searches found nothing — most likely because the sync was off, or the search terms missed.

## Pre-flight (do these first, in order)

### 1. Confirm iCloud Keychain sync is ON, on BOTH devices

**iPhone** (the one that did the Tapo onboarding):
- Settings → tap your name → iCloud → Passwords & Keychain → toggle ON
- Wait 1–2 minutes for sync to settle if it was just turned on

**Mac**:
- System Settings → tap your name → iCloud → Passwords & Keychain → toggle ON
- Wait 1–2 minutes

### 2. Force a sync nudge
On iPhone: open Settings → Passwords. On Mac: open Passwords app (the new one, replaces Keychain Access for iCloud-synced items). Just opening these triggers a sync pull.

### 3. Open BOTH search apps on the Mac
The Tapo LAT could be in either:
- **Passwords app** (`/System/Applications/Passwords.app`) — iCloud-synced web/app passwords. Likely target.
- **Keychain Access** (`/System/Library/CoreServices/Applications/Keychain Access.app`) — local-only keychain entries (login keychain, system keychain). Older app passwords sometimes here.

## Search terms to try

For each term, run it in the search box of BOTH apps. Look at every result, even if "Account" looks unrelated — Tapo often files under generic names.

| Term | Why |
|---|---|
| `tapo` | brand string — most obvious |
| `tplink` | older product brand, may be the entry name |
| `tp-link` | with hyphen |
| `c675d` | model |
| `AA:BB:CC:DD:EE:FF` | the cam's WiFi MAC, sometimes used as identifier |
| `58044F628127` | MAC without colons |
| `<DEVICE_ID>` | the deviceId from `getDeviceList` |
| `your.cloud@example.com` | your cloud account — the entry may be filed under your username |
| `iot` | TP-Link's umbrella name for these |
| `mihome` / `kasa` | not Tapo, but worth a sanity check |

## What a hit looks like

The LAT is `~16 chars`, mixed-case alphanumeric, often with hyphens or underscores. Example shape: `Ab3cD-eFgH-iJkLm`. If the password field shows something matching that pattern next to a Tapo-related entry name, that's it.

There may also be **multiple entries per cam** — one for the cloud account, one for the device's local password, possibly a separate entry for media-stream credentials. Check every Tapo-tagged entry's password field.

## Test a candidate against the captured device_confirm

Once you find anything plausible, test it offline before risking a BLE attempt:

```sh
.venv/bin/python tapo_check_ble.py "candidate-here"
```

A match prints `*** MATCH: 'candidate-here'` and means we found the LAT. Plug into [`tapo_pair_ble.py`](tapo_pair_ble.py) and the cam pairs over BLE.

## If still no hit

Two interpretations:
1. **iOS doesn't sync this Keychain item to iCloud** — many iOS apps explicitly mark their passwords `kSecAttrSynchronizableNo`. In that case the LAT only exists on the iPhone itself, never on the Mac.
2. **The Tapo app stores it as a generic "internet password" with a non-obvious account name** — try Keychain Access → File → New Search → look at ALL results sorted by date, around the original onboarding date (per cloud `lastBindTime` = 2026-04-29).

If (1), the only way to extract is on the iPhone directly: jailbreak, sideloaded debug build, or Frida-iOS-Dump. Or pivot to PacketLogger capture (Path 4).
