class Eyra < Formula
  desc "Local-first voice coordinator for macOS terminals"
  homepage "https://github.com/gabrimatic/eyra"
  license "PolyForm-Noncommercial-1.0.0"

  # Private beta formula: tracks the unreleased release-candidate branch until a
  # signed/tagged release asset exists.
  url "https://github.com/gabrimatic/eyra.git", branch: "master"
  version "4.2.0rc1"
  head "https://github.com/gabrimatic/eyra.git", branch: "master"

  depends_on "python@3.11"
  depends_on "uv"
  depends_on "ollama" => :recommended

  def install
    libexec.install Dir["*"]
    system Formula["uv"].opt_bin/"uv", "sync", "--frozen", "--no-dev", chdir: libexec
    (bin/"eyra").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run --frozen --no-sync eyra "$@"
    SH
    (bin/"eyra-web").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run --frozen --no-sync eyra web "$@"
    SH
    (bin/"eyra-doctor").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run --frozen --no-sync eyra doctor "$@"
    SH
    (bin/"eyra-certify").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run --frozen --no-sync eyra certify "$@"
    SH
    (bin/"eyra-setup").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run --frozen --no-sync eyra setup "$@"
    SH
    (bin/"eyra-connectors").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run --frozen --no-sync eyra connectors "$@"
    SH
  end

  def caveats
    <<~EOS
      Eyra is local-first and keeps network, OS automation, MCP, Realtime, Web UI,
      and external-agent tools disabled by default.

      First run:
        eyra setup
        eyra doctor

      This private beta formula installs from the master branch. Switch it to a
      tagged release asset and sha256 before using it as a stable public tap.

      Voice requires Local Whisper:
        brew tap gabrimatic/local-whisper
        brew install local-whisper

      Grant microphone and screen recording permissions only if you want voice input
      and screen analysis. Eyra preserves .env, jobs, triggers, logs, and the
      operation ledger across updates.
    EOS
  end

  test do
    system bin/"eyra", "version"
    system bin/"eyra", "paths", "--json"
    system({ "USE_MOCK_CLIENT" => "true", "LIVE_LISTENING_ENABLED" => "false", "LIVE_SPEECH_ENABLED" => "false" },
           bin/"eyra", "doctor", "--json")
  end
end
