import QtQuick
import Quickshell.Io

Item {
  id: root

  property var shell: null
  property var manifest: null

  property bool ready: false
  property bool tokenConfigured: false
  property string accountName: ""
  property string initializationError: ""
  property var userTags: []
  property bool userTagsLoaded: false
  property var queueItems: []

  property var pendingJobs: []
  property var activeJob: null
  property int requestSerial: 0
  property string operationOutput: ""
  property string operationError: ""
  property bool initialized: false
  property bool helperStarted: false
  property bool helperTimedOut: false

  readonly property string pluginDir: manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir)
    : ""
  readonly property string helperPath: pluginDir !== ""
    ? pluginDir.replace(/\/$/, "") + "/scripts/pinboard_helper.py"
    : ""
  readonly property int queuePending: countQueueStatus("pending")
  readonly property int queueFailed: countQueueStatus("failed")

  signal response(string requestId, string operation, var result)

  function countQueueStatus(status) {
    var count = 0
    var items = queueItems || []
    for (var i = 0; i < items.length; i++) {
      if (String(items[i].status || "pending") === status) count++
    }
    return count
  }

  function hasOperation(operation) {
    if (activeJob && activeJob.operation === operation) return true
    for (var i = 0; i < pendingJobs.length; i++) {
      if (pendingJobs[i].operation === operation) return true
    }
    return false
  }

  function cancelAuthenticatedJobs() {
    var authenticated = {
      "tags": true,
      "duplicate": true,
      "suggest": true,
      "submit": true,
      "queue-retry-due": true,
      "queue-retry-now": true
    }
    var keep = []
    var canceled = []
    for (var i = 0; i < pendingJobs.length; i++) {
      var job = pendingJobs[i]
      if (authenticated[job.operation]) canceled.push(job)
      else keep.push(job)
    }
    pendingJobs = keep

    for (var j = 0; j < canceled.length; j++) {
      var canceledJob = canceled[j]
      response(canceledJob.requestId, canceledJob.operation, {
        ok: false,
        code: "credential-changed",
        error: "The Pinboard credential changed before this request ran."
      })
    }
  }

  function cancelPending(requestPrefix, operations) {
    var allowed = {}
    for (var i = 0; i < operations.length; i++) allowed[String(operations[i])] = true
    var keep = []
    for (var j = 0; j < pendingJobs.length; j++) {
      var job = pendingJobs[j]
      if (String(job.requestId).indexOf(String(requestPrefix)) === 0 && allowed[job.operation])
        continue
      keep.push(job)
    }
    pendingJobs = keep
  }

  function request(operation, payload, requestId, priority) {
    requestSerial++
    var id = requestId || (operation + ":" + requestSerial)
    var selectedPriority = priority === undefined ? 50 : Number(priority)
    if (operation === "save-token" || operation === "clear-token")
      selectedPriority = Math.max(selectedPriority, 200)
    var next = pendingJobs.slice()
    next.push({
      operation: String(operation),
      payload: payload || {},
      requestId: String(id),
      priority: selectedPriority,
      serial: requestSerial
    })
    next.sort(function(a, b) {
      if (a.priority !== b.priority) return b.priority - a.priority
      return a.serial - b.serial
    })
    pendingJobs = next
    Qt.callLater(startNext)
    return id
  }

  function initialize() {
    if (initialized || helperPath === "") return
    initialized = true
    if (!hasOperation("status")) request("status", {}, "service:status", 100)
  }

  function refreshQueue(requestId) {
    return request("queue-list", {}, requestId || "service:queue-list", 30)
  }

  function loadUserTags(requestId) {
    if (!tokenConfigured || userTagsLoaded || hasOperation("tags")) return ""
    return request("tags", {}, requestId || "service:tags", 20)
  }

  function startNext() {
    if (activeJob || helperProc.running || pendingJobs.length === 0) return
    if (helperPath === "") {
      initializeTimer.restart()
      return
    }

    var next = pendingJobs.slice()
    activeJob = next.shift()
    pendingJobs = next
    operationOutput = ""
    operationError = ""
    helperStarted = false
    helperTimedOut = false
    helperProc.command = ["python3", helperPath, activeJob.operation]
    helperTimeout.restart()
    helperProc.running = true
  }

  function safeProcessError(exitCode) {
    var message = String(operationError || "").trim()
    if (message.length > 240) message = message.slice(0, 240) + "..."
    return {
      ok: false,
      code: exitCode === 127 ? "missing-dependency" : "helper-failed",
      error: message || "Omapin helper exited without a response."
    }
  }

  function finishActive(exitCode) {
    var job = activeJob
    if (!job) return
    helperTimeout.stop()

    var result = null
    var raw = String(operationOutput || "").trim()
    if (helperTimedOut) {
      result = {
        ok: false,
        code: "helper-timeout",
        error: "Omapin helper timed out."
      }
    } else if (raw !== "") {
      try {
        result = JSON.parse(raw)
      } catch (error) {
        result = {
          ok: false,
          code: "invalid-helper-response",
          error: "Omapin helper returned an invalid response."
        }
      }
    } else {
      result = safeProcessError(exitCode)
    }

    applyResult(job.operation, result)
    activeJob = null
    helperStarted = false
    helperTimedOut = false
    response(job.requestId, job.operation, result)
    Qt.callLater(startNext)
  }

  function applyResult(operation, result) {
    if (!result) return

    if ((operation === "save-token" || operation === "clear-token") && !result.ok) {
      cancelAuthenticatedJobs()
      if (!hasOperation("status")) request("status", {}, "service:status", 190)
    }

    if (operation === "status") {
      if (result.ok) {
        var nextAccount = String(result.username || "")
        var accountChanged = nextAccount !== accountName
        ready = true
        tokenConfigured = !!result.tokenConfigured
        accountName = tokenConfigured ? nextAccount : ""
        initializationError = ""
        if (!tokenConfigured || accountChanged) {
          userTags = []
          userTagsLoaded = false
        }
        if (!tokenConfigured || accountChanged) cancelAuthenticatedJobs()
      } else {
        ready = false
        tokenConfigured = false
        accountName = ""
        userTags = []
        userTagsLoaded = false
        queueItems = []
        initializationError = String(result.error || "Could not initialize Omapin.")
        cancelAuthenticatedJobs()
      }
    } else if (operation === "save-token" && result.ok) {
      cancelAuthenticatedJobs()
      ready = true
      tokenConfigured = true
      accountName = String(result.username || "")
      initializationError = ""
      userTags = []
      userTagsLoaded = false
    } else if (operation === "clear-token" && result.ok) {
      cancelAuthenticatedJobs()
      ready = true
      tokenConfigured = false
      accountName = ""
      initializationError = ""
      userTags = []
      userTagsLoaded = false
    } else if (operation === "tags" && result.ok) {
      userTags = result.tags || []
      userTagsLoaded = true
    }

    if (!result.ok && result.code === "not_authenticated") {
      ready = true
      tokenConfigured = false
      accountName = ""
      initializationError = ""
      userTags = []
      userTagsLoaded = false
      queueItems = []
      cancelAuthenticatedJobs()
    } else if (!result.ok && [
                 "secret_storage_unavailable",
                 "secret_storage_error",
                 "invalid_stored_token"
               ].indexOf(String(result.code || "")) !== -1) {
      ready = false
      tokenConfigured = false
      accountName = ""
      initializationError = String(result.error || "Secure token storage is unavailable.")
      userTags = []
      userTagsLoaded = false
      queueItems = []
      cancelAuthenticatedJobs()
    }

    if (operation === "submit" && result.ok && !result.queued) {
      userTags = []
      userTagsLoaded = false
    }
    if ((operation === "queue-retry-due" || operation === "queue-retry-now")
        && result.ok && result.result === "submitted") {
      userTagsLoaded = false
      loadUserTags("service:tags-after-retry")
    }

    if (result.queue !== undefined) queueItems = result.queue || []
    else if (result.items !== undefined && operation.indexOf("queue-") === 0) queueItems = result.items || []
  }

  onHelperPathChanged: initialize()
  Component.onCompleted: initializeTimer.start()

  Timer {
    id: initializeTimer
    interval: 100
    onTriggered: root.initialize()
  }

  Timer {
    id: helperTimeout
    interval: 30000
    onTriggered: {
      if (!root.activeJob) return
      if (!helperProc.running || helperProc.processId === null) {
        if (!root.helperStarted) root.finishActive(127)
        return
      }
      root.helperTimedOut = true
      root.operationError = "Omapin helper timed out."
      helperProc.signal(9)
    }
  }

  Timer {
    interval: 4000
    repeat: true
    running: root.ready && root.tokenConfigured && root.queuePending > 0
    onTriggered: {
      if (!root.hasOperation("queue-retry-due"))
        root.request("queue-retry-due", {}, "service:queue-retry-due", 5)
    }
  }

  Process {
    id: helperProc
    stdinEnabled: true

    onStarted: {
      root.helperStarted = true
      var payload = root.activeJob ? root.activeJob.payload : {}
      write(JSON.stringify(payload || {}) + "\n")
      if (root.activeJob && root.activeJob.operation === "save-token")
        root.activeJob.payload = {}
    }

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.operationOutput = text
    }

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.operationError = text
    }

    onRunningChanged: {
      if (!running && root.activeJob && !root.helperStarted) {
        Qt.callLater(function() {
          if (!helperProc.running && root.activeJob && !root.helperStarted)
            root.finishActive(127)
        })
      }
    }

    onExited: function(exitCode) {
      helperTimeout.stop()
      Qt.callLater(function() { root.finishActive(exitCode) })
    }
  }
}
