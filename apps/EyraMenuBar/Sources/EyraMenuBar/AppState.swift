import Foundation
import AppKit
import Combine
import EyraMenuBarCore

@MainActor
final class AppState: ObservableObject {
    @Published var statusText = "Checking Eyra..."
    @Published var modelReady = false
    @Published var voiceSummary = "Unknown"
    @Published var privacySummary = "Checking local-first status..."
    @Published var serviceRunning = false
    @Published var serviceURL = ""
    @Published var settings: [EyraSetting] = []
    @Published var lastOutput = ""
    @Published var isBusy = false

    private let cli = EyraCLI.shared

    func refresh() {
        Task {
            await loadStatus()
            await loadSettings()
        }
    }

    func loadStatus() async {
        isBusy = true
        defer { isBusy = false }
        let result = await cli.run(["status", "--json"], timeout: 30)
        lastOutput = result.output
        guard let data = result.output.data(using: .utf8),
              let decoded = try? EyraJSON.decodeStatus(data) else {
            statusText = "Eyra status needs attention."
            modelReady = false
            serviceRunning = false
            return
        }
        modelReady = decoded.localModelReady
        voiceSummary = decoded.voiceSummary
        privacySummary = decoded.privacySummary
        serviceRunning = decoded.service?.running ?? false
        serviceURL = decoded.service?.openUrl ?? decoded.service?.url ?? ""
        statusText = serviceRunning ? "Eyra control service is running." : "Eyra control service is stopped."
    }

    func loadSettings() async {
        let result = await cli.run(["settings", "--json"], timeout: 10)
        guard let data = result.output.data(using: .utf8),
              let decoded = try? EyraJSON.decodeSettings(data) else {
            return
        }
        settings = decoded.filter(\.simple)
    }

    func startService() {
        runAndRefresh(["start"])
    }

    func stopService() {
        runAndRefresh(["stop"])
    }

    func restartService() {
        runAndRefresh(["restart"])
    }

    func runDoctor() {
        runAndRefresh(["doctor"])
    }

    func runSetupInTerminal() {
        runTerminalCommand("eyra setup")
    }

    func startTerminalSession() {
        runTerminalCommand("eyra")
    }

    func openWebUI() {
        runAndRefresh(["open"])
    }

    func openLogs() {
        runAndRefresh(["logs", "--open"])
    }

    func openMemoryFolder() {
        runAndRefresh(["memory", "path"])
    }

    func showMemory() {
        runAndRefresh(["memory", "show"])
    }

    func reloadMemory() {
        runAndRefresh(["memory", "reload"])
    }

    func openAgentsFile() {
        openConfiguredPath("AGENTS_FILE")
    }

    func openPersonalityFile() {
        openConfiguredPath("PERSONALITY_FILE")
    }

    func openDocs() {
        cli.openDocs()
    }

    func openSettingsWindow() {
        NSApplication.shared.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func set(_ key: String, value: String) {
        runAndRefresh(["settings", "set", key, value])
    }

    private func runAndRefresh(_ args: [String]) {
        Task {
            isBusy = true
            let result = await cli.run(args, timeout: 45)
            lastOutput = result.output
            isBusy = false
            await loadStatus()
            await loadSettings()
        }
    }

    private func runTerminalCommand(_ command: String) {
        let script = "tell application \"Terminal\" to do script \"\(command.replacingOccurrences(of: "\"", with: "\\\""))\""
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script]
        try? process.run()
    }

    private func openConfiguredPath(_ key: String) {
        guard let raw = settings.first(where: { $0.key == key })?.value, !raw.isEmpty else {
            return
        }
        let path = (raw as NSString).expandingTildeInPath
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }
}
