import QtQuick
import Quickshell
import "." as Omapin

ShellRoot {
  id: root
  property bool sawInvalidToken: false
  property bool sawTimeout: false

  Omapin.Service {
    id: service
    manifest: ({})
    helperPath: Qt.resolvedUrl("tests/fixtures/scripts/pinboard_helper.py").toString().replace(/^file:\/\//, "")
    helperTimeoutMs: 200
    onResponse: function(requestId, operation, result) {
      if (requestId === "initial") {
        if (!result.ok) root.fail("status failed")
        service.request("save-token", { token: "wrong" }, "bad-token", 110)
      } else if (requestId === "bad-token") {
        if (result.code !== "invalid_token") root.fail("wrong error: " + result.code)
        root.sawInvalidToken = true
        service.request("status", {}, "after-error", 100)
      } else if (requestId === "after-error") {
        if (!root.sawInvalidToken || !result.ok) root.fail("next request failed")
        service.request("save-token", { token: "alice:hang" }, "hung-token", 110)
      } else if (requestId === "hung-token") {
        if (result.code !== "helper-timeout") root.fail("watchdog error: " + result.code)
        root.sawTimeout = true
        service.request("status", {}, "after-timeout", 100)
      } else if (requestId === "after-timeout") {
        if (!root.sawTimeout || !result.ok) root.fail("request after timeout failed")
        service.activeJob = { requestId: "orphan-token", operation: "save-token", payload: {} }
        service.helperStarted = true
        for (var i = 0; i < service.data.length; i++) {
          if (service.data[i].objectName === "helperTimeout")
            service.data[i].restart()
        }
      } else if (requestId === "orphan-token") {
        if (result.code !== "helper-timeout") root.fail("orphan error: " + result.code)
        service.request("status", {}, "after-orphan", 100)
      } else if (requestId === "after-orphan") {
        if (!result.ok) root.fail("request after orphan failed")
        else {
          console.log("PASS: invalid, stalled and orphaned token requests complete; service recovers")
          Qt.quit()
        }
      }
    }
  }

  Omapin.Service {
    id: pathService
    manifest: ({})
    initialized: true
  }

  function fail(message) {
    console.error("FAIL: " + message)
    Qt.quit()
  }

  Timer {
    interval: 2000
    running: true
    onTriggered: root.fail("stuck waiting for a token response")
  }

  Component.onCompleted: {
    if (!pathService.helperPath.endsWith("/scripts/pinboard_helper.py"))
      root.fail("plugin helper path is missing without private manifest fields")
    else service.request("status", {}, "initial", 100)
  }
}
