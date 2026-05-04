# Path 4 — PacketLogger BLE Capture During iOS Pairing

Capture the iPhone's BLE traffic while the Tapo app pairs with the C675D over BCCP. The capture lets us decode the LAT being pushed to the cam (or, failing that, lets us brute-force it offline against multiple constraints).

## One-time setup

### 1. Get PacketLogger.app (free, official Apple tool)

PacketLogger ships inside "Additional Tools for Xcode" — a free download from Apple Developer. **Does NOT require a paid developer account, just an Apple ID.**

- https://developer.apple.com/download/all/?q=Additional%20Tools (sign in with your Apple ID)
- Download "Additional Tools for Xcode 16" (or whichever Xcode version matches your macOS)
- Open the downloaded `.dmg`
- Drag `Hardware/PacketLogger.app` into `/Applications/`

### 2. Install Apple's Bluetooth Logging profile on your iPhone

Without this profile, PacketLogger sees only the Mac's BLE radio, not the iPhone's. The profile tells iOS to dump HCI events over USB.

- On the iPhone, open Safari and go to: **https://developer.apple.com/bug-reporting/profiles-and-logs/**
- Tap "Bluetooth" under "Profiles and Logs"
- Tap "Install" twice in Settings → General → VPN & Device Management → Bluetooth profile
- **Reboot the iPhone** (required — the profile only activates after reboot)

### 3. Tether the iPhone to the Mac via USB
Use a Lightning/USB-C cable. Trust the Mac if prompted on the phone.

## The capture run

1. **Pre-flight** — make sure cam is in pairing mode (LED solid red, then alternating red↔green). If it isn't, factory-reset it now: hold reset button until the LED is solid red, **keep holding** until it alternates red↔green.

2. **Open PacketLogger.app** on the Mac. Top-left corner has a target dropdown. Select your **iPhone** by name (not "macOS Bluetooth Stack").

3. Hit the **record** button (round red dot, top-left). PacketLogger now captures every BLE event from the iPhone in real time.

4. **On the iPhone**: open the Tapo app → tap "+" or "Add Device" → "Camera" → "C675D". Follow the on-screen flow. The app will:
   - Scan for the cam over BLE
   - Connect via BCCP
   - Run KLAP1 v3 Round 1 + Round 2
   - Push the LAT and your WiFi credentials to the cam
   - Cam reboots onto your WiFi

5. **Wait for the iOS app to confirm the cam is online**, then stop the capture (red dot again).

6. **Export the capture**: File → Save As… → `tapo_pairing.pklg`. Then File → Export → "As Text…" → save as `tapo_pairing.txt`. (Both formats are useful; the .txt is what our decoder reads.)

## Decode

```sh
cd /Users/mickael/Desktop/tapo.nosync
.venv/bin/python tapo_decode_ble_capture.py tapo_pairing.txt
```

Expected output: cnonce, nonce, device_confirm, digest_passwd, stok. The decoder will also tell you which constraint(s) it captured — at minimum we get a fresh device_confirm we can brute-force; ideally we get the digest_passwd too which lets the cracker rule out 100% of false positives.

## What to do after capture (depending on what the decoder finds)

| Capture content | Implication | Next step |
|---|---|---|
| Round 1 only | iOS aborted or our parsing missed Round 2. We have one digest formula to crack against. | Re-run capture; check decoder's `--show-encrypted` flag. |
| Round 1 + Round 2 + stok | Pairing succeeded. The cam now has the LAT iOS just generated. | Brute-force LAT against device_confirm AND digest_passwd. Or: pull from Keychain (now-fresh entry). |
| Round 1 + Round 2 but no stok | KLAP failed (wrong password or stale challenge). Cam will lock out after 10. | Investigate — likely iOS app talked to an unexpected endpoint. |

## Notes / caveats

- **The cam will be re-bound to your cloud account after a successful iOS pairing.** That's fine — `tapo_pair_ble.py` and the cracker still work because we now have known-good cnonce/nonce/digest tuples.
- **iCloud Keychain sync may finally populate the LAT on the Mac after a fresh pairing**, even if it didn't before — worth re-running the steps in [`KEYCHAIN_SEARCH.md`](KEYCHAIN_SEARCH.md) immediately after the pairing succeeds.
- **If you don't want the cam re-bound to your cloud account**, do the pairing using a SECONDARY Apple ID / Tapo account on the iPhone. The BLE protocol is the same; only the "owner" cloud record changes.
- **If PacketLogger says no iPhone target appears**, the most common causes (in order): profile not installed → installed but iPhone not rebooted → cable is charge-only (use a known data cable). Re-do step 2.
