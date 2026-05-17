import SwiftUI

@main
struct EyraMenuBarApp: App {
    @StateObject private var appState = AppState()

    var body: some Scene {
        MenuBarExtra {
            MenuBarContentView()
                .environmentObject(appState)
        } label: {
            Image(systemName: appState.serviceRunning ? "waveform.circle.fill" : "waveform.circle")
        }
        .menuBarExtraStyle(.menu)

        Settings {
            EyraSettingsView()
                .environmentObject(appState)
        }
        .defaultSize(width: 720, height: 520)
    }
}
