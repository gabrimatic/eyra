import SwiftUI
import AppKit

struct MenuBarContentView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        Text(appState.statusText)
            .font(.headline)
        Text(appState.privacySummary)
            .foregroundStyle(.secondary)

        Divider()

        Label(appState.modelReady ? "Local model ready" : "Local model needs setup", systemImage: appState.modelReady ? "checkmark.circle" : "exclamationmark.triangle")
        Label("Voice: \(appState.voiceSummary)", systemImage: "waveform")
        Label(appState.serviceRunning ? "Web control running" : "Web control stopped", systemImage: appState.serviceRunning ? "play.circle" : "pause.circle")

        Divider()

        Button(appState.serviceRunning ? "Open Web UI" : "Start Eyra Control") {
            appState.openWebUI()
        }
        .keyboardShortcut("o", modifiers: .command)

        Button("Open Terminal Session") {
            appState.startTerminalSession()
        }

        Button("Start Control Service") {
            appState.startService()
        }
        .disabled(appState.serviceRunning)

        Button("Stop Control Service") {
            appState.stopService()
        }
        .disabled(!appState.serviceRunning)

        Button("Restart Control Service") {
            appState.restartService()
        }

        Divider()

        Menu("Voice") {
            SettingToggle(key: "LIVE_LISTENING_ENABLED", title: "Voice input")
            SettingToggle(key: "LIVE_SPEECH_ENABLED", title: "Speech output")
            Button("Run Doctor") {
                appState.runDoctor()
            }
            Button("Open setup in Terminal") {
                appState.runSetupInTerminal()
            }
        }

        Menu("Privacy and tools") {
            SettingToggle(key: "NETWORK_TOOLS_ENABLED", title: "Network tools")
            SettingToggle(key: "OS_TOOLS_ENABLED", title: "Mac control tools")
            SettingToggle(key: "CONNECTORS_ENABLED", title: "Connectors")
            SettingToggle(key: "REALTIME_VOICE_ENABLED", title: "Realtime voice")
            Text("Advanced tools stay off until you enable them.")
                .foregroundStyle(.secondary)
        }

        Button("Settings...") {
            appState.openSettingsWindow()
        }
        .keyboardShortcut(",", modifiers: .command)

        Divider()

        Button("Refresh") {
            appState.refresh()
        }
        .keyboardShortcut("r", modifiers: .command)

        Button("Open logs") {
            appState.openLogs()
        }

        Button("Open docs") {
            appState.openDocs()
        }

        Divider()

        Button("Quit Eyra Menu Bar") {
            NSApplication.shared.terminate(nil)
        }
        .keyboardShortcut("q", modifiers: .command)
        .onAppear {
            appState.refresh()
        }
    }
}

struct SettingToggle: View {
    @EnvironmentObject private var appState: AppState
    let key: String
    let title: String

    private var current: Bool {
        appState.settings.first(where: { $0.key == key })?.value == "true"
    }

    var body: some View {
        Toggle(title, isOn: Binding(
            get: { current },
            set: { appState.set(key, value: $0 ? "true" : "false") }
        ))
    }
}

struct EyraSettingsView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Eyra Settings")
                .font(.title2.bold())
            Text("Simple settings are safe front-door controls. Advanced features remain available, but off by default.")
                .foregroundStyle(.secondary)

            List(appState.settings) { setting in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(setting.label)
                            .font(.headline)
                        Spacer()
                        Text(setting.value)
                            .foregroundStyle(.secondary)
                    }
                    Text(setting.description)
                        .foregroundStyle(.secondary)
                    Text(setting.privacy)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }

            HStack {
                Button("Refresh") {
                    appState.refresh()
                }
                Button("Open setup in Terminal") {
                    appState.runSetupInTerminal()
                }
                Button("Run Doctor") {
                    appState.runDoctor()
                }
            }
        }
        .padding(24)
        .frame(minWidth: 720, minHeight: 520)
        .onAppear {
            appState.refresh()
        }
    }
}
