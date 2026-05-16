from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "doc"


def test_mintlify_docs_live_under_doc():
    assert DOC.joinpath("docs.json").is_file()
    assert DOC.joinpath("index.mdx").is_file()
    assert DOC.joinpath("get-started/installation.mdx").is_file()
    assert DOC.joinpath("reference/settings.mdx").is_file()
    assert DOC.joinpath("scripts/prepare-github-pages.mjs").is_file()
    assert not ROOT.joinpath("docs").exists()


def test_readme_points_to_published_docs_not_local_sources():
    readme = ROOT.joinpath("README.md").read_text()
    assert "https://gabrimatic.github.io/eyra/" in readme
    assert "curl -fsSL https://gabrimatic.github.io/eyra/install.sh | bash" in readme
    assert "raw.githubusercontent.com/gabrimatic/eyra/main/install.sh" not in readme
    assert "](doc/" not in readme
    assert "](docs/" not in readme


def test_installation_docs_use_short_pages_installer_url():
    installation = DOC.joinpath("get-started/installation.mdx").read_text()
    assert "curl -fsSL https://gabrimatic.github.io/eyra/install.sh | bash" in installation
    assert "raw.githubusercontent.com/gabrimatic/eyra/main/install.sh" not in installation


def test_docs_pages_workflow_is_scoped_and_static_exported():
    workflow = ROOT.joinpath(".github/workflows/docs-pages.yml").read_text()
    assert "ubuntu-latest" in workflow
    assert '"doc/**"' in workflow
    assert "pull_request:" in workflow
    assert "mint@4.2.565 validate" in workflow
    assert "mint@4.2.565 broken-links" in workflow
    assert "mint@4.2.565 export" in workflow
    assert "prepare-github-pages.mjs _site /eyra" in workflow
    assert "cp install.sh _site/install.sh" in workflow
    assert "actions/upload-pages-artifact@v5" in workflow
    assert "actions/deploy-pages@v5" in workflow
    assert "if: github.event_name == 'push' || github.event_name == 'workflow_dispatch'" in workflow
    assert 'await rm(join(siteDir, "scripts"), { recursive: true, force: true })' in DOC.joinpath(
        "scripts/prepare-github-pages.mjs"
    ).read_text()


def test_full_ci_ignores_docs_only_changes():
    workflow = ROOT.joinpath(".github/workflows/ci.yml").read_text()
    assert 'working-directory: doc' in workflow
    assert '"doc/**"' in workflow
    assert '"README.md"' in workflow
    assert '"docs/**"' not in workflow
