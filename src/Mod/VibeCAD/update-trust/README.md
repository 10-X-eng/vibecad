# VibeCAD update trust anchor

Production packages must contain `root.json`, the offline-signed root metadata
from the `10-X-eng/vibecad-updates` TUF repository. The application deliberately
does not fall back to unsigned GitHub API data when this file is absent.

Root creation and rotation are owner-controlled key ceremonies. Private keys do
not belong in this repository, GitHub Actions secrets, build artifacts, or a
developer workstation cache.
