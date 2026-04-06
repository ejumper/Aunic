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

---
# Transcript
| # | role      | type        | tool_name  | tool_id  | content
|---|-----------|-------------|------------|----------|-------------------------------
| 1  | assistant | tool_call   | web_search | call_function_zv4m0bgmu1vm_1 | {"queries":["iOS PWA secure token storage Keychain WebApp site"]}
| 2  | tool      | tool_result | web_search | call_function_zv4m0bgmu1vm_1 | [{"url":"https://ravi6997.medium.com/securing-ios-apps-end-to-end-from-local-storage-to-backend-7d110171c196","title":"Securing iOS Apps End-to-End: From Local Storage to Backend","snippet":"Learn how to secure iOS apps from local storage to backend APIs. Covers Keychain, Secure Enclave, SSL/TLS pinning, OAuth2, JWT tokens, and common mistakes to avoid. Perfect for Swift, SwiftUI, and iOS 26 developers."},{"url":"https://developer.apple.com/documentation/security/keychain-services","title":"Keychain services | Apple Developer Documentation","snippet":"The keychain services API helps you solve this problem by giving your app a mechanism to store small bits of user data in an encrypted database called a keychain. When you securely remember the password for them, you free the user to choose a complicated one. The keychain is not limited to passwords, as shown in Figure 1."},{"url":"https://oneuptime.com/blog/post/2026-02-02-swift-keychain-secure-storage","title":"How to Use Keychain for Secure Storage in Swift","snippet":"Learn how to use the iOS Keychain to securely store sensitive data like passwords, tokens, and cryptographic keys in your Swift applications with practical examples and best practices."},{"url":"https://auth0.com/docs/secure/security-guidance/data-security/token-storage","title":"Token Storage - Auth0 Docs","snippet":"Browser in-memory scenarios Auth0 recommends storing tokens in browser memory as the most secure option. Using Web Workers to handle the transmission and storage of tokens is the best way to protect the tokens, as Web Workers run in a separate global scope than the rest of the application."},{"url":"https://securecodingpractices.com/secure-data-storage-ios-keychain-core-data-realm/","title":"Secure Data Storage iOS: Keychain, Core Data & Realm Guide","snippet":"A practical guide for iOS developers on secure data storage using Keychain, Core Data, and Realm, with real-world tips from expert bootcamp training sessions."},{"url":"https://suho.dev/posts/security-best-practices-in-ios/","title":"iOS best practices - storage sensitive data","snippet":"For instance: Authentication tokens (access token, refresh token) or credentials should not be saved in UserDefaults without any encryption. Avoid storing API Keys, Encryption Keys in .plist files, hardcoded as String in code. Instead, you must store sensitive data by using Keychain, which stores data inside the Secure Enclave."},{"url":"https://capgo.app/blog/secure-token-storage-best-practices-for-mobile-developers/","title":"Secure Token Storage: Best Practices for Mobile Developers","snippet":"Key Takeaways: Use Platform-Native Secure Storage: Store tokens in iOS Keychain or Android Keystore for hardware-backed security. Encrypt Tokens at Rest: Use tools like EncryptedSharedPreferences (Android) or CryptoKit (iOS) for secure encryption. Limit Token Exposure: Use short-lived tokens and refresh token rotation to reduce risk."},{"url":"https://github.com/tksreact/rn-secure-keystore","title":"GitHub - tksreact/rn-secure-keystore: Secure key-value storage for ...","snippet":"A comprehensive, cross-platform React Native wrapper for secure key-value storage using native security features of Android and iOS. It supports biometric authentication, hardware-backed encryption, and deep platform integrations such as Android StrongBox, EncryptedSharedPreferences, and iOS Secure Enclave via the Keychain. This library enables storing data securely with biometric protection ..."},{"url":"https://www.momentslog.com/development/ios/managing-user-data-with-keychain-implementing-secure-data-storage-and-access","title":"Managing User Data with Keychain: Implementing Secure Data Storage and ...","snippet":"Learn how to securely manage user data with Keychain, implementing effective storage and access strategies for enhanced data protection."},{"url":"https://duendesoftware.com/learn/best-practices-using-jwts-with-web-and-mobile-apps","title":"JWT Best Practices for Web & Mobile Apps | Duende","snippet":"Avoid common JWT security pitfalls in web and mobile app development. Follow best practices for token storage, expiration, and validation to build robust apps."}]
