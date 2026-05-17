import Foundation
import AppKit

struct CLIResult: Sendable {
    let exitCode: Int32
    let output: String
}

final class EyraCLI: Sendable {
    static let shared = EyraCLI()

    var executable: String {
        if let override = ProcessInfo.processInfo.environment["EYRA_CLI_PATH"], !override.isEmpty {
            return override
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        for candidate in [
            "\(home)/.local/bin/eyra",
            "/opt/homebrew/bin/eyra",
            "/usr/local/bin/eyra"
        ] where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return "eyra"
    }

    func run(_ arguments: [String], timeout: TimeInterval = 20) async -> CLIResult {
        let executable = self.executable
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
                process.arguments = [executable] + arguments
                let pipe = Pipe()
                process.standardOutput = pipe
                process.standardError = pipe

                do {
                    try process.run()
                } catch {
                    continuation.resume(returning: CLIResult(exitCode: 127, output: "Could not run eyra: \(error.localizedDescription)"))
                    return
                }

                let deadline = Date().addingTimeInterval(timeout)
                while process.isRunning && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.1)
                }
                if process.isRunning {
                    process.terminate()
                }
                process.waitUntilExit()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                let output = String(data: data, encoding: .utf8) ?? ""
                continuation.resume(returning: CLIResult(exitCode: process.terminationStatus, output: output))
            }
        }
    }

    @MainActor
    func openDocs() {
        NSWorkspace.shared.open(URL(string: "https://gabrimatic.github.io/eyra/")!)
    }
}
