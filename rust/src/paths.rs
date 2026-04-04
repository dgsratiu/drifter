use anyhow::{bail, Result};
use std::path::{Path, PathBuf};

pub fn resolve_project_root() -> Result<PathBuf> {
    let cwd = std::env::current_dir()?;

    for candidate in cwd.ancestors() {
        if is_project_root(candidate) {
            return Ok(candidate.to_path_buf());
        }
    }

    bail!("could not locate project root from {}", cwd.display())
}

pub fn resolve_db_path(input: &str, project_root: &Path) -> PathBuf {
    let path = Path::new(input);
    if path.is_absolute() {
        return path.to_path_buf();
    }

    if input == "./drifter.db" || input == "drifter.db" {
        return project_root.join("drifter.db");
    }

    std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(path)
}

fn is_project_root(path: &Path) -> bool {
    path.join("rust").join("Cargo.toml").is_file()
        && path.join("docs").join("prd.md").is_file()
        && path.join("schema.sql").is_file()
}
