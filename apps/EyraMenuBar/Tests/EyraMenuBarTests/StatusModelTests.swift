import Foundation
import Testing
@testable import EyraMenuBarCore

@Test func decodesStatusSummary() throws {
    let json = """
    {
      "ok": true,
      "message": "Eyra status\\n\\nLocal model: Ready\\nVoice: Ready\\nLocal-first default: No data leaves your Mac by default",
      "service": {
        "running": true,
        "managed": true,
        "url": "http://127.0.0.1:8765",
        "openUrl": "http://127.0.0.1:8765/?token=redacted",
        "log": "/tmp/eyra.log",
        "message": "running"
      }
    }
    """.data(using: .utf8)!

    let status = try EyraJSON.decodeStatus(json)

    #expect(status.localModelReady)
    #expect(status.voiceSummary == "Ready")
    #expect(status.privacySummary == "Nothing leaves your Mac by default.")
    #expect(status.service?.running == true)
}

@Test func decodesSimpleSettings() throws {
    let json = """
    {
      "ok": true,
      "message": "settings",
      "settings": [
        {
          "key": "LIVE_SPEECH_ENABLED",
          "label": "Speech output",
          "description": "Speak answers aloud.",
          "category": "Voice",
          "value": "true",
          "simple": true,
          "privacy": "local",
          "restart_required": true,
          "secret": false,
          "default": "true",
          "allowed_values": ["true", "false"],
          "value_type": "bool"
        }
      ]
    }
    """.data(using: .utf8)!

    let settings = try EyraJSON.decodeSettings(json)

    #expect(settings.count == 1)
    #expect(settings[0].key == "LIVE_SPEECH_ENABLED")
    #expect(settings[0].value == "true")
}
