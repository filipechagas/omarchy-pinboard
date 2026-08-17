pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as QQC
import qs.Commons as C
import qs.Ui as Ui
import "Model.js" as Model

Ui.Panel {
  id: root
  moduleName: "io.github.filipechagas.omapin"
  manageIpc: false

  property var anchorItem: null
  property var host: null
  property var service: null

  property bool showSettings: false
  property string intent: "create"
  property bool privateValue: false
  property bool readLaterValue: false
  property bool submitting: false
  property bool tokenBusy: false
  property bool inspectingDuplicate: false
  property bool inspectingSuggestions: false
  property bool loadingTitle: false
  property string statusText: ""
  property string statusKind: "info"
  property var suggestions: ({ recommended: [], popular: [] })
  property var inspectionSnapshot: null
  property int inspectionSerial: 0
  property int requestSerial: 0
  property string lastInspectedUrl: ""
  property string loadedBookmarkUrl: ""
  property string automaticTitle: ""
  property string titleRequestId: ""
  property string duplicateRequestId: ""
  property string suggestionsRequestId: ""
  property string submitRequestId: ""
  property string tokenRequestId: ""
  property string clipboardRequestId: ""
  property string observedAccountName: ""
  property string keyringStatusText: ""
  property bool applyingRemoteValues: false
  property bool autocompleteDismissed: false
  property int activeAutocompleteIndex: 0

  readonly property string instanceKey: Date.now().toString(36)
    + "-" + Math.floor(Math.random() * 0x1000000).toString(36)
  readonly property bool tokenConfigured: service ? !!service.tokenConfigured : false
  readonly property bool serviceReady: service ? !!service.ready : false
  readonly property color foreground: C.Color.popups.text
  readonly property color muted: C.Color.muted
  readonly property color accent: C.Color.accent
  readonly property color urgent: C.Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : C.Style.font.family
  readonly property var suggestedTags: Model.suggestionTags(suggestions)
  readonly property var autocompleteOptions: Model.autocomplete(
    tagsField.text,
    suggestions,
    service ? service.userTags : [])
  readonly property bool autocompleteVisible: tagsField.activeFocus
    && !autocompleteDismissed
    && autocompleteOptions.length > 0
  readonly property bool formBusy: submitting || tokenBusy
  readonly property bool canSubmit: tokenConfigured
    && !formBusy
    && !inspectingDuplicate
    && urlField.text.trim() !== ""
    && titleField.text.trim() !== ""

  function nextRequestId(kind) {
    requestSerial++
    return "panel:" + instanceKey + ":" + kind + ":" + requestSerial
  }

  function request(operation, payload, kind, priority) {
    if (!service) {
      setStatus("Omapin's background service is not available.", "error")
      return ""
    }
    return service.request(operation, payload || {}, nextRequestId(kind || operation), priority)
  }

  function setStatus(message, kind) {
    statusText = String(message || "")
    statusKind = kind || "info"
  }

  function syncAccountState() {
    var nextAccount = service ? String(service.accountName || "") : ""
    if (nextAccount === observedAccountName) return
    resetForm()
    observedAccountName = nextAccount
  }

  function reveal(item) {
    if (!item || !formScroll || !formScroll.contentItem) return
    Qt.callLater(function() {
      if (!item || !formScroll) return
      var point = item.mapToItem(formScroll.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      var maxY = Math.max(0, formScroll.contentHeight - formScroll.height)
      if (top < formScroll.contentY)
        formScroll.contentY = Math.max(0, top)
      else if (bottom > formScroll.contentY + formScroll.height)
        formScroll.contentY = Math.min(maxY, bottom - formScroll.height)
    })
  }

  function focusInitial() {
    if (!opened) return
    var target = tokenConfigured ? urlField : tokenField
    if (target) target.forceActiveFocus()
  }

  function prepareOpen() {
    if (!service) return
    if (service.initializationError) {
      keyringStatusText = String(service.initializationError)
      setStatus(keyringStatusText, "error")
    }
    if (tokenConfigured) {
      service.loadUserTags(nextRequestId("tags"))
      if (urlField.text.trim() === "" && clipboardRequestId === "") {
        clipboardRequestId = request("clipboard", {}, "clipboard", 95)
      }
    }
  }

  function open() {
    controller.show()
    if (service) {
      if (!service.hasOperation("status"))
        service.request("status", {}, "service:status", 100)
      prepareOpen()
    }
    Qt.callLater(focusInitial)
  }

  function close() {
    autocompleteDismissed = false
    controller.hide()
  }

  function toggle() {
    if (opened) close()
    else open()
  }

  function resetForm() {
    cancelInspectionJobs()
    setStatus("")
    applyingRemoteValues = true
    urlField.text = ""
    titleField.text = ""
    notesField.text = ""
    tagsField.text = ""
    privateValue = false
    readLaterValue = false
    applyingRemoteValues = false
    intent = "create"
    suggestions = ({ recommended: [], popular: [] })
    inspectionSnapshot = null
    lastInspectedUrl = ""
    loadedBookmarkUrl = ""
    automaticTitle = ""
    inspectingDuplicate = false
    inspectingSuggestions = false
    loadingTitle = false
    autocompleteDismissed = false
    activeAutocompleteIndex = 0
  }

  function invalidateInspection() {
    cancelInspectionJobs()
    inspectionSerial++
    inspectionSnapshot = null
    lastInspectedUrl = ""
    automaticTitle = ""
    inspectingDuplicate = false
    inspectingSuggestions = false
    loadingTitle = false
    suggestions = ({ recommended: [], popular: [] })

    if (intent === "update") {
      applyingRemoteValues = true
      titleField.text = ""
      notesField.text = ""
      tagsField.text = ""
      privateValue = false
      readLaterValue = false
      applyingRemoteValues = false
      loadedBookmarkUrl = ""
    }
    intent = "create"
    setStatus("")
  }

  function cancelInspectionJobs() {
    if (!service) return
    service.cancelPending(
      "panel:" + instanceKey + ":",
      ["fetch-title", "duplicate", "suggest"])
  }

  function inspectUrl() {
    cancelInspectionJobs()
    var url = urlField.text.trim()
    inspectionSerial++
    var serial = inspectionSerial

    if (url === "") {
      applyingRemoteValues = true
      titleField.text = ""
      notesField.text = ""
      tagsField.text = ""
      applyingRemoteValues = false
      intent = "create"
      suggestions = ({ recommended: [], popular: [] })
      lastInspectedUrl = ""
      loadedBookmarkUrl = ""
      return
    }

    if (!Model.startsLikeUrl(url)) {
      intent = "create"
      suggestions = ({ recommended: [], popular: [] })
      lastInspectedUrl = ""
      setStatus("Enter a valid HTTP or HTTPS URL.", "error")
      return
    }

    lastInspectedUrl = url
    setStatus("")
    automaticTitle = ""
    inspectionSnapshot = {
      serial: serial,
      url: url,
      title: titleField.text,
      notes: notesField.text,
      tags: tagsField.text,
      privateValue: privateValue,
      readLaterValue: readLaterValue
    }

    loadingTitle = true
    inspectingDuplicate = tokenConfigured
    inspectingSuggestions = tokenConfigured

    titleRequestId = request("fetch-title", { url: url }, "title-" + serial, 90)
    if (tokenConfigured) {
      duplicateRequestId = request("duplicate", { url: url }, "duplicate-" + serial, 80)
      suggestionsRequestId = request("suggest", { url: url }, "suggest-" + serial, 50)
    }
  }

  function appendTag(tag) {
    tagsField.text = Model.mergeTag(tagsField.text, tag)
    tagsField.forceActiveFocus()
    tagsField.cursorPosition = tagsField.text.length
  }

  function inspectionIsCurrent() {
    if (!inspectionSnapshot) return false
    var current = urlField.text.trim()
    return current === inspectionSnapshot.url
      || (intent === "update" && loadedBookmarkUrl !== "" && current === loadedBookmarkUrl)
  }

  function addAllSuggestions() {
    tagsField.text = Model.mergeSuggestions(tagsField.text, suggestions)
    tagsField.forceActiveFocus()
    tagsField.cursorPosition = tagsField.text.length
  }

  function applyAutocomplete(tag) {
    if (!tag) return false
    tagsField.text = Model.completeTag(tagsField.text, tag)
    tagsField.cursorPosition = tagsField.text.length
    activeAutocompleteIndex = 0
    return true
  }

  function saveToken() {
    var token = tokenField.text.trim()
    if (token === "") {
      setStatus("Enter your Pinboard token in username:TOKEN format.", "error")
      tokenField.forceActiveFocus()
      return
    }
    tokenRequestId = request("save-token", { token: token }, "save-token", 110)
    if (tokenRequestId !== "") {
      tokenField.text = ""
      tokenBusy = true
      setStatus("Saving token to the system keyring...", "info")
    }
  }

  function clearToken() {
    tokenRequestId = request("clear-token", {}, "clear-token", 110)
    if (tokenRequestId !== "") {
      tokenBusy = true
      setStatus("Clearing token...", "info")
    }
  }

  function submitBookmark() {
    if (submitting) return
    if (!tokenConfigured) {
      setStatus("Configure a Pinboard token before saving.", "error")
      return
    }
    var validationError = Model.validateForm(
      urlField.text,
      titleField.text,
      notesField.text,
      tagsField.text)
    if (validationError !== "") {
      setStatus(validationError, "error")
      return
    }
    if (inspectingDuplicate) {
      setStatus("Wait for the duplicate check to finish.", "info")
      return
    }

    submitting = true
    setStatus(intent === "update" ? "Updating bookmark..." : "Saving bookmark...", "info")
    submitRequestId = request("submit", {
      payload: {
        url: urlField.text,
        title: titleField.text,
        notes: notesField.text,
        tags: Model.splitTags(tagsField.text),
        private: privateValue,
        readLater: readLaterValue,
        intent: intent
      }
    }, "submit", 120)
    if (submitRequestId === "") submitting = false
  }

  function applyExistingBookmark(bookmark) {
    if (!inspectionSnapshot || !bookmark) return
    var snapshot = inspectionSnapshot
    if (urlField.text.trim() !== snapshot.url) return

    applyingRemoteValues = true
    if (bookmark.url) urlField.text = String(bookmark.url)
    if (titleField.text === snapshot.title || titleField.text === automaticTitle)
      titleField.text = String(bookmark.title || "")
    if (notesField.text === snapshot.notes)
      notesField.text = String(bookmark.notes || "")
    if (tagsField.text === snapshot.tags)
      tagsField.text = (bookmark.tags || []).join(" ")
    if (privateValue === snapshot.privateValue)
      privateValue = !!bookmark.private
    if (readLaterValue === snapshot.readLaterValue)
      readLaterValue = !!bookmark.readLater
    applyingRemoteValues = false

    intent = "update"
    loadedBookmarkUrl = String(bookmark.url || snapshot.url)
    lastInspectedUrl = loadedBookmarkUrl
    automaticTitle = ""
    setStatus("Existing pin loaded. Saving will update it.", "info")
  }

  function handleTitleResult(requestId, result) {
    if (requestId !== titleRequestId) return
    loadingTitle = false
    if (!result || !result.ok || !inspectionIsCurrent()) return

    var fetched = String(result.title || "").trim()
    if (fetched !== "" && titleField.text === inspectionSnapshot.title && titleField.text.trim() === "") {
      applyingRemoteValues = true
      titleField.text = fetched
      applyingRemoteValues = false
      automaticTitle = fetched
    }
  }

  function handleDuplicateResult(requestId, result) {
    if (requestId !== duplicateRequestId) return
    inspectingDuplicate = false
    if (!inspectionIsCurrent()) return

    if (!result || !result.ok) {
      intent = "create"
      setStatus("Could not check for an existing pin: " + String(result && result.error || "unknown error"), "error")
      return
    }

    if (result.exists && result.bookmark) applyExistingBookmark(result.bookmark)
    else intent = "create"
  }

  function handleSuggestionsResult(requestId, result) {
    if (requestId !== suggestionsRequestId) return
    inspectingSuggestions = false
    if (!inspectionIsCurrent()) return
    if (!result || !result.ok) {
      suggestions = ({ recommended: [], popular: [] })
      return
    }
    suggestions = {
      recommended: result.recommended || [],
      popular: result.popular || []
    }
  }

  function handleTokenResult(requestId, operation, result) {
    if (requestId !== tokenRequestId) return
    tokenBusy = false
    if (!result || !result.ok) {
      setStatus(String(result && result.error || "Could not update the token."), "error")
      return
    }

    if (operation === "save-token") {
      showSettings = false
      resetForm()
      setStatus("Token saved in the system keyring.", "success")
      if (service) service.loadUserTags(nextRequestId("tags"))
      if (urlField.text.trim() === "" && clipboardRequestId === "")
        clipboardRequestId = request("clipboard", {}, "clipboard", 95)
      Qt.callLater(function() { urlField.forceActiveFocus() })
    } else {
      showSettings = true
      resetForm()
      setStatus("Pinboard token cleared.", "success")
      Qt.callLater(function() { tokenField.forceActiveFocus() })
    }
  }

  function handleSubmitResult(requestId, result) {
    if (requestId !== submitRequestId) return
    submitting = false
    if (!result || !result.ok) {
      setStatus("Save failed: " + String(result && result.error || "unknown error"), "error")
      return
    }

    if (result.queued) {
      setStatus(String(result.message || "Bookmark queued for retry."), "info")
      return
    }

    resetForm()
    setStatus("")
    close()
  }

  function handleResponse(requestId, operation, result) {
    if (operation === "status" && requestId === "service:status") {
      if (!result || !result.ok) {
        keyringStatusText = String(result && result.error || "Could not initialize Omapin.")
        setStatus(keyringStatusText, "error")
      } else {
        syncAccountState()
        if (result.migrated)
          setStatus("Imported the desktop Omapin token into this plugin's keyring entry.", "success")
        else if (statusText === keyringStatusText)
          setStatus("")
        keyringStatusText = ""
        if (opened) {
          prepareOpen()
          Qt.callLater(focusInitial)
        }
      }
      return
    }
    if (operation === "fetch-title") handleTitleResult(requestId, result)
    else if (operation === "duplicate") handleDuplicateResult(requestId, result)
    else if (operation === "suggest") handleSuggestionsResult(requestId, result)
    else if (operation === "save-token" || operation === "clear-token") {
      if (result && result.ok) syncAccountState()
      if (requestId !== tokenRequestId && result && result.ok) {
        tokenField.text = ""
        resetForm()
        showSettings = operation === "clear-token"
        if (opened) {
          prepareOpen()
          Qt.callLater(focusInitial)
        }
      }
      handleTokenResult(requestId, operation, result)
    }
    else if (operation === "submit") handleSubmitResult(requestId, result)
    else if (operation === "clipboard" && requestId === clipboardRequestId) {
      clipboardRequestId = ""
      if (opened && tokenConfigured && result && result.ok
          && urlField.text.trim() === "" && String(result.text || "") !== "") {
        applyingRemoteValues = true
        urlField.text = String(result.text)
        applyingRemoteValues = false
        inspectUrl()
      }
    } else if (operation === "queue-retry-now" && requestId.indexOf("panel:" + instanceKey) === 0) {
      if (result && result.ok) {
        var retryKind = result.result === "failed"
          ? "error"
          : (result.result === "rescheduled" ? "info" : "success")
        setStatus(String(result.message || "Queue retry finished."), retryKind)
      }
      else setStatus(String(result && result.error || "Queue retry failed."), "error")
    }
  }

  onOpenedChanged: {
    if (opened) {
      prepareOpen()
      Qt.callLater(focusInitial)
    }
  }

  onTokenConfiguredChanged: {
    if (opened) Qt.callLater(focusInitial)
  }

  onAutocompleteOptionsChanged: {
    if (activeAutocompleteIndex >= autocompleteOptions.length)
      activeAutocompleteIndex = 0
  }

  onServiceChanged: {
    observedAccountName = service ? String(service.accountName || "") : ""
    if (service && opened) {
      if (service.initializationError)
        setStatus(String(service.initializationError), "error")
      prepareOpen()
      Qt.callLater(focusInitial)
    }
  }

  Connections {
    target: root.service
    function onResponse(requestId, operation, result) {
      root.handleResponse(requestId, operation, result)
    }
  }

  Ui.KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root.host || root
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: root.tokenConfigured ? urlField : tokenField
    contentWidth: popup.fittedContentWidth(C.Style.space(520))
    contentHeight: popup.fittedContentHeight(formColumn.implicitHeight, C.Style.space(720))

    FocusScope {
      anchors.fill: parent
      focus: true

      Keys.priority: Keys.AfterItem
      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) {
          root.close()
          event.accepted = true
        }
      }

      Flickable {
        id: formScroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: formColumn.implicitHeight
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        clip: true

        QQC.ScrollBar.vertical: QQC.ScrollBar {
          policy: QQC.ScrollBar.AsNeeded
        }

        Column {
          id: formColumn
          width: formScroll.width
          spacing: C.Style.spacing.panelGap

          Item {
            width: parent.width
            height: Math.max(mark.implicitHeight, titleBlock.implicitHeight, headerActions.implicitHeight)

            Text {
              id: mark
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: "\uf02e"
              color: root.accent
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.display
            }

            Column {
              id: titleBlock
              anchors.left: mark.right
              anchors.leftMargin: C.Style.space(12)
              anchors.right: headerActions.left
              anchors.rightMargin: C.Style.space(12)
              anchors.verticalCenter: parent.verticalCenter
              spacing: C.Style.spacing.xs

              Text {
                width: parent.width
                text: "OMAPIN"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.heading
                font.bold: true
                font.letterSpacing: 1.2
                elide: Text.ElideRight
              }

              Text {
                width: parent.width
                text: "Pinboard capture, at hand."
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.bodySmall
                elide: Text.ElideRight
              }
            }

            Row {
              id: headerActions
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: C.Style.spacing.sm

              Ui.BorderSurface {
                width: modeText.implicitWidth + C.Style.space(14)
                height: modeText.implicitHeight + C.Style.space(8)
                radius: C.Style.cornerRadius
                color: root.intent === "update"
                  ? C.Style.selectedFillFor(root.foreground, root.accent)
                  : C.Style.normalFillFor(root.foreground, root.accent)
                borderSpec: C.Border.controlSpec(
                  root.intent === "update" ? "selected" : "normal",
                  root.foreground,
                  root.accent)

                Text {
                  id: modeText
                  anchors.centerIn: parent
                  text: root.intent.toUpperCase()
                  color: root.intent === "update" ? root.accent : root.muted
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.caption
                  font.bold: true
                  font.letterSpacing: 0.8
                }
              }

              Ui.Button {
                visible: root.tokenConfigured
                text: root.showSettings ? "Close" : "Settings"
                bordered: true
                focusable: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: {
                  root.showSettings = !root.showSettings
                  if (root.showSettings) Qt.callLater(function() { tokenField.forceActiveFocus() })
                }
              }
            }
          }

          Ui.PanelSeparator {
            width: parent.width
            foreground: root.foreground
          }

          Ui.BorderSurface {
            id: tokenCard
            visible: !root.tokenConfigured || root.showSettings
            width: parent.width
            height: tokenColumn.implicitHeight + C.Style.space(28)
            radius: C.Style.cornerRadius
            color: C.Style.normalFillFor(root.foreground, root.accent)
            borderSpec: C.Border.controlSpec("normal", root.foreground, root.accent)

            Column {
              id: tokenColumn
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: C.Style.space(14)
              anchors.rightMargin: C.Style.space(14)
              spacing: C.Style.space(10)

              Item {
                width: parent.width
                height: Math.max(authTitle.implicitHeight, authBadge.implicitHeight)

                Text {
                  id: authTitle
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: "PINBOARD TOKEN"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.subtitle
                  font.bold: true
                  font.letterSpacing: 0.7
                }

                Text {
                  id: authBadge
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.tokenConfigured ? "CONFIGURED" : "REQUIRED"
                  color: root.tokenConfigured ? root.accent : root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.caption
                  font.bold: true
                  font.letterSpacing: 0.7
                }
              }

              Text {
                width: parent.width
                text: root.tokenConfigured
                  ? "Replace your token or log out. Credentials stay in Secret Service."
                  : "Add username:TOKEN once. Omapin stores it in your system keyring."
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.bodySmall
                wrapMode: Text.WordWrap
              }

              Ui.TextField {
                id: tokenField
                width: parent.width
                enabled: !root.tokenBusy
                password: true
                placeholderText: "username:TOKEN"
                foreground: root.foreground
                accent: root.accent
                font.family: root.fontFamily
                inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
                onAccepted: root.saveToken()
                onActiveFocusChanged: if (activeFocus) root.reveal(tokenCard)
              }

              Row {
                spacing: C.Style.spacing.rowGap

                Ui.Button {
                  text: "Save token"
                  bordered: true
                  selected: true
                  focusable: true
                  enabled: !root.tokenBusy
                  opacity: enabled ? 1 : 0.45
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: root.saveToken()
                }

                Ui.Button {
                  visible: root.tokenConfigured
                  text: "Logout"
                  bordered: true
                  focusable: true
                  enabled: !root.tokenBusy
                  opacity: enabled ? 1 : 0.45
                  foreground: root.urgent
                  accent: root.urgent
                  fontFamily: root.fontFamily
                  onClicked: root.clearToken()
                }
              }
            }
          }

          Column {
            visible: root.tokenConfigured
            width: parent.width
            enabled: !root.formBusy
            spacing: C.Style.space(11)

            Text {
              text: "URL"
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.caption
              font.bold: true
              font.letterSpacing: 1
            }

            Ui.TextField {
              id: urlField
              width: parent.width
              enabled: !root.formBusy
              placeholderText: "https://news.ycombinator.com"
              foreground: root.foreground
              accent: root.accent
              font.family: root.fontFamily
              inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText
              onAccepted: {
                if (text.trim() !== root.lastInspectedUrl) root.inspectUrl()
                titleField.forceActiveFocus()
              }
              onEditingFinished: {
                if (root.opened && text.trim() !== root.lastInspectedUrl) root.inspectUrl()
              }
              onTextEdited: {
                if (!root.applyingRemoteValues && text.trim() !== root.lastInspectedUrl)
                  root.invalidateInspection()
              }
              onActiveFocusChanged: if (activeFocus) root.reveal(this)
            }

            Text {
              visible: root.inspectingDuplicate || root.loadingTitle || root.inspectingSuggestions
              text: {
                var parts = []
                if (root.loadingTitle) parts.push("title")
                if (root.inspectingDuplicate) parts.push("existing pin")
                if (root.inspectingSuggestions) parts.push("tags")
                return "Looking up " + parts.join(", ") + "..."
              }
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.caption
              font.italic: true
            }

            Text {
              text: "TITLE"
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.caption
              font.bold: true
              font.letterSpacing: 1
            }

            Ui.TextField {
              id: titleField
              width: parent.width
              enabled: !root.formBusy
              placeholderText: "Hacker News"
              foreground: root.foreground
              accent: root.accent
              font.family: root.fontFamily
              maximumLength: 255
              onTextEdited: root.automaticTitle = ""
              onAccepted: root.submitBookmark()
              onActiveFocusChanged: if (activeFocus) root.reveal(this)
            }

            Text {
              text: "NOTES"
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.caption
              font.bold: true
              font.letterSpacing: 1
            }

            QQC.TextArea {
              id: notesField
              width: parent.width
              height: C.Style.space(96)
              enabled: !root.formBusy
              placeholderText: "Optional notes"
              wrapMode: TextEdit.Wrap
              selectByMouse: true
              persistentSelection: true
              activeFocusOnTab: true
              hoverEnabled: true
              color: root.foreground
              placeholderTextColor: root.muted
              selectionColor: C.Style.selectionFillFor(root.foreground, root.accent)
              selectedTextColor: root.foreground
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.body
              Keys.priority: Keys.BeforeItem
              Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Tab && event.modifiers === Qt.NoModifier) {
                  tagsField.forceActiveFocus()
                  event.accepted = true
                } else if (event.key === Qt.Key_Backtab
                           || (event.key === Qt.Key_Tab && (event.modifiers & Qt.ShiftModifier))) {
                  titleField.forceActiveFocus()
                  event.accepted = true
                }
              }

              readonly property var chrome: C.Border.controlSpec(
                activeFocus ? "focus" : (hovered ? "hover-cursor" : "normal"),
                root.foreground,
                root.accent)

              leftPadding: C.Style.spacing.controlPaddingX + C.Border.left(chrome)
              rightPadding: C.Style.spacing.controlPaddingX + C.Border.right(chrome)
              topPadding: C.Style.spacing.inputPaddingY + C.Border.top(chrome)
              bottomPadding: C.Style.spacing.inputPaddingY + C.Border.bottom(chrome)

              background: Ui.BorderSurface {
                color: C.Style.controlFill(
                  notesField.activeFocus,
                  notesField.hovered,
                  root.foreground,
                  root.accent)
                borderSpec: notesField.chrome
                radius: C.Style.cornerRadius
              }

              onActiveFocusChanged: if (activeFocus) root.reveal(this)
            }

            Text {
              text: "TAGS"
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.caption
              font.bold: true
              font.letterSpacing: 1
            }

            Ui.TextField {
              id: tagsField
              width: parent.width
              enabled: !root.formBusy
              placeholderText: "tech news rust"
              foreground: root.foreground
              accent: root.accent
              font.family: root.fontFamily
              inputMethodHints: Qt.ImhNoPredictiveText
              Keys.priority: Keys.BeforeItem
              Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Escape && root.autocompleteVisible) {
                  root.autocompleteDismissed = true
                  event.accepted = true
                  return
                }

                var count = root.autocompleteVisible ? root.autocompleteOptions.length : 0
                if (count === 0) {
                  if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                      && event.modifiers === Qt.NoModifier) {
                    root.submitBookmark()
                    event.accepted = true
                  }
                  return
                }

                if (event.key === Qt.Key_Down) {
                  root.activeAutocompleteIndex = (root.activeAutocompleteIndex + 1) % count
                  event.accepted = true
                } else if (event.key === Qt.Key_Up) {
                  root.activeAutocompleteIndex = (root.activeAutocompleteIndex - 1 + count) % count
                  event.accepted = true
                } else if ((event.key === Qt.Key_Tab || event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                           && event.modifiers === Qt.NoModifier) {
                  var match = root.autocompleteOptions[root.activeAutocompleteIndex]
                    || root.autocompleteOptions[0]
                  if (root.applyAutocomplete(match)) event.accepted = true
                }
              }
              onTextEdited: {
                root.activeAutocompleteIndex = 0
                root.autocompleteDismissed = false
              }
              onActiveFocusChanged: {
                if (activeFocus) {
                  root.autocompleteDismissed = false
                  root.reveal(this)
                }
              }
            }

            Column {
              visible: root.autocompleteVisible
              width: parent.width
              spacing: C.Style.spacing.xs

              Repeater {
                model: root.autocompleteOptions

                Ui.Button {
                  required property var modelData
                  required property int index
                  width: parent.width
                  text: String(modelData)
                  leftAlign: true
                  bordered: false
                  hasCursor: index === root.activeAutocompleteIndex
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onHovered: function(on) {
                    if (on) root.activeAutocompleteIndex = index
                  }
                  onClicked: {
                    root.applyAutocomplete(modelData)
                    tagsField.forceActiveFocus()
                  }
                }
              }
            }

            Column {
              visible: root.suggestedTags.length > 0
              width: parent.width
              spacing: C.Style.spacing.md

              Ui.PanelSectionHeader {
                text: "SUGGESTED"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              Flow {
                id: suggestionFlow
                width: parent.width
                height: childrenRect.height
                spacing: C.Style.spacing.md

                Repeater {
                  model: root.suggestedTags

                  Ui.Button {
                    required property var modelData
                    text: String(modelData)
                    bordered: true
                    selected: true
                    focusable: true
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    horizontalPadding: C.Style.space(8)
                    verticalPadding: C.Style.space(4)
                    onClicked: root.appendTag(modelData)
                    onActiveFocusChanged: if (activeFocus) root.reveal(this)
                  }
                }

                Ui.Button {
                  text: "Add all"
                  bordered: true
                  focusable: true
                  foreground: root.muted
                  fontFamily: root.fontFamily
                  horizontalPadding: C.Style.space(8)
                  verticalPadding: C.Style.space(4)
                  onClicked: root.addAllSuggestions()
                  onActiveFocusChanged: if (activeFocus) root.reveal(this)
                }
              }
            }

            Grid {
              id: toggleGrid
              width: parent.width
              columns: width >= C.Style.space(360) ? 2 : 1
              spacing: C.Style.spacing.rowGap

              Ui.Toggle {
                width: toggleGrid.columns === 2
                  ? (toggleGrid.width - toggleGrid.spacing) / 2
                  : toggleGrid.width
                label: "Private"
                description: "Keep this pin to yourself"
                checked: root.privateValue
                foreground: root.foreground
                accent: root.accent
                fontFamily: root.fontFamily
                onClicked: root.privateValue = !root.privateValue
                onActiveFocusChanged: if (activeFocus) root.reveal(this)
              }

              Ui.Toggle {
                width: toggleGrid.columns === 2
                  ? (toggleGrid.width - toggleGrid.spacing) / 2
                  : toggleGrid.width
                label: "Read later"
                description: "Mark this pin as unread"
                checked: root.readLaterValue
                foreground: root.foreground
                accent: root.accent
                fontFamily: root.fontFamily
                onClicked: root.readLaterValue = !root.readLaterValue
                onActiveFocusChanged: if (activeFocus) root.reveal(this)
              }
            }

            Item {
              width: parent.width
              height: Math.max(submitButton.implicitHeight, intentLabel.implicitHeight)

              Ui.Button {
                id: submitButton
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: root.submitting
                  ? "Saving..."
                  : (root.intent === "update" ? "Update bookmark" : "Write bookmark")
                iconText: root.submitting ? "\uf110" : ""
                iconSpinning: root.submitting
                bordered: true
                selected: true
                focusable: true
                enabled: root.canSubmit
                opacity: enabled ? 1 : 0.45
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.submitBookmark()
                onActiveFocusChanged: if (activeFocus) root.reveal(this)
              }

              Text {
                id: intentLabel
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: root.intent === "update" ? "REPLACES EXISTING PIN" : "CREATES A NEW PIN"
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.caption
                font.letterSpacing: 0.6
              }
            }
          }

          Ui.BorderSurface {
            visible: root.statusText !== ""
            width: parent.width
            height: statusLabel.implicitHeight + C.Style.space(20)
            radius: C.Style.cornerRadius
            color: root.statusKind === "error"
              ? C.Style.normalFillFor(root.urgent, root.urgent)
              : C.Style.normalFillFor(root.foreground, root.accent)
            borderSpec: C.Border.controlSpec(
              root.statusKind === "error" ? "hover-cursor" : "normal",
              root.statusKind === "error" ? root.urgent : root.foreground,
              root.statusKind === "error" ? root.urgent : root.accent)

            Text {
              id: statusLabel
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: C.Style.space(10)
              anchors.rightMargin: C.Style.space(10)
              text: (root.statusKind === "error" ? "!  " : ">  ") + root.statusText
              color: root.statusKind === "error" ? root.urgent : root.foreground
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }

          Ui.BorderSurface {
            visible: root.service
              && (root.service.queuePending > 0 || root.service.queueFailed > 0)
            width: parent.width
            height: queueRow.implicitHeight + C.Style.space(18)
            radius: C.Style.cornerRadius
            color: C.Style.normalFillFor(root.foreground, root.accent)
            borderSpec: C.Border.controlSpec("normal", root.foreground, root.accent)

            Row {
              id: queueRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: C.Style.space(10)
              anchors.rightMargin: C.Style.space(10)
              spacing: C.Style.spacing.rowGap

              Text {
                width: parent.width - retryButton.implicitWidth - parent.spacing
                anchors.verticalCenter: parent.verticalCenter
                text: {
                  var pending = root.service ? root.service.queuePending : 0
                  var failed = root.service ? root.service.queueFailed : 0
                  var value = pending + " queued"
                  if (failed > 0) value += " / " + failed + " need attention"
                  return value
                }
                color: root.service && root.service.queueFailed > 0 ? root.urgent : root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.bodySmall
                elide: Text.ElideRight
              }

              Ui.Button {
                id: retryButton
                anchors.verticalCenter: parent.verticalCenter
                text: "Retry"
                bordered: true
                focusable: true
                enabled: root.service && !root.service.hasOperation("queue-retry-now")
                  && !root.tokenBusy
                opacity: enabled ? 1 : 0.45
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.request("queue-retry-now", {}, "queue-retry-now", 115)
                onActiveFocusChanged: if (activeFocus) root.reveal(this)
              }
            }
          }

          Item {
            width: 1
            height: C.Style.spacing.hairline
          }
        }
      }
    }
  }
}
