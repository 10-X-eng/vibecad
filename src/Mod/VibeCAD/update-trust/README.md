# Optional enterprise TUF trust anchor

Normal VibeCAD updates use official `10-X-eng/vibecad` GitHub Releases and do
not require a `root.json` file.

Administrators may instead configure a custom TUF service through managed
policy. That opt-in path requires `metadata_base_url` and `target_base_url`,
plus either `trusted_root` or a public `root.json` packaged here by the managed
distribution. Partial configuration fails closed. VibeCAD does not host a
default TUF service.

Private keys do not belong in this repository, GitHub Actions secrets, build
artifacts, or a developer workstation cache.
