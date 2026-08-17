import QtQuick
import qs.Commons as C
import qs.Ui as Ui

Ui.BarWidget {
  id: root
  moduleName: "io.github.filipechagas.omapin"

  readonly property var pinboardService: bar && bar.shell
    ? bar.shell.serviceFor("io.github.filipechagas.omapin")
    : null
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true
    : false

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("host" in target) target.host = root
    if ("service" in target) target.service = root.pinboardService
  }

  function open() {
    if (panelLoader.item && panelLoader.item.open) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  function toggle() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item && panelLoader.item.closeForPopoutSwitch)
      panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()
  onPinboardServiceChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\uf02e"
    active: root.opened
    tooltipText: {
      if (!root.pinboardService) return "Omapin"
      if (root.pinboardService.queueFailed > 0) return "Omapin - queued bookmark needs attention"
      if (root.pinboardService.queuePending > 0) return "Omapin - bookmark queued"
      return "Omapin"
    }
    onPressed: root.toggle()
  }

  Rectangle {
    visible: root.pinboardService
      && (root.pinboardService.queuePending > 0 || root.pinboardService.queueFailed > 0)
    width: C.Style.space(5)
    height: width
    radius: width / 2
    color: root.pinboardService && root.pinboardService.queueFailed > 0
      ? C.Color.urgent
      : C.Color.accent
    anchors.right: button.right
    anchors.top: button.top
    anchors.rightMargin: C.Style.space(2)
    anchors.topMargin: C.Style.space(2)
  }
}
