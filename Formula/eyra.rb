class Eyra < Formula
  desc "Local-first voice coordinator for macOS terminals"
  homepage "https://github.com/gabrimatic/eyra"
  license "PolyForm-Noncommercial-1.0.0"

  # Replace the URL and sha256 when the v4.2.0 release candidate asset exists.
  url "https://github.com/gabrimatic/eyra/archive/refs/tags/v4.2.0rc1.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  head "https://github.com/gabrimatic/eyra.git", branch: "master"

  depends_on "python@3.11"
  depends_on "uv"
  depends_on "ollama" => :recommended

  def install
    libexec.install Dir["*"]
    (bin/"eyra").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run eyra "$@"
    SH
    (bin/"eyra-web").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run eyra web "$@"
    SH
    (bin/"eyra-doctor").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run eyra doctor "$@"
    SH
    (bin/"eyra-certify").write <<~SH
      #!/bin/bash
      cd "#{libexec}" && exec "#{Formula["uv"].opt_bin}/uv" run eyra certify "$@"
    SH
  end

  def caveats
    <<~EOS
      Eyra is local-first and keeps network, OS automation, MCP, Realtime, Web UI,
      and external-agent tools disabled by default.

      First run:
        eyra setup
        eyra doctor

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
    system bin/"eyra", "doctor", "--json"
  end
end
