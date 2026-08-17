# Port Research

Research was performed against the installed Omarchy 4.0.0 release and primary
upstream sources on 2026-08-17.

## Omarchy Plugin Host

- Omarchy 4 runs its shell and plugins in one Quickshell process. Third-party
  plugins use a root `manifest.json` and live under
  `~/.config/omarchy/plugins/<plugin-id>/`.
- A service plus bar-widget manifest gives Omapin one shared background worker
  and a native bar-attached `KeyboardPanel` on each display.
- `Color` and `Style` expose theme roles and scaled spacing. Plugins should use
  those roles instead of reading Omarchy theme files themselves.
- Plugin manifests cannot install keybindings. The supported invocation is
  `omarchy-shell shell toggle <plugin-id>` from a user Hyprland binding.

Sources:

- [Omarchy 4 plugin manual](https://github.com/basecamp/omarchy/blob/v4.0.0/manual/32-shell-plugins.md)
- [Omarchy 4 shell README](https://github.com/basecamp/omarchy/blob/v4.0.0/shell/README.md)
- [Omarchy 4 shell plugin source](https://github.com/basecamp/omarchy/tree/v4.0.0/shell/plugins)
- [Quickshell 0.3.0 documentation](https://quickshell.org/docs/v0.3.0/)
- [Omanote Secret Service example](https://github.com/brianblakely/omanote)

## Pinboard Contract

- Pinboard v1 uses HTTPS GET requests, including for writes.
- API tokens use the `username:TOKEN` format.
- `posts/add` maps title to `description`, notes to `extended`, complete tags to
  `tags`, privacy to `shared`, read-later to `toread`, and create/update to
  `replace`.
- `posts/get`, `posts/suggest`, and `tags/get` provide duplicate data,
  URL-specific tags, and the account's full tag vocabulary.
- The API allows one request per user every three seconds and requires clients
  to handle HTTP 429 responses with backoff.

Source: [Pinboard API v1 documentation](https://pinboard.in/api/).

## Secret Storage Compatibility

The original Rust application used `keyring` 3.6.3 with service `ommapin` and
account `pinboard_auth_token`. On Linux, that version's persistent backend
stores a Secret Service item with these lookup attributes:

```text
target=default
service=ommapin
username=pinboard_auth_token
application=rust-keyring
```

The helper reads that exact legacy item only when its canonical plugin item is
missing, then stores the token under:

```text
omarchy-plugin=io.github.filipechagas.omapin
field=token
```

Sources:

- [`keyring` 3.6.3 Secret Service backend](https://docs.rs/keyring/3.6.3/src/keyring/secret_service.rs.html)
- [`secret-tool` manual](https://man.archlinux.org/man/secret-tool.1.en)

## Publication

Omarchy's manual recommends distribution as a public Git repository and listing
community plugins at omarchyplugins.com. The root manifest, README, license,
installation/removal instructions, dependencies, and privilege boundaries in
this repository follow that submission contract.

Sources:

- [Marketplace publishing guide](https://omarchyplugins.com/publish.html)
- [Marketplace submission contract](https://github.com/HANCORE-linux/omarchy-plugin-marketplace/blob/main/SUBMISSION.md)

The intended listing category is `Productivity`, with the supported marketplace
tags `bar` and `quickshell`. `bookmarks` can be proposed as a new reusable tag.
