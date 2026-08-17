# Architecture

## Runtime Shape

Omapin is one Omarchy plugin with two entry points:

- `Service.qml` is loaded once by the shell. It serializes helper operations,
  owns shared token/queue/tag state, and runs the background retry timer.
- `BarWidget.qml` is instantiated by each bar surface. It renders the bookmark
  icon and loads `Panel.qml`, which contains the keyboard-first form.

`Panel.qml` uses Omarchy's `KeyboardPanel`, `TextField`, `Toggle`, `Button`,
`BorderSurface`, `Color`, and `Style` primitives. The shell therefore owns
surface placement, focus priming, outside-click dismissal, scaling, and theme
colors.

## Helper Boundary

`scripts/pinboard_helper.py` is a short-lived command helper. QML sends one JSON
request over stdin and receives one JSON response over stdout. QML only holds a
new token long enough to hand it to the helper, then clears the editor and job
payload. The boundary keeps it out of durable shell state and visible process
arguments.

The helper owns:

- Secret Service lookup, migration, storage, and deletion through
  `secret-tool`.
- Pinboard v1 request construction, parsing, timeout handling, and redaction.
- The global three-second Pinboard request pace.
- URL normalization and bounded page-title discovery.
- Sensitive-aware clipboard reads through `wl-paste`.
- An atomic JSON retry queue under the XDG state directory.

Every operation starts a fresh process. Pinboard pacing and queued work remain
correct across processes because the helper uses locked, persistent state. The
service terminates a helper that runs for more than 30 seconds and recovers from
process launch failures without blocking later jobs.

Title discovery deliberately accepts local and private-network HTTP URLs because
Omapin is a desktop bookmark client, not a remote fetch service. Requests remain
bounded by response size, redirect count, HTTPS downgrade checks, and the helper
watchdog; only the parsed title is returned to QML.

## Form Flow

1. Opening the panel checks shared service state, focuses the token or URL
   field, and requests clipboard prefill when the URL is empty.
2. URL inspection requests a title first, then duplicate detection and tag
   recommendations. The latter two are serialized to honor Pinboard's limit.
3. Responses carry panel-specific request IDs. Editing the URL invalidates old
   requests, and remote metadata only replaces fields that the user has not
   changed since inspection began.
4. A duplicate loads all bookmark fields and selects `replace=yes`; a new URL
   selects `replace=no`.
5. A successful save resets and closes the panel. A transient failure leaves
   the form in place and creates or updates one account-bound queue item.

## Queue

The service asks the helper to process at most one due item every four seconds.
API requests are still spaced by at least three seconds. Retry delays are 15,
45, 180, 900, and then 3600 seconds, with a maximum of 12 retry attempts.

Permanent failures stay visible in the queue count and can be retried from the
panel. A queued item records the Pinboard username, not the token, and cannot be
delivered while another account is active.

## Deliberate Differences From The Desktop App

The port preserves the desktop app's user-facing workflow but does not preserve
known defects:

- All Pinboard calls, not only writes, obey the documented rate limit.
- Authenticated URLs are redacted from every error path.
- Metadata from stale inspections cannot overwrite a newly edited form.
- Switching away from an existing pin clears metadata loaded for that pin.
- Writes have bounded timeouts.
- Queue items cannot cross Pinboard accounts, and failed items are visible.
- Token replacement and logout are queue barriers; later authenticated work is
  canceled instead of overtaking a credential transition.
- Title downloads are limited by response type and size.
- Token input is masked.
