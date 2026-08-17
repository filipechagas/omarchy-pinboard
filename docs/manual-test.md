# Manual Test Matrix

Use a disposable Pinboard URL when testing create/update behavior.

## Installation And Lifecycle

- `omarchy plugin validate .` succeeds.
- Enabling the plugin adds one bookmark icon to the requested bar section.
- Clicking the icon and `omarchy-shell shell toggle io.github.filipechagas.omapin`
  both open and close the panel.
- Escape and an outside click close the panel.
- Disabling, re-enabling, and restarting the shell preserve the token and queue.
- A keyboard summon opens on the focused monitor; clicking opens on that bar.
- Top, bottom, left, and right bars keep the panel on-screen.

## Authentication

- With no token, only the masked token form is available.
- Invalid token syntax is rejected without changing Secret Service.
- A valid token survives a shell restart and enables the bookmark form.
- An original desktop Omapin token is imported without displaying it.
- Replacing a token clears account-derived form data and tag autocomplete.
- Logout removes both the plugin and legacy keyring entries.
- Missing, locked, or unavailable Secret Service shows an actionable error.
- A missing Python/helper launch reports an error without wedging later requests.
- Save and Retry cannot overtake a pending token replacement or logout.

## Bookmark Form

- A normal HTTP/HTTPS clipboard URL prefills on open; non-URLs and password
  manager clipboard data do not.
- URL inspection fills an empty title without overwriting a title typed while
  the request is running.
- A new URL stays in create mode.
- An existing URL loads title, notes, tags, private, and read-later values and
  switches to update mode.
- Editing the URL while inspection is running prevents stale results from
  changing the new form.
- URL, title, notes, and tags are reachable in order with Tab/Shift+Tab.
- Tag completion supports Up, Down, Tab, Enter, Escape, pointer selection, tag
  chips, and Add all.
- Create closes the panel and creates one pin; update closes it and replaces the
  existing pin.
- Empty/invalid URLs, empty/long titles, long notes, comma tags, long tags, and
  more than 100 tags produce visible validation errors.

## Retry Queue

- A transient network failure queues one item and keeps the form visible.
- Repeated submission of the same account and URL does not duplicate the item.
- A restored connection sends a due item and clears the queue indicator.
- Retry triggers an immediate attempt.
- A permanent API failure remains visible as needing attention.
- A manual retry that becomes permanent uses error styling, not success styling.
- Changing to another Pinboard username never sends the old account's item.

## Appearance

- Light and dark themes update panel surface, text, controls, focus, and accent.
- Increased Omarchy font/spacing scales do not clip fields or actions.
- A short display uses vertical scrolling and keeps the focused field visible.
