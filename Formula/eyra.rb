class Eyra < Formula
  desc "Local-first voice coordinator for macOS terminals"
  homepage "https://github.com/gabrimatic/eyra"

  url "https://github.com/gabrimatic/eyra/releases/download/v4.3.3/eyra-4.3.3.tar.gz"
  sha256 "656668d34ef5b64fd0cac00bdcd5fc8dbb9f77092e1e46458d3df9b128edb2ae"
  version "4.3.3"
  license "PolyForm-Noncommercial-1.0.0"
  head "https://github.com/gabrimatic/eyra.git", branch: "main"

  depends_on "python@3.11"
  depends_on "uv"
  depends_on "ollama" => :recommended

  def install
    libexec.install Dir["*"]
    venv = var/"eyra/venv"
    rm_r venv if venv.exist?
    venv.dirname.mkpath
    system Formula["uv"].opt_bin/"uv", "venv", venv, "--python", Formula["python@3.11"].opt_bin/"python3.11"
    ENV["UV_PROJECT_ENVIRONMENT"] = venv
    cd libexec do
      system Formula["uv"].opt_bin/"uv", "sync", "--frozen", "--no-dev"
      if File.executable?("/usr/bin/swift")
        system "bash", "scripts/build_menu_bar_app.sh"
      end
    end
    if (libexec/"dist/Eyra.app").exist?
      rm_r libexec/"Eyra.app" if (libexec/"Eyra.app").exist?
      cp_r libexec/"dist/Eyra.app", libexec/"Eyra.app"
    end
    (bin/"eyra").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/eyra" "$@"
    SH
    (bin/"eyra-web").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/eyra-web" "$@"
    SH
    (bin/"eyra-doctor").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/eyra-doctor" "$@"
    SH
    (bin/"eyra-certify").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/eyra-certify" "$@"
    SH
    (bin/"eyra-setup").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/eyra-setup" "$@"
    SH
    (bin/"eyra-connectors").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/eyra-connectors" "$@"
    SH
    (bin/"eyra-menu").write <<~SH
      #!/bin/bash
      exec "#{venv}/bin/eyra-menu" "$@"
    SH
  end

  def caveats
    <<~EOS
      Eyra is local-first and keeps network, OS automation, MCP, Realtime, Web UI,
      and external-agent tools disabled by default.

      First run:
        eyra setup
        eyra doctor

      This formula currently installs from the main branch. Switch it to a
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
    test_env = { "USE_MOCK_CLIENT" => "true", "LIVE_LISTENING_ENABLED" => "false", "LIVE_SPEECH_ENABLED" => "false" }
    with_env(test_env) do
      system bin/"eyra", "doctor", "--json"
      system bin/"eyra", "menu", "--json", "--check"
    end
  end
end
