# Browser UI Security
This contains ideas for how to secure an internet exposed version of Aunic that lets users remotely work on their desktop.

## Basics
- login username and password to access the site from anywhere other than localhost
- Can only create/open Markdown files.
- Workspace root configuration required for browser mode
- All browser tools ask by default.
    - bash deny by default
- Only localhost can change security settings
- Only localhost can change workspace root
- HTTPS required off-localhost
- Audit log with notifications for suspicious logins
- Secrets server-side only
## Other Features
- Hard path jail enforcement
    - no symlink escape
    - no path traversal
    - no “open file by absolute path outside workspace”
    - this is one of the most important parts
- First time setup portal asking user to configure security settings
    - should also inform and provide resources about isolating/containerizing and things like VPNs/Tailscale
- Allow Permission (as well as file creation/deletion) is password protected (think like sudo)
    - 10 minute elevation period then re-enter
    - can't be login password
    - rate limited
    - hashed at rest
    - can be turned off for LAN, not for WAN
- Device Allowlist
    - user registers device
    - server issues a device-bound long-lived credential/certificate/token
    - future access requires both account auth and trusted device token
        - On a practical level, this can be:
            - signed device token in secure storage
            - revocable from WAN, not issuable from WAN
    - **iOS PWA limitation:** PWAs cannot access Keychain — tokens live in localStorage/IndexedDB, which are extractable on iOS. This makes PWA device tokens "soft trust" (fraud deterrence, not hard security). Consider:
        - Short token lifespans + refresh
        - Requiring native apps for hard device trust
        - Binding tokens to IP/hardware fingerprint as secondary check
        - Server-side revocation on suspicious activity
- Add localhost-only bootstrap mode
    - First-time setup must happen on localhost
    - Until bootstrap completes:
        - no remote access
    - Bootstrap should require:
        - create admin account
        - set workspace root
        - set elevation secret
        - optionally register first trusted device
    - That is one of the best creator-controlled safety measures you can add.
- Add remote-mode safety profiles
    - localhost mode
    - LAN mode
    - internet mode
- Each mode should tighten defaults automatically.
    - localhost:
        - looser, optional HTTPS
    - LAN:
        - recommend HTTPS don't force it, trusted device optional
    - internet:
        - HTTPS mandatory
        - trusted device must be explicitly disabled (with lots of scary pop-ups saying don't)
        - elevation required
        - bash hard-disabled by default (with lots of scary pop-ups saying don't enable)
- security settings localhost-only

## Things to consider
- some sort of "percentage changed" or "change rate-limit" Auth checks, to stop 50% of files being deleted, etc.
- Origin lock / allowed origin list
    - browser requests should only be accepted from configured origins
    - helps reduce accidental exposure/misconfiguration
- Destructive-op interlocks
    - delete/rename/write outside existing files should require elevation
    - maybe bulk edits too
    - “ask” alone is weaker than “ask + elevated session”
    - Security event banner
- on login from a new device
    - repeated denied elevation attempts
    - access from new geo/IP region if you choose to log that
changes to security settings
workspace root changes
Safe browser-mode tool partition
I would seriously consider making browser mode a stricter environment than TUI/CLI by design:
note_edit, note_write, web_search, web_fetch: normal
read, glob, grep, list: ask
edit, write: ask + elevation
delete/create directory: ask + elevation
bash: deny by default, maybe even hidden unless explicitly enabled on localhost
That is a strong product decision and a good one.

What I would not rely on

IP allowlists as primary identity
browser fingerprinting as security
short PINs for remote danger operations
warnings alone
users setting up proxies/VPNs correctly

trusted device registration instead of fuzzy device fingerprinting
elevated session instead of just a short password prompt every time
If you want, I can turn this into a concrete “browser security baseline” spec with:

mandatory controls
default browser tool policy
elevation rules
trusted device flow
localhost-only admin actions
