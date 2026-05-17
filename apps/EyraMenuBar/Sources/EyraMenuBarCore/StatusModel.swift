import Foundation

public struct EyraSetting: Codable, Identifiable, Equatable {
    public var id: String { key }
    public let key: String
    public let label: String
    public let description: String
    public let category: String
    public let value: String
    public let simple: Bool
    public let privacy: String
    public let restartRequired: Bool
    public let secret: Bool

    enum CodingKeys: String, CodingKey {
        case key, label, description, category, value, simple, privacy, secret
        case restartRequired = "restart_required"
    }
}

public struct EyraServiceStatus: Codable, Equatable {
    public let running: Bool
    public let managed: Bool
    public let url: String
    public let openUrl: String?
    public let log: String?
    public let message: String?
}

public struct EyraStatus: Codable, Equatable {
    public let ok: Bool
    public let message: String
    public let service: EyraServiceStatus?

    public var localModelReady: Bool {
        message.contains("Local model: Ready")
    }

    public var voiceSummary: String {
        for line in message.components(separatedBy: .newlines) {
            if line.hasPrefix("Voice:") {
                return line.replacingOccurrences(of: "Voice:", with: "").trimmingCharacters(in: .whitespaces)
            }
        }
        return "Unknown"
    }

    public var privacySummary: String {
        if message.contains("No data leaves your Mac by default") {
            return "Nothing leaves your Mac by default."
        }
        return "Review enabled remote or network settings."
    }
}

public enum EyraJSON {
    public static func decodeStatus(_ data: Data) throws -> EyraStatus {
        try JSONDecoder().decode(EyraStatus.self, from: data)
    }

    public static func decodeSettings(_ data: Data) throws -> [EyraSetting] {
        struct Payload: Decodable {
            let settings: [EyraSetting]
        }
        return try JSONDecoder().decode(Payload.self, from: data).settings
    }
}
