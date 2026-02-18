import tarfile
from pathlib import Path

from infra_visualiser_action import client


def test_create_archive_includes_matching_files_from_recipe_dir(tmp_path: Path):
    """Test that create_archive includes *.tf, *.json, *.dot files from recipe_dir"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe" / "nested"
    recipe_dir.mkdir(parents=True)
    
    # Create matching files
    (recipe_dir / "main.tf").write_text("terraform content")
    (recipe_dir / "variables.tf").write_text("variables content")
    (recipe_dir / "config.json").write_text('{"key": "value"}')
    (recipe_dir / "graph.dot").write_text("digraph G {}")
    
    # Create non-matching file (should be excluded when include_markdown=False)
    (recipe_dir / "README.md").write_text("readme content")
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    result = client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
    )
    
    assert result == archive_path
    assert archive_path.exists()
    
    # Verify archive contents
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])
    
    expected_files = set([
        "recipe/nested/main.tf",
        "recipe/nested/variables.tf",
        "recipe/nested/config.json",
        "recipe/nested/graph.dot",
    ])
    assert members.issuperset(expected_files), members.difference(expected_files)
    assert "recipe/nested/README.md" not in members


def test_create_archive_includes_markdown_when_enabled(tmp_path: Path):
    """When include_markdown=True, *.md files at the repository root are included in the archive."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe" / "nested"
    recipe_dir.mkdir(parents=True)
    
    (recipe_dir / "main.tf").write_text("terraform content")
    (repo_root / "README.md").write_text("readme content")
    (repo_root / "docs.md").write_text("docs content")
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        include_markdown=True,
    )
    
    assert archive_path.exists()
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])
    
    expected = {"recipe/nested/main.tf", "README.md", "docs.md"}
    assert members == expected


def test_create_archive_include_markdown_adds_markdown_from_repo_root_and_subdirs(tmp_path: Path):
    """With include_markdown=True, only *.md at the repository root are added; .md in subdirs are not."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "main.tf").write_text("content")
    (recipe_dir / "README.md").write_text("readme in recipe")
    
    (repo_root / "ROOT.md").write_text("root readme")
    
    extra_dir = repo_root / "modules" / "local"
    extra_dir.mkdir(parents=True)
    (extra_dir / "main.tf").write_text("module content")
    (extra_dir / "NOTES.md").write_text("notes")
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        extra_paths=[extra_dir],
        include_markdown=True,
    )
    
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])
    
    assert "recipe/main.tf" in members
    assert "modules/local/main.tf" in members
    assert "ROOT.md" in members
    assert "recipe/README.md" in members
    assert "modules/local/NOTES.md" in members


def test_create_archive_include_markdown_skips_vendor_dirs(tmp_path: Path):
    """With include_markdown=True, *.md under vendor/node_modules/etc. are excluded."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recipe_dir = repo_root / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "main.tf").write_text("content")
    (recipe_dir / "README.md").write_text("recipe readme")

    (repo_root / "vendor" / "pkg" / "README.md").parent.mkdir(parents=True)
    (repo_root / "vendor" / "pkg" / "README.md").write_text("go vendor readme")
    (repo_root / "node_modules" / "some-pkg" / "CHANGELOG.md").parent.mkdir(parents=True)
    (repo_root / "node_modules" / "some-pkg" / "CHANGELOG.md").write_text("npm readme")

    archive_path = tmp_path / "output" / "archive.tar.gz"
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        include_markdown=True,
    )
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])

    assert "recipe/main.tf" in members
    assert "recipe/README.md" in members
    assert "vendor/pkg/README.md" not in members
    assert "node_modules/some-pkg/CHANGELOG.md" not in members


def test_create_archive_includes_extra_paths_as_files(tmp_path: Path):
    """Test that extra_paths files are included in archive"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "main.tf").write_text("content")
    
    extra_file = repo_root / "extra" / "file.json"
    extra_file.parent.mkdir()
    extra_file.write_text('{"extra": "data"}')
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        extra_paths=[extra_file],
    )
    
    with tarfile.open(archive_path, "r:gz") as tar:
        members =   set([member.name for member in tar.getmembers()])
    
    assert set(["recipe/main.tf", "extra/file.json"]) == set(members)


def test_create_archive_only_includes_files_in_extra_paths_directories(tmp_path: Path):
    """Test that extra_paths directories include *.tf files but exclude subdirectories"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "main.tf").write_text("content")
    
    # Create extra directory with nested .tf files
    extra_dir = repo_root / "modules" / "local-module"
    extra_dir.mkdir(parents=True)
    (extra_dir / "main.tf").write_text("module content")
    (extra_dir / "variables.tf").write_text("module vars")
    (extra_dir / "nested").mkdir()
    (extra_dir / "nested" / "sub.tf").write_text("nested content")
    
    # Non-matching file in extra_dir (should not be included)
    (extra_dir / "README.md").write_text("readme")
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        extra_paths=[extra_dir],
    )
    
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])
    
    expected_files = {
        "recipe/main.tf",
        "modules/local-module/main.tf",
        "modules/local-module/variables.tf",
    }
    
    assert members == expected_files
    assert "modules/local-module/nested/sub.tf" not in members


def test_create_archive_skips_nonexistent_extra_paths(tmp_path: Path):
    """Test that non-existent extra_paths are skipped"""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    recipe_dir = repo_root / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "main.tf").write_text("content")
    
    nonexistent = repo_root / "nonexistent" / "file.tf"
    existing = repo_root / "existing.tf"
    existing.write_text("existing content")
    
    archive_path = tmp_path / "output" / "archive.tar.gz"
    
    client.create_archive(
        repo_root=repo_root,
        recipe_dir=recipe_dir,
        archive_path=archive_path,
        extra_paths=[nonexistent, existing],
    )
    
    with tarfile.open(archive_path, "r:gz") as tar:
        members = set([member.name for member in tar.getmembers()])
    
    assert set(["recipe/main.tf", "existing.tf"]) == members
