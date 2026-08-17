# Omapin

Omapin is a keyboard-first [Pinboard](https://pinboard.in/) client for the
Omarchy 4 shell. It lives in the bar, opens as a native Omarchy panel, follows
the active system theme, and stores the Pinboard API token in Secret Service.

## Features

- Prefills HTTP and HTTPS links from the clipboard.
- Finds a page title without blocking the form.
- Detects an existing Pinboard URL and switches from create to update mode.
- Loads URL-specific recommendations and autocompletes from your Pinboard tags.
- Supports notes, private pins, and the read-later flag.
- Queues transient failures and retries them in the background.
- Imports the token stored by the original desktop Omapin app.

## Requirements

- Omarchy 4.0 or newer.
- Python 3.10 or newer.
- `secret-tool` from `libsecret` and an unlocked Secret Service keyring.
- Optional: `wl-paste` from `wl-clipboard` for clipboard prefill. Manual entry
  and paste still work without it.

These dependencies are part of a standard Omarchy 4 installation. The plugin
does not install or remove system packages. The corresponding Arch packages are
`python`, `libsecret`, and optionally `wl-clipboard`.

## Install

After this repository is published:

```bash
omarchy plugin add https://github.com/filipechagas/omarchy-pinboard.git --enable
```

The plugin ID is `io.github.filipechagas.omapin`. It is added to the right bar
section by default.

Open the bookmark icon and enter the API token shown on Pinboard's
[password settings page](https://pinboard.in/settings/password). The expected
format is `username:TOKEN`.

## Keybinding

Omarchy plugins do not declare global shortcuts. Add or replace a binding in
`~/.config/hypr/bindings.lua`:

```lua
hl.unbind("SUPER + SHIFT + G")
o.bind(
  "SUPER + SHIFT + G",
  "Toggle Omapin",
  "omarchy-shell shell toggle io.github.filipechagas.omapin"
)
```

`SUPER + SHIFT + G` is the standalone Omapin chord on the source setup. The
`hl.unbind` line makes the replacement explicit. For another key, unbind that
chord first when Omarchy already uses it.

### Hide The Bar Icon

Omapin can stay available through the keybinding without taking up space in the
bar:

```bash
omarchy bar set io.github.filipechagas.omapin showIcon false --json
```

The zero-width widget remains loaded so the shortcut, background retries, and
focused-monitor routing continue to work. To restore the icon:

```bash
omarchy bar set io.github.filipechagas.omapin showIcon true --json
```

## Usage

1. Copy a link and open Omapin. A valid HTTP or HTTPS URL is filled in.
2. Leave the URL field or press Enter. Omapin fetches the title, checks for an
   existing pin, and loads tag suggestions.
3. Edit the title, notes, tags, privacy, or read-later state.
4. Select **Write bookmark** or **Update bookmark**.

Pinboard permits one API call every three seconds. Duplicate detection and tag
recommendations therefore arrive in stages rather than at the same time.

## Security And Data

- The API token is sent to the helper and `secret-tool` over stdin. QML holds it
  only while you enter it and hand it off, then clears the editor and request
  payload. It is never placed in a process argument, environment variable, or
  settings file.
- API errors never include an authenticated request URL.
- When the plugin credential is absent, Omapin automatically copies the
  original Rust app's `ommapin` keyring item into its own Secret Service item.
  Logging out clears both entries so a legacy token cannot be imported again.
- Pinboard operations send the token, URL, title, notes, tags, privacy, and
  read-later state to `api.pinboard.in` over HTTPS. Pinboard v1 requires the
  token in the request query. Omapin redacts that authenticated URL from errors.
- Pending bookmarks are stored with mode `0600` under
  `${XDG_STATE_HOME:-$HOME/.local/state}/omapin/`. URLs, titles, notes, and tags in
  that retry queue are local plaintext. Queue entries are tied to the Pinboard
  username that created them and are never sent under another account.
- Clipboard prefill ignores common password-manager MIME hints. It never writes
  to the clipboard. A recognized link is inspected automatically, which sends
  requests to that page and to Pinboard when the panel opens. As a local
  bookmark client, Omapin also permits local and private-network HTTP URLs.

## Remove

```bash
omarchy plugin remove io.github.filipechagas.omapin
```

Removal does not delete the Secret Service token or local queue. Log out from
the panel before removal if those credentials should be cleared. To clean up
after removal instead:

```bash
secret-tool clear omarchy-plugin io.github.filipechagas.omapin field token
secret-tool clear target default service ommapin username pinboard_auth_token application rust-keyring
rm -rf -- "${XDG_STATE_HOME:-$HOME/.local/state}/omapin"
```

The first two commands clear the plugin and legacy token entries. The final
command permanently removes queued bookmark data and request-pacing state.

## Development

```bash
omarchy plugin validate .
/usr/lib/qt6/bin/qmllint \
  -i /usr/share/omarchy/shell/Commons/qmldir \
  -i /usr/share/omarchy/shell/Ui/qmldir \
  BarWidget.qml Panel.qml Service.qml
python3 -W error -m unittest discover -s tests -v
node --test tests/test_model.js
```

Qt's standalone linter reports known false positives for host-injected bar
properties and nested Omarchy singleton properties. Syntax or type errors still
cause a nonzero exit.

See [`docs/architecture.md`](docs/architecture.md) for the runtime design and
[`docs/research.md`](docs/research.md) for source material used by the port.
Release checks are listed in [`docs/manual-test.md`](docs/manual-test.md).
