import QtQuick
import Quickshell
import "." as Omapin
import "Model.js" as Model

ShellRoot {
  id: root
  property bool sawTags: false

  Omapin.Service {
    id: service
    helperPath: Qt.resolvedUrl("tests/fixtures/scripts/tags_helper.py").toString().replace(/^file:\/\//, "")
    onResponse: function(requestId, operation, result) {
      if (operation === "status") {
        service.request("suggest", { url: "https://example.com" }, "suggest", 50)
        service.loadUserTags("tags")
      } else if (requestId === "tags") {
        if (!result.ok || !hasCompletions()) return root.fail("existing tags did not reach autocomplete")
        root.sawTags = true
      } else if (requestId === "suggest") {
        if (!root.sawTags) return root.fail("autocomplete tags wait behind URL suggestions")
        if (result.ok || !hasCompletions()) return root.fail("failed suggestions removed autocomplete")
        service.request("submit", {}, "submit", 120)
      } else if (requestId === "submit") {
        if (!hasCompletions()) return root.fail("saving erased cached autocomplete tags")
        service.userTagsLoaded = false
        service.request("tags", { fail: true }, "failed-tags", 85)
      } else if (requestId === "failed-tags") {
        if (service.userTagsError !== "Unable to reach Pinboard.")
          return root.fail("tag loading errors are hidden")
        if (!hasCompletions()) return root.fail("tag refresh failure erased cached autocomplete")
        service.loadUserTags("retried-tags")
      } else if (requestId === "retried-tags") {
        if (!service.userTagsLoaded || service.userTagsError !== "" || !hasCompletions())
          return root.fail("tag retry did not recover")
        console.log("PASS: autocomplete loads before suggestions, survives saves and failures, and recovers on retry")
        Qt.quit()
      }
    }
  }

  function hasCompletions() {
    var matches = Model.autocomplete("ai memory llm github tools utils os", {}, service.userTags)
    return matches.length === 2 && matches[0] === "oss" && matches[1] === "osint"
  }

  function fail(message) {
    console.error("FAIL: " + message)
    Qt.quit()
  }

  Timer {
    interval: 4000
    running: true
    onTriggered: root.fail("stuck waiting for tags")
  }
}
